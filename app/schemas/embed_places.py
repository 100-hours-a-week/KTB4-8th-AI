from pydantic import BaseModel, Field

from app.schemas.common import APIResponse


#요청 스키마
class PlaceToEmbed(BaseModel):
    place_id : str = Field(description = "장소 식별자 (백엔드 DB의 PK)")
    place_name : str = Field(description = "장소명. 임베딩하지 않고 메타데이터로만 저장한다")
    summary : str = Field(description = "장소 특징 요약. 이 값을 임베딩한다")


class EmbedPlacesRequest(BaseModel):
    # docs/1 비고: places 리스트 최대 100개
    places : list[PlaceToEmbed] = Field(max_length = 100, description = "임베딩할 장소 목록")


#응답 스키마
class EmbedPlacesData(BaseModel):
    embedded_count : int = Field(description = "저장에 성공한 장소 수")


EmbedPlacesResponse = APIResponse[EmbedPlacesData]


# ── 삭제 (/v1/embed-places/delete) ────────────────────────────────────

#요청 스키마
class EmbedPlacesDeleteRequest(BaseModel):
    # 시트: 최대 100개. 없는 id 가 섞여도 에러가 아니다 — 서비스가 걸러낸다.
    place_ids : list[str] = Field(max_length = 100, description = "삭제할 장소 식별자 목록")


#응답 스키마
class EmbedPlacesDeleteData(BaseModel):
    deleted_count : int = Field(description = "실제로 삭제된 장소 수 (없던 id 는 세지 않음)")


EmbedPlacesDeleteResponse = APIResponse[EmbedPlacesDeleteData]
