#슬롯 추출 엔드포인트
from app.api.router import router
from app.models.factory import get_llm_client
from app.prompts.loader import load_prompt
from app.schemas.extract import ExtractData, ExtractRequest, ExtractResponse

@router.post("/v1/extract")
async def extract(request: ExtractRequest) -> ExtractResponse:
    prompt = load_prompt(
        "extract",
        chat=request.chat,
        date=str(request.date),
        prev_slot=request.prev_slot.model_dump_json(),
        prev_query=request.prev_query or "없음",
    )

    llm = get_llm_client().with_structured_output(ExtractData)
    data = await llm.ainvoke(prompt)

    return ExtractResponse(message="extract_success", data=data)
