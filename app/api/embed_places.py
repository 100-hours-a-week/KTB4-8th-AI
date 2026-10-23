"""장소 임베딩 저장·삭제 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.embed_places import (
    EmbedPlacesDeleteRequest,
    EmbedPlacesDeleteResponse,
    EmbedPlacesRequest,
    EmbedPlacesResponse,
)
from app.services.embed_places import delete_places as run_delete
from app.services.embed_places import embed_places as run_embedding


@router.post("/v1/embed-places", response_model=EmbedPlacesResponse)
async def embed_places(req: EmbedPlacesRequest) -> EmbedPlacesResponse:
    data = await run_embedding(req)
    return EmbedPlacesResponse(message="embed_success", data=data)


@router.post("/v1/embed-places/delete", response_model=EmbedPlacesDeleteResponse)
async def delete_embedded_places(req: EmbedPlacesDeleteRequest) -> EmbedPlacesDeleteResponse:
    data = await run_delete(req)
    return EmbedPlacesDeleteResponse(message="embed_delete_success", data=data)
