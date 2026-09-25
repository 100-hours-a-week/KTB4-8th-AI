#슬롯 추출 엔드포인트
import asyncio

from google.genai.errors import APIError
from pydantic import ValidationError

from app.api.router import router
from app.core.config import settings
from app.core.exceptions import LLMInvalidResponseError, LLMRateLimitedError, LLMTimeoutError
from app.models.factory import get_llm
from app.prompts.loader import load_prompt
from app.schemas.extract import ExtractData, ExtractRequest, ExtractResponse
from app.services.slot_merge import merge_slots

@router.post("/extract")
async def extract(request: ExtractRequest) -> ExtractResponse:
    prompt = load_prompt(
        "extract",
        chat=request.chat,
        date=str(request.date),
        prev_slot=request.prev_slot.model_dump_json(),
        prev_query=request.prev_query or "없음",
    )

    llm = get_llm().with_structured_output(ExtractData)

    try:
        data = await asyncio.wait_for(llm.ainvoke(prompt), timeout=settings.TIMEOUT_EXTRACT)
    except asyncio.TimeoutError:
        raise LLMTimeoutError(detail="슬롯 추출 모델 호출이 제한 시간 내에 끝나지 않았습니다.")
    except APIError as e:
        if e.code == 429:
            raise LLMRateLimitedError(detail=str(e))
        raise LLMInvalidResponseError(detail=str(e))
    except ValidationError as e:
        raise LLMInvalidResponseError(detail=str(e))

    data.slot = merge_slots(request.prev_slot, data.slot)

    return ExtractResponse(message="extract_success", data=data)
