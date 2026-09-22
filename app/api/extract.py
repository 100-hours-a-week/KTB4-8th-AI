#슬롯 추출 엔드포인트
from app.api.router import router
from app.schemas.extract import ExtractRequest, ExtractResponse

@router.post("/v1/extract")
async def extract(request: ExtractRequest) -> ExtractResponse:
    raise NotImplementedError
