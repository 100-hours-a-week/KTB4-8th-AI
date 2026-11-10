"""챗봇 슬롯 추출 서비스."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Literal

from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import LLMInvalidResponseError, LLMTimeoutError, MapAPIError
from app.core.logging import logger
from app.integrations.map_client import geocode, reverse_geocode
from app.models.factory import astream_gpu_first, get_gpu_llm, get_llm, invoke_gpu_first, to_domain_error
from app.prompts.loader import load_prompt
from app.schemas.common import Coordinate
from app.schemas.extract import ExtractData, ExtractDataLLM, ExtractDataStreamLLM, ExtractRequest, Slots, SlotsLLM
from app.services.slot_datetime import resolve_visit, this_week_status, with_weekday
from app.services.slot_merge import merge_slots


# 출발지 좌표 ↔ 이름 변환. 지도 API 가 실패해도 대화는 계속한다 — 채팅 응답 하나 때문에
# 지도 장애를 502 로 올리는 건 과하다(장소 검증은 저장될 데이터라 502 로 올림).
async def _origin_text(origin: Coordinate | None) -> str | None:
    if origin is None:
        return None
    try:
        return await reverse_geocode(origin.lat, origin.lng)
    except MapAPIError as exc:
        logger.warning("extract 출발지 좌표 → 주소 실패, 출발지 없이 진행: %r", exc)
        return None


async def _origin_coordinate(name: str) -> Coordinate | None:
    try:
        found = await geocode(name)
    except MapAPIError as exc:
        logger.warning("extract 출발지 %r → 좌표 실패(지도 API): %r", name, exc)
        return None
    if found is None:
        # 주소 검색이라 "서울역" 같은 장소명은 못 찾는다(10/21 실측) — r3 시험에서 이 비율을 본다
        logger.warning("extract 출발지 %r → 좌표 못 찾음", name)
        return None
    return Coordinate(lat=found[0], lng=found[1])


async def _build_prompt(request: ExtractRequest):
    """프롬프트와, 프롬프트에 보여 준 이전 출발지 주소(후처리에서 비교용)."""
    # 모델은 좌표를 읽고 쓸 수 없어서, 이전 출발지는 주소로 바꿔 보여 준다.
    prev_origin_text = await _origin_text(request.prev_slot.origin)
    prev_for_prompt = request.prev_slot.model_dump(mode="json")
    prev_for_prompt["origin"] = prev_origin_text
    # 날짜에 요일을 붙여 보여 준다. bot_message 에 "토요일" 같은 말을 쓰려면 요일이 필요한데,
    # 모델이 요일을 계산하면 틀린다(10/28 Gemma 실측).
    if request.prev_slot.datetime is not None:
        prev_for_prompt["datetime"] = with_weekday(request.prev_slot.datetime)

    prompt = load_prompt(
        "extract",
        chat=request.chat,
        date=with_weekday(request.date),
        this_week_status=this_week_status(request.date),
        prev_slot=json.dumps(prev_for_prompt, ensure_ascii=False),
        prev_query=request.prev_query or "없음",
    )
    return prompt, prev_origin_text


def _passed_message(slot: SlotsLLM) -> str:
    return f"이번 주 {slot.weekday}요일은 이미 지났어요. 언제 가실 예정이에요?"


async def extract(request: ExtractRequest) -> ExtractData:
    prompt, prev_origin_text = await _build_prompt(request)

    gpu = get_gpu_llm()
    try:
        out, model = await invoke_gpu_first(
            "extract",
            prompt,
            gpu.with_structured_output(ExtractDataLLM, method="json_schema") if gpu else None,
            get_llm().with_structured_output(ExtractDataLLM),
            total=settings.TIMEOUT_EXTRACT,
            gpu_timeout=settings.TIMEOUT_EXTRACT_GPU,
        )
        logger.info("extract model=%s", model)
    except asyncio.TimeoutError:
        raise LLMTimeoutError(detail="슬롯 추출 모델 호출이 제한 시간 내에 끝나지 않았습니다.")
    except ValidationError as e:
        raise LLMInvalidResponseError(detail=str(e))
    except Exception as e:
        # 랭체인이 Gemini 429 를 google APIError 가 아닌 ModelRateLimitError 로 바꿔 던진다
        # (langchain-google-genai 4.4.0). APIError 로 잡던 시절엔 429 가 500 으로 나갔다.
        raise to_domain_error(e) from e

    return await _finish(request, out, prev_origin_text)


async def _finish(
    request: ExtractRequest, out: ExtractDataLLM, prev_origin_text: str | None, replace_passed_message: bool = True
) -> ExtractData:
    """모델 출력을 응답으로 바꾼다(출발지 좌표, 방문 일시, 이전 슬롯과 합치기, 지난 요일).

    replace_passed_message: 지난 요일일 때 문구를 코드 문구로 바꿀지. 스트림은 모델 문구를 이미
    화면에 보냈으므로 바꾸지 않는다(화면과 저장이 같아야 해서) — 대신 프롬프트 규칙 4-5 가 모델에게
    지났다고 말하게 한다. 날짜를 비우는 건 둘 다 한다.
    """
    # 모델이 말한 출발지 이름을 좌표로 바꾼다. 이전 출발지를 그대로 다시 적어 왔으면
    # (주소 문자열) 다시 찾지 않고 이전 좌표를 쓴다 — 그 문자열은 건물 이름이 붙어 검색이 안 될 수 있다.
    said = out.slot.origin
    new_origin = None
    if said and said != prev_origin_text:
        new_origin = await _origin_coordinate(said)
    visit, passed = resolve_visit(out.slot, request.date, request.prev_slot.datetime, request.prev_query)
    new_slot = Slots(
        **out.slot.model_dump(include={"region", "available_time", "category"}),
        origin=new_origin,
        datetime=visit,
    )

    slot = merge_slots(request.prev_slot, new_slot)
    if said and said != prev_origin_text and new_origin is None:
        # 사용자가 출발지를 바꿨는데 좌표를 못 찾았다. 이전 좌표를 남기면 엉뚱한 출발지가 되므로 비운다.
        slot.origin = None

    bot_message = out.bot_message
    if passed:
        # 이미 지난 "이번 주 X"(10/28 결정). 이전 날짜를 남기면 사용자가 바꾼 뜻이 사라지므로
        # 비우고, 모델 문장은 그 날짜로 진행하는 말일 수 있어 코드가 다시 묻는다.
        slot.datetime = None
        if replace_passed_message:
            bot_message = _passed_message(out.slot)

    return ExtractData(slot=slot, query=out.query, bot_message=bot_message)


# ── 스트리밍 (/v3/extract/stream) ──────────────────────────────────────

ExtractStreamItem = tuple[Literal["delta"], str] | tuple[Literal["result"], ExtractData]

_MESSAGE_KEY = re.compile(r'"bot_message"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class _MessageReader:
    """생성 중인 JSON 에서 bot_message 문자열 값만 골라, 새로 확정된 글자를 돌려준다.

    JSON 문자열의 이스케이프(\\n, \\", \\uXXXX)를 풀어서 낸다. 이스케이프가 조각 경계에서
    잘리면 다음 조각이 올 때까지 기다린다.
    """

    def __init__(self) -> None:
        self.start: int | None = None  # 값의 첫 글자 위치(여는 따옴표 다음)
        self.pos = 0
        self.done = False

    def find(self, buf: str) -> bool:
        if self.start is None:
            m = _MESSAGE_KEY.search(buf)
            if m:
                self.start = self.pos = m.end()
        return self.start is not None

    def read(self, buf: str) -> str:
        out = []
        while not self.done and self.pos < len(buf):
            ch = buf[self.pos]
            if ch == '"':
                self.done = True
                break
            if ch != "\\":
                out.append(ch)
                self.pos += 1
                continue
            if self.pos + 1 >= len(buf):
                break  # 이스케이프가 잘렸다 — 다음 조각을 기다린다
            kind = buf[self.pos + 1]
            if kind == "u":
                if self.pos + 6 > len(buf):
                    break
                code = int(buf[self.pos + 2:self.pos + 6], 16)
                if 0xD800 <= code < 0xDC00:  # 서로게이트 쌍의 앞쪽 — 뒤쪽까지 있어야 푼다
                    if self.pos + 12 > len(buf):
                        break
                    low = int(buf[self.pos + 8:self.pos + 12], 16)
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    self.pos += 12
                else:
                    out.append(chr(code))
                    self.pos += 6
            else:
                out.append(_ESCAPES.get(kind, kind))
                self.pos += 2
        return "".join(out)


async def extract_stream(request: ExtractRequest) -> AsyncIterator[ExtractStreamItem]:
    """bot_message 를 생기는 대로 ("delta", 글자들)로 내고, 끝나면 ("result", 응답)을 낸다.

    한 번 호출로 bot_message → slot → query 순서의 JSON(ExtractDataStreamLLM)을 받으면서
    bot_message 부분만 실시간으로 보낸다(11/10 결정 1 B). 결과(done)는 /v1 과 같은 후처리를 거친다.
    - 지난 요일(11/10 결정 c): 문구가 슬롯보다 먼저 나가서 코드가 문구를 바꿀 수 없다. 프롬프트
      규칙 4-5 로 모델이 "이미 지났어요"라고 말하게 하고, done 문구는 모델 문구 그대로 둔다
      (화면과 저장이 같게). 날짜를 비우는 건 코드가 한다.
    - 모델이 순서를 지키지 않아도 bot_message 가 시작되면 그때부터 보낸다. 끝까지 못 찾으면
      끝난 뒤 done 과 같은 문구를 한 번에 보낸다.
    - GPU→Gemini 대체는 첫 글자 전까지만(결정 3a). 실패는 우리 예외(AIServerError)로 올린다.
    """
    prompt, prev_origin_text = await _build_prompt(request)
    gpu = get_gpu_llm()
    reader = _MessageReader()
    sent = False
    buf = ""
    try:
        stream = astream_gpu_first(
            "extract_stream",
            prompt,
            gpu.bind(response_format=ExtractDataStreamLLM) if gpu else None,
            get_llm().bind(response_mime_type="application/json",
                           response_json_schema=ExtractDataStreamLLM.model_json_schema()),
            total=settings.TIMEOUT_EXTRACT,
            gpu_first_timeout=settings.TIMEOUT_EXTRACT_GPU,
        )
        async with aclosing(stream) as texts:
            async for text in texts:
                buf += text
                if reader.find(buf):
                    piece = reader.read(buf)
                    if piece:
                        yield "delta", piece
                        sent = True
        parsed = ExtractDataStreamLLM.model_validate_json(buf)
    except asyncio.TimeoutError:
        raise LLMTimeoutError(detail="슬롯 추출 모델 호출이 제한 시간 내에 끝나지 않았습니다.")
    except ValidationError as e:
        raise LLMInvalidResponseError(detail=str(e))
    except Exception as e:
        raise to_domain_error(e) from e

    out = ExtractDataLLM(slot=parsed.slot, query=parsed.query, bot_message=parsed.bot_message)
    data = await _finish(request, out, prev_origin_text, replace_passed_message=False)
    if not sent:
        yield "delta", data.bot_message
    yield "result", data
