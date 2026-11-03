"""장소 검증 검색 결과(고정 형식 텍스트) → VerifyPlaceLLM. 모델 호출 없음.

형식은 verify_place_search.yaml 의 "출력 형식"이다. 예전엔 Gemini 구조화 호출이 하던 일이다(11/3 교체).
꾸밈(헤딩 #, 목록 기호, **굵게**)은 줄마다 먼저 지우고 읽는다 — 형식을 지정하기 전 검색 결과는
898건 중 99~100% 가 꾸밈을 붙였다(11/3 LangSmith). 형식을 못 읽으면 ParseError(이유)를 던진다.
"""
import re
from datetime import date

from app.schemas.business_hours import TimeRange, WeeklyHours
from app.schemas.verify_place import BusinessHoursLLM, ExceptionDayLLM, GroundedPlace, VerifyPlaceLLM

DAYS = {"월": "MONDAY", "화": "TUESDAY", "수": "WEDNESDAY", "목": "THURSDAY",
        "금": "FRIDAY", "토": "SATURDAY", "일": "SUNDAY"}
KEYS = {"근거": "evidence", "상호": "place_name", "주소": "address", "위치": "location_detail",
        "예외": "exceptions", "행사": "event"}
NONE_WORDS = {"없음", "해당 없음", "-", "정보 없음", "null"}
UNKNOWN = {"확인 불가", "모름", "정보 없음", "미확인"}

_DECOR = re.compile(r"^\s*(?:#{1,6}\s*|[-*•·]\s+|\d+\.\s+)*")
_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-~–]\s*(\d{1,2}):(\d{2})$")
_DATE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")


class ParseError(ValueError):
    pass


def clean(line: str) -> str:
    return _DECOR.sub("", line).replace("**", "").replace("__", "").strip()


def _hhmm(h: str, m: str) -> str:
    h = int(h)
    if h == 24:
        h = 0  # 자정 마감은 00:00 (앱 약속)
    if not 0 <= h <= 23 or not 0 <= int(m) <= 59:
        raise ParseError(f"시각 범위 밖 {h}:{m}")
    return f"{h:02d}:{m}"


def ranges(value: str) -> list[TimeRange]:
    out = []
    for part in re.split(r"[,/]", value):
        m = _RANGE.match(part.strip())
        if not m:
            raise ParseError(f"시간 형식 아님: {value!r}")
        out.append(TimeRange(open_time=_hhmm(m[1], m[2]), close_time=_hhmm(m[3], m[4])))
    return out


def day_value(value: str):
    v = value.strip()
    if v in UNKNOWN:
        return None
    if v.startswith("휴무") or v == "정기 휴무":
        return []
    return ranges(v)


def _date(text: str) -> date | None:
    m = _DATE.search(text)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def exceptions(value: str) -> list[ExceptionDayLLM]:
    if value.strip() in NONE_WORDS:
        return []
    out = []
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        d = _date(item)
        if d is None:
            raise ParseError(f"예외 날짜 없음: {item!r}")
        rest = _DATE.sub("", item).strip()
        out.append(ExceptionDayLLM(date=d, hours=[] if rest.startswith("휴무") or not rest else ranges(rest)))
    return out


def event(value: str) -> tuple[date | None, date | None, bool]:
    v = value.strip()
    if v in NONE_WORDS:
        return None, None, False
    if "확인 불가" in v:
        return _date(v), None, True
    found = [date(int(y), int(m), int(d)) for y, m, d in _DATE.findall(v)]
    if not found:
        raise ParseError(f"행사 형식 아님: {value!r}")
    return found[0], (found[1] if len(found) > 1 else None), len(found) < 2


def parse(text: str) -> tuple[VerifyPlaceLLM, dict]:
    """(결과, 통계). 통계: 버린 후보 수와 이유."""
    lines = [clean(l) for l in text.splitlines()]
    lines = [l for l in lines if l]
    verdict = next((l for l in lines if l.startswith("판정")), None)
    if verdict is None:
        raise ParseError("판정 줄 없음")
    if "없음" in verdict:
        return VerifyPlaceLLM(exists=False, candidates=[]), {"dropped": 0}

    blocks, cur = [], None
    for l in lines:
        if re.match(r"^\[?\s*후보\s*\d+\s*\]?\s*:?$", l):
            cur = {}
            blocks.append(cur)
        elif cur is not None and ":" in l:
            key, value = (s.strip() for s in l.split(":", 1))
            key = key.removesuffix("요일")
            if key in DAYS:
                cur.setdefault("days", {})[DAYS[key]] = value
            elif key in KEYS:
                cur[KEYS[key]] = value
    stats = {"dropped": 0, "drop_reasons": []}
    places = []
    for b in blocks:
        missing = [k for k in ("evidence", "place_name", "address") if not b.get(k) or b[k] in NONE_WORDS]
        if missing:
            stats["dropped"] += 1
            stats["drop_reasons"].append(f"빈 칸 {missing}")
            continue
        days = b.get("days", {})
        if len(days) != 7:
            raise ParseError(f"요일 {len(days)}개: {b['place_name']}")
        weekly = {k: day_value(v) for k, v in days.items()}
        exc = exceptions(b.get("exceptions", "없음"))
        start, end, unresolved = event(b.get("event", "해당 없음"))
        hours = None if all(v is None for v in weekly.values()) and not exc else \
            BusinessHoursLLM(weekly_hours=WeeklyHours(**weekly), exceptions=exc)
        loc = b.get("location_detail")
        places.append(GroundedPlace(
            evidence=b["evidence"], place_name=b["place_name"], address=b["address"],
            location_detail=None if not loc or loc in NONE_WORDS else loc,
            business_hours=hours, event_start_date=start, event_end_date=end,
            event_period_unresolved=unresolved))
    if not places:
        raise ParseError("운영 중인데 읽은 후보 0개")
    return VerifyPlaceLLM(exists=True, candidates=places), stats
