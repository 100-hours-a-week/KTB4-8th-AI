from datetime import date
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.common import APIResponse

#요청 스키마
class AnalyzeVideoRequest(BaseModel):
    video_url : str = Field(description = "유튜브 쇼츠 영상 url")

#응답 스키마
class AnalyzeVideoData(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    category : str = Field(description = "장소의 카테고리, 비슷한것끼리 묶임 (ex. 카페)") #돌려보고 정규화 맘에 들게 이뤄지지 않으면 Literal 사용)
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")

AnalyzeVideoResponse = APIResponse[AnalyzeVideoData]