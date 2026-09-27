"""장소 검증 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.verify_place import VerifyPlaceRequest, VerifyPlaceResponse
from app.services.verify_place import verify_place as run_verify


@router.post("/verify-place", response_model=VerifyPlaceResponse)
async def verify_place(req: VerifyPlaceRequest) -> VerifyPlaceResponse:
    # 예외는 잡지 않는다. main.py 의 AIServerError 핸들러가 상태 코드로 바꾼다
    # (지도 실패 502 map_api_error, 그라운딩 한도 429 llm_rate_limited 등).
    data = await run_verify(req)
    return VerifyPlaceResponse(message="verify_success", data=data)
