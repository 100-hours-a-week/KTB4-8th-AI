from dataclasses import Field
from datetime import date
from typing import Optional
from pydantic import BaseModel

#요청 스키마
class AnalyzeRequest(BaseModel):
    video_url : str = Field(description = "유튜브 쇼츠 영상 url")

#응답 스키마
class AnalyzeResponse(BaseModel):
    place_name : Optional[str] = Field(description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    category : str = Field(description = "장소의 카테고리, 카테고리는 카페/팝업/전시/맛집 으로 나뉨")
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도")
    summary : Optional[str] = Field(default = None, description = "장소 요약")