from datetime import date as date_type
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.available_time import AvailableTime
from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse


class Slots(BaseModel):
    """대화 슬롯 모델. 내부 요소는 전부 String이며 null이 될 수 있음"""
    origin: Optional[str] = Field(default=None, description="출발지")
    region: Optional[str] = Field(default=None, description="지역")
    datetime: Optional[str] = Field(default=None, description="날짜·시간대")
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분) (180·360·540)")
    category: Optional[PlaceCategory] = Field(default=None, description="카테고리")


class ExtractRequest(BaseModel):
    """슬롯 추출 요청 모델"""
    chat: str = Field(..., description="사용자 발화")
    date: date_type = Field(..., description="오늘 날짜 (상대 표현 변환 기준)")
    prev_slot: Slots = Field(..., description="이전 턴까지 채워진 슬롯 상태")
    prev_query: Optional[str] = Field(default=None, description="이전 턴까지의 정성 조건")


class ExtractData(BaseModel):
    """슬롯 추출 결과 모델"""
    slot: Slots = Field(..., description="누적 갱신된 슬롯 값")
    query: str = Field(..., description="정성 조건")
    bot_message: str = Field(..., description="사용자에게 보여줄 응답 문구")


ExtractResponse = APIResponse[ExtractData]
