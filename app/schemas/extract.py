from datetime import date as date_type
from datetime import datetime as datetime_type
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field

from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse, Coordinate

AvailableTime = Literal["180", "360", "540"]

# 날짜와 시간을 함께 받는다. datetime 은 시간 칸이 항상 있어서 "시간만 모름"을
# 따로 표현할 수 없다 — 그래서 00:00 을 "시간 모름"으로 약속한다(백엔드와 합의).
# 날짜만 들어와도("2026-10-18") 에러 없이 00:00 이 된다. 실제 자정 출발과
# 구분되지 않는 건 감수했다. 이 서비스에서 자정 출발 코스는 드물다.
def _drop_timezone(value: Optional[datetime_type]) -> Optional[datetime_type]:
    """시간대 표시만 떼고 시각 숫자는 그대로 둔다.

    모든 시각은 한국 시간이다. 그런데 Gemini 가 구조화 출력의 date-time 형식에
    맞춰 "14:00:00Z" 처럼 UTC 표시를 붙여 왔다(10/19 실측, 프롬프트로 막아도 붙음).
    그대로 내보내면 백엔드가 UTC 로 보고 한국 시간 23:00 으로 바꿀 수 있다.
    한국 시간으로 "변환"하면 안 된다 — 모델은 한국 시각을 적고 표시만 잘못 붙였다.
    """
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


VisitDatetime = Annotated[
    Optional[datetime_type],
    AfterValidator(_drop_timezone),
    Field(description="방문 날짜와 시각(한국 시간). 시각을 모르면 00:00"),
]


def has_visit_time(value: Optional[datetime_type]) -> bool:
    """00:00 은 "시간 모름" 약속이라 시각이 없는 것으로 본다."""
    return value is not None and (value.hour, value.minute) != (0, 0)


def _empty_coordinate_to_none(value):
    """백엔드는 출발지가 없을 때 {"lat": null, "lng": null} 을 보낸다(10/21 실측). 빈 좌표는 "출발지 없음"."""
    if isinstance(value, dict) and value.get("lat") is None and value.get("lng") is None:
        return None
    return value


# 출발지는 좌표로 주고받는다(10/21 결정, 코스 추천의 origin 과 같은 형식). 모델은 좌표를
# 만들 수 없어서, 모델에게는 주소로 바꿔 보여 주고(SlotsLLM) 모델이 말한 이름은 코드가
# 좌표로 바꾼다(api/extract.py). 이름 → 좌표는 주소 검색이라 "서울역" 같은 장소명은
# 못 찾을 수 있다 — 시험 후 문자열로 되돌릴 수 있는 임시 방식이다.
OriginCoordinate = Annotated[
    Optional[Coordinate],
    BeforeValidator(_empty_coordinate_to_none),
    Field(description="출발지 좌표. 없거나 못 찾으면 null"),
]


class Slots(BaseModel):
    """대화 슬롯 모델. origin 은 좌표, 나머지는 문자열이며 모두 null 이 될 수 있음"""
    origin: OriginCoordinate = None
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


Weekday = Literal["월", "화", "수", "목", "금", "토", "일"]


class SlotsLLM(BaseModel):
    """모델이 채우는 슬롯. 출발지는 좌표 대신 이름으로 받는다.

    날짜는 모델이 계산하지 않는다. 말한 표현만 옮기고, 실제 날짜는 코드가 오늘 날짜로
    계산한다(services/slot_datetime.py). Gemma 31B 가 "토요일"을 일요일 날짜로
    적었다(10/28, 2건 중 2건). Gemini 도 계산을 틀리면 막을 방법이 없었다.
    """
    origin: Optional[str] = Field(default=None, description="이번 발화에서 말한 출발지 이름")
    region: Optional[str] = Field(default=None, description="지역")
    days_from_today: Optional[int] = Field(default=None, description="오늘 0, 내일 1, 모레 2")
    weekday: Optional[Weekday] = Field(default=None, description="말한 요일. 주말은 토")
    week: Optional[Literal["이번 주", "다음 주"]] = Field(
        default=None, description="요일 앞에 '이번 주'·'다음 주'를 붙였을 때만. 요일만 말했으면 null"
    )
    month_day: Optional[str] = Field(default=None, description="말한 날짜 MM-DD (예: 11월 3일 → 11-03)")
    time: Optional[str] = Field(default=None, description="말한 시각 HH:MM (24시간)")
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분) (180·360·540)")
    category: Optional[list[PlaceCategory]] = Field(default=None, description="카테고리 목록. null 은 이번 턴에서 다루지 않음, [] 는 상관없음")


class ExtractDataLLM(BaseModel):
    """모델의 구조화 출력. 응답(ExtractData)으로 내보내기 전에 출발지를 좌표로 바꾼다."""
    slot: SlotsLLM
    query: str = Field(..., description="정성 조건 및 시간")
    bot_message: str = Field(..., description="사용자에게 보여줄 응답 문구")


ExtractResponse = APIResponse[ExtractData]
