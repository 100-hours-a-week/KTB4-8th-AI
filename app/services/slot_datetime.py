"""extract 의 방문 일시 계산. 모델은 말한 표현만 옮기고(SlotsLLM), 날짜는 여기서 정한다.

요일 규칙(10/28 결정):
- 요일만 말하면("토요일") 오늘 이후 가장 가까운 그 요일. 오늘과 같은 요일이면 다음 주다 —
  오늘을 뜻했으면 "오늘"이라고 말한다.
- "이번 주 X"는 이번 주(월~일)의 X. 이미 지났으면 날짜를 비우고 다시 묻는다.
- "다음 주 X"는 다음 주(월~일)의 X.
연도 없는 날짜("11월 3일")는 오늘 이후 가장 가까운 그 날짜다.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.schemas.extract import SlotsLLM, has_visit_time

WEEKDAYS = "월화수목금토일"
# 날짜 없이 시각만 말한 턴에 모델이 query 에 남기는 출발 시각(extract.yaml 규칙 2-4)
_DEPART = re.compile(r"출발\s*(\d{1,2}:\d{2})")


def _parse_time(text: Optional[str]) -> Optional[time]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return time(int(m[1]), int(m[2]))


def _month_day(text: str, today: date) -> Optional[date]:
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text.strip())
    if not m:
        return None
    for year in (today.year, today.year + 1):
        try:
            day = date(year, int(m[1]), int(m[2]))
        except ValueError:  # 02-30 같은 없는 날짜, 평년의 02-29
            continue
        if day >= today:
            return day
    return None


def _resolve_date(slot: SlotsLLM, today: date) -> tuple[Optional[date], bool]:
    """말한 날짜 표현 → 날짜. 두 번째 값은 "이번 주 X"가 이미 지났는지."""
    if slot.month_day:
        return _month_day(slot.month_day, today), False
    if slot.days_from_today is not None and slot.days_from_today >= 0:
        return today + timedelta(days=slot.days_from_today), False
    if slot.weekday:
        target = WEEKDAYS.index(slot.weekday)
        monday = today - timedelta(days=today.weekday())
        if slot.week == "이번 주":
            day = monday + timedelta(days=target)
            return (None, True) if day < today else (day, False)
        if slot.week == "다음 주":
            return monday + timedelta(days=7 + target), False
        return today + timedelta(days=(target - today.weekday()) % 7 or 7), False
    return None, False


def resolve_visit(
    slot: SlotsLLM, today: date, prev: Optional[datetime], prev_query: Optional[str]
) -> tuple[Optional[datetime], bool]:
    """이번 발화의 방문 일시. None 이면 이번 턴에 바뀌지 않은 것이다(이전 값은 merge 가 지킨다).

    두 번째 값이 True 면 이미 지난 "이번 주 X"를 말한 것 — 호출하는 쪽이 날짜를 비우고 다시 묻는다.
    """
    day, passed = _resolve_date(slot, today)
    if passed:
        return None, True
    said = _parse_time(slot.time)

    if day is None:
        # 시각만 말하면 이전 날짜에 붙인다. 이전 날짜도 없으면 모델이 query 에 "출발 HH:MM" 으로 남긴다.
        return (datetime.combine(prev.date(), said), False) if said and prev else (None, False)

    if said is None:
        # 날짜만 말하면 이전에 정한 시각을 잇는다. 없으면 00:00("시각 모름" 약속).
        if prev is not None and has_visit_time(prev):
            said = prev.time()
        elif m := _DEPART.search(prev_query or ""):
            said = _parse_time(m[1])
    return datetime.combine(day, said or time(0, 0)), False


def with_weekday(value: date | datetime) -> str:
    """프롬프트용. 모델이 요일을 계산하지 않도록 날짜 옆에 붙여 준다."""
    text = f"{value:%Y-%m-%d %H:%M}" if isinstance(value, datetime) else f"{value:%Y-%m-%d}"
    return f"{text} ({WEEKDAYS[value.weekday()]})"
