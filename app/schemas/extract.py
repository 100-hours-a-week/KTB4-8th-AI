from datetime import date as date_type
from datetime import datetime as datetime_type
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse

AvailableTime = Literal["180", "360", "540"]

# 날짜와 시간을 함께 받는다. datetime 은 시간 칸이 항상 있어서 "시간만 모름"을
# 따로 표현할 수 없다 — 그래서 00:00 을 "시간 모름"으로 약속한다(백엔드와 합의).
# 날짜만 들어와도("2026-10-18") 에러 없이 00:00 이 된다. 실제 자정 출발과
# 구분되지 않는 건 감수했다. 이 서비스에서 자정 출발 코스는 드물다.
VisitDatetime = Annotated[
    Optional[datetime_type],
    Field(description="방문 날짜와 시각. 시각을 모르면 00:00"),
]


def has_visit_time(value: Optional[datetime_type]) -> bool:
    """00:00 은 "시간 모름" 약속이라 시각이 없는 것으로 본다."""
    return value is not None and (value.hour, value.minute) != (0, 0)


class Slots(BaseModel):
    """대화 슬롯 모델. 내부 요소는 전부 String이며 null이 될 수 있음"""
    origin: Optional[str] = Field(default=None, description="출발지")
    region: Optional[str] = Field(default=None, description="지역")
    datetime: VisitDatetime = None
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분) (180·360·540)")
    category: Optional[list[PlaceCategory]] = Field(default=None, description="카테고리 목록. null 은 이번 턴에서 다루지 않음, [] 는 상관없음")


class ExtractRequest(BaseModel):
    """슬롯 추출 요청 모델"""
    chat: str = Field(..., description="사용자 발화")
    date: date_type = Field(..., description="오늘 날짜 (상대 표현 변환 기준)")
    prev_slot: Slots = Field(..., description="이전 턴까지 채워진 슬롯 상태")
    prev_query: Optional[str] = Field(default=None, description="이전 턴까지의 정성 조건 및 시간")


class ExtractData(BaseModel):
    """슬롯 추출 결과 모델"""
    slot: Slots = Field(..., description="누적 갱신된 슬롯 값")
    query: str = Field(..., description="정성 조건 및 시간")
    bot_message: str = Field(..., description="사용자에게 보여줄 응답 문구")


ExtractResponse = APIResponse[ExtractData]
