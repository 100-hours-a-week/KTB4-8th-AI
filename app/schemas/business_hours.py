from datetime import date
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, Field


def _to_hhmm(value):
    """초 이하를 버린다. 백엔드는 저장한 LocalTime 을 "11:30:00"·"23:59:59.999999999" 로
    돌려보낸다(10/22 recommend-courses 실측). 내보내는 값은 원래부터 "HH:MM" 이다."""
    if isinstance(value, str) and len(value) > 5 and value[2] == ":" and value[5] == ":":
        return value[:5]
    return value


def _merge_split_midnight(ranges):
    """백엔드가 쪼갠 자정 넘김 구간을 우리 표현으로 되돌린다.

    백엔드는 자정 넘김 영업을 그날 몫 {17:00:00, 23:59:59.999999999} 과 다음 날 몫
    {00:00:00, 01:00:00} 으로 나눠 보내고, 자정에 닫는 가게는 다음 날 몫이 길이 0 인
    {00:00:00, 00:00:00} 이 된다(10/22·10/23 실측).
    - 23:59:59.x 마감은 자정 마감(00:00)으로 바꾼다.
    - {00:00, 00:00} 은 언제나 지운다. 그것만 있던 날은 [] (휴무)가 된다 — 실제로 휴무일
      앞날의 꼬리였다(용산은행나무포차 일요일). 24시간 영업은 00:00~23:59 로 오므로
      (verify_place_structure.yaml 과 같은 약속) 여기에 걸리지 않는다.
    """
    if not isinstance(ranges, list):
        return ranges
    merged = []
    for r in ranges:
        if not isinstance(r, dict):
            merged.append(r)
            continue
        open_time, close_time = str(r.get("open_time", "")), str(r.get("close_time", ""))
        if open_time.startswith("00:00") and close_time.startswith("00:00"):
            continue  # 전날 자정 마감의 꼬리, 길이 0
        if close_time.startswith("23:59:59"):
            r = {**r, "close_time": "00:00"}
        merged.append(r)
    return merged


# 자정을 넘기는 영업은 close_time 이 open_time 보다 작다(18:00~02:00 → "02:00").
# 그래서 두 값의 앞뒤는 검사하지 않는다.
#패턴 두번 써야해서 ClockTime이용해준것
ClockTime = Annotated[str, BeforeValidator(_to_hhmm), Field(pattern = r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM")]

class TimeRange(BaseModel):
    open_time : ClockTime = Field(description = "여는 시각")
    close_time : ClockTime = Field(description = "닫는 시각, 자정을 넘기면 다음 날 시각 그대로")


# 요일 값의 의미: null = 모름, [] = 휴무, 그 외 = 영업 시간대 목록.
# exceptions 의 [] 도 휴무라 두 곳의 [] 가 같은 뜻이 된다.
Ranges = Annotated[list[TimeRange], BeforeValidator(_merge_split_midnight)]
DayHours = Optional[Ranges]

# 기본값을 두지 않아 7개 요일 키가 모두 필수다. 모르는 요일도 빠뜨리지 않고
# null 로 적게 해야 "빠뜨림"과 "모름"이 구분된다.
class WeeklyHours(BaseModel):
    MONDAY : DayHours
    TUESDAY : DayHours
    WEDNESDAY : DayHours
    THURSDAY : DayHours
    FRIDAY : DayHours
    SATURDAY : DayHours
    SUNDAY : DayHours

class BusinessHours(BaseModel):
    timezone : str = Field(default = "Asia/Seoul", description = "시간대")
    weekly_hours : WeeklyHours = Field(description = "요일별 정기 영업시간")
    exceptions : dict[date, Ranges] = Field(
        default_factory = dict,
        description = "임시 휴무·단축 영업. 영업측 공지 기준, 지난 날짜는 넣지 않는다. [] 는 그날 휴무"
    )
    estimated : bool = Field(default = False, description = "주변 가게 영업시간으로 추정한 값인지")