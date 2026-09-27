"""장소 검증 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.verify_place import VerifyPlaceRequest, VerifyPlaceResponse
from app.services.verify_place import verify_place as run_verify


@router.post("/v1/verify-place", response_model=VerifyPlaceResponse)
async def verify_place(req: VerifyPlaceRequest) -> VerifyPlaceResponse:
    data = await run_verify(req)
    return VerifyPlaceResponse(message="verify_success", data=data)
