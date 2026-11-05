"""슬롯 추출 엔드포인트. 라우팅, 요청 원본 로그, 응답 조립만 맡는다."""

from fastapi import Request

from app.api.router import router
from app.core.logging import logger
from app.schemas.extract import ExtractRequest, ExtractResponse
from app.services.extract import extract as run_extract


@router.post("/v1/extract")
async def extract(request: ExtractRequest, http_request: Request = None) -> ExtractResponse:
    # 백엔드가 보낸 원본(raw)과 파싱 후 값(used)을 같이 남긴다. Slots 는 모르는 키를
    # 조용히 버려서, 키 이름이 다르면 used 만으로는 "전부 null"로만 보인다.
    raw = (await http_request.json()).get("prev_slot") if http_request else None
    logger.info("extract prev_slot raw=%s used=%s prev_query=%r",
                raw, request.prev_slot.model_dump_json(), request.prev_query)

    data = await run_extract(request)
    return ExtractResponse(message="extract_success", data=data)
