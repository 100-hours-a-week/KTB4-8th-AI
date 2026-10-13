from datetime import date
from typing import Annotated, Optional

from pydantic import BaseModel, Field

# 자정을 넘기는 영업은 close_time 이 open_time 보다 작다(18:00~02:00 → "02:00").
# 그래서 두 값의 앞뒤는 검사하지 않는다.
#패턴 두번 써야해서 ClockTime이용해준것
ClockTime = Annotated[str, Field(pattern = r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM")]

class TimeRange(BaseModel):
    open_time : ClockTime = Field(description = "여는 시각")
    close_time : ClockTime = Field(description = "닫는 시각, 자정을 넘기면 다음 날 시각 그대로")


# 요일 값의 의미: null = 모름, [] = 휴무, 그 외 = 영업 시간대 목록.
# exceptions 의 [] 도 휴무라 두 곳의 [] 가 같은 뜻이 된다.
DayHours = Optional[list[TimeRange]]

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
    exceptions : dict[date, list[TimeRange]] = Field(
        default_factory = dict,
        description = "임시 휴무·단축 영업. 영업측 공지 기준, 지난 날짜는 넣지 않는다. [] 는 그날 휴무"
    )
    estimated : bool = Field(default = False, description = "주변 가게 영업시간으로 추정한 값인지")