#슬롯 추출 엔드포인트
import asyncio
import json

from fastapi import Request
from google.genai.errors import APIError
from pydantic import ValidationError

from app.api.router import router
from app.core.config import settings
from app.core.exceptions import LLMInvalidResponseError, LLMRateLimitedError, LLMTimeoutError, MapAPIError
from app.core.logging import logger
from app.integrations.map_client import geocode, reverse_geocode
from app.models.factory import get_llm
from app.prompts.loader import load_prompt
from app.schemas.common import Coordinate
from app.schemas.extract import ExtractData, ExtractDataLLM, ExtractRequest, ExtractResponse, Slots
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

@router.post("/v1/extract")
async def extract(request: ExtractRequest, http_request: Request = None) -> ExtractResponse:
    # 백엔드가 보낸 원본(raw)과 파싱 후 값(used)을 같이 남긴다. Slots 는 모르는 키를
    # 조용히 버려서, 키 이름이 다르면 used 만으로는 "전부 null"로만 보인다.
    raw = (await http_request.json()).get("prev_slot") if http_request else None
    logger.info("extract prev_slot raw=%s used=%s prev_query=%r",
                raw, request.prev_slot.model_dump_json(), request.prev_query)

    # 모델은 좌표를 읽고 쓸 수 없어서, 이전 출발지는 주소로 바꿔 보여 준다.
    prev_origin_text = await _origin_text(request.prev_slot.origin)
    prev_for_prompt = request.prev_slot.model_dump(mode="json")
    prev_for_prompt["origin"] = prev_origin_text

    prompt = load_prompt(
        "extract",
        chat=request.chat,
        date=str(request.date),
        prev_slot=json.dumps(prev_for_prompt, ensure_ascii=False),
        prev_query=request.prev_query or "없음",
    )

    llm = get_llm().with_structured_output(ExtractDataLLM)

    try:
        out = await asyncio.wait_for(llm.ainvoke(prompt), timeout=settings.TIMEOUT_EXTRACT)
    except asyncio.TimeoutError:
        raise LLMTimeoutError(detail="슬롯 추출 모델 호출이 제한 시간 내에 끝나지 않았습니다.")
    except APIError as e:
        if e.code == 429:
            raise LLMRateLimitedError(detail=str(e))
        raise LLMInvalidResponseError(detail=str(e))
    except ValidationError as e:
        raise LLMInvalidResponseError(detail=str(e))

    # 모델이 말한 출발지 이름을 좌표로 바꾼다. 이전 출발지를 그대로 다시 적어 왔으면
    # (주소 문자열) 다시 찾지 않고 이전 좌표를 쓴다 — 그 문자열은 건물 이름이 붙어 검색이 안 될 수 있다.
    said = out.slot.origin
    new_origin = None
    if said and said != prev_origin_text:
        new_origin = await _origin_coordinate(said)
    new_slot = Slots(**out.slot.model_dump(exclude={"origin"}), origin=new_origin)

    slot = merge_slots(request.prev_slot, new_slot)
    if said and said != prev_origin_text and new_origin is None:
        # 사용자가 출발지를 바꿨는데 좌표를 못 찾았다. 이전 좌표를 남기면 엉뚱한 출발지가 되므로 비운다.
        slot.origin = None

    data = ExtractData(slot=slot, query=out.query, bot_message=out.bot_message)
    return ExtractResponse(message="extract_success", data=data)
