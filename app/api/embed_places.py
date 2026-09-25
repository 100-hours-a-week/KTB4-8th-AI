"""장소 임베딩 저장 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.embed_places import EmbedPlacesRequest, EmbedPlacesResponse
from app.services.embed_places import embed_places as run_embedding


@router.post("/embed-places", response_model=EmbedPlacesResponse)
async def embed_places(req: EmbedPlacesRequest) -> EmbedPlacesResponse:
    data = await run_embedding(req)
    return EmbedPlacesResponse(message="embed_success", data=data)
