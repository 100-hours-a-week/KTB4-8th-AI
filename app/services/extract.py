"""챗봇 슬롯 추출 서비스."""

import asyncio
import json

from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import LLMInvalidResponseError, LLMTimeoutError, MapAPIError
from app.core.logging import logger
from app.integrations.map_client import geocode, reverse_geocode
from app.models.factory import get_gpu_llm, get_llm, invoke_gpu_first, to_domain_error
from app.prompts.loader import load_prompt
from app.schemas.common import Coordinate
from app.schemas.extract import ExtractData, ExtractDataLLM, ExtractRequest, Slots
from app.services.slot_datetime import resolve_visit, with_weekday
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


async def extract(request: ExtractRequest) -> ExtractData:
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
        prev_slot=json.dumps(prev_for_prompt, ensure_ascii=False),
        prev_query=request.prev_query or "없음",
    )

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
        bot_message = f"이번 주 {out.slot.weekday}요일은 이미 지났어요. 언제 가실 예정이에요?"

    return ExtractData(slot=slot, query=out.query, bot_message=bot_message)
