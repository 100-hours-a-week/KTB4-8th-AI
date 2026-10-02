"""코스 조립 — LLM 을 쓰지 않는 계산만 모은다.

recommend_courses.py 에서 모델이 고른 장소를 받아 방문 순서·이동시간·도착
시각을 채우고 외출 가능 시간을 검증한다. 모델 호출이 실패했을 때의 규칙 기반
조합(docs/6 5-6)도 여기 있다. 외부 호출이 없어 단독으로 테스트할 수 있다.
"""

import math
from dataclasses import dataclass

from app.schemas.recommend_courses import Course, CoursePlace, RecommendCandidate

LatLng = tuple[float, float]

MAX_COURSES = 3
MAX_PLACES_PER_COURSE = 4

# 이 거리(직선) 이하는 걸어간다고 보고 지도 API 를 부르지 않는다. 자동차
# 길찾기는 250m 거리도 일방통행을 돌아 1.5km·6분으로 나온다(실측).
WALKING_MAX_METERS = 1000
_WALK_METERS_PER_MIN = 67  # 시속 4km
_DRIVE_METERS_PER_MIN = 333  # 시속 20km, 도심 평균 — 지도 API 가 경로를 못 찾을 때만 쓴다
_DETOUR = 1.3  # 직선 거리 → 실제 경로 보정

# 시간 정보 없이 날짜만 오면 이 시각에 출발한다고 본다
DEFAULT_START = "10:00"
DEFAULT_STAY_MINUTES = 60
_STAY_BY_CATEGORY = {"카페": 60, "맛집": 70, "전시": 90, "팝업": 40, "명소": 60, "기타": 60}


@dataclass
class Pick:
    """코스에 넣을 장소 하나. 이동시간은 순서가 정해진 뒤 채운다."""

    candidate: RecommendCandidate
    stay_minutes: int
    reason: str


def distance_m(a: LatLng, b: LatLng) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def estimate_minutes(a: LatLng, b: LatLng) -> int:
    """지도 API 없이 이동 시간을 추정한다. 가까우면 도보, 멀면 자동차."""
    meters = distance_m(a, b) * _DETOUR
    per_min = _WALK_METERS_PER_MIN if distance_m(a, b) <= WALKING_MAX_METERS else _DRIVE_METERS_PER_MIN
    return 0 if meters < 1 else max(1, round(meters / per_min))


def needs_map_api(a: LatLng, b: LatLng) -> bool:
    return distance_m(a, b) > WALKING_MAX_METERS


def coord(c: RecommendCandidate) -> LatLng | None:
    """좌표가 없는 후보는 None. BE가 장소 좌표를 아직 모으지 못해 비워 보낼 수 있다(임시)."""
    if c.lat is None or c.lng is None:
        return None
    return c.lat, c.lng


def default_stay(categories: list[str] | None) -> int:
    """모델이 체류 시간을 안 줬을 때(조합 실패·0분) 쓰는 기본값.

    후보(RecommendCandidate)에는 장소별 카테고리가 없어서 요청의 카테고리 목록만
    보고 정한다. None·[] 는 카테고리 조건이 없는 요청이다.
    """
    # TODO(human): 카테고리가 여러 개일 때 기본 체류 시간을 정한다.
    return DEFAULT_STAY_MINUTES


def order_by_distance(picks: list[Pick], origin: LatLng | None) -> list[Pick]:
    """가까운 곳부터 방문하도록 정렬한다(IA_SRS AI-012, 최근접 이웃).

    출발지가 있으면 거기서, 없으면 모델이 첫 번째로 둔 장소에서 시작한다.
    좌표 없는 장소가 하나라도 있으면 거리를 잴 수 없어 모델이 정한 순서를 그대로 둔다.
    """
    if len(picks) <= 1 or any(coord(p.candidate) is None for p in picks):
        return list(picks)
    remaining = list(picks)
    if origin is None:
        ordered = [remaining.pop(0)]
        here = coord(ordered[0].candidate)
    else:
        ordered, here = [], origin
    while remaining:
        nearest = min(remaining, key=lambda p: distance_m(here, coord(p.candidate)))
        remaining.remove(nearest)
        ordered.append(nearest)
        here = coord(nearest.candidate)
    return ordered


def legs(picks: list[Pick], origin: LatLng | None) -> list[tuple[LatLng, LatLng] | None]:
    """장소마다 직전 지점에서 오는 구간. 출발지 없는 첫 장소나 좌표 없는 구간은 None(이동 0분)."""
    result: list[tuple[LatLng, LatLng] | None] = []
    here = origin
    for p in picks:
        there = coord(p.candidate)
        result.append((here, there) if here is not None and there is not None else None)
        here = there
    return result


def _start_minutes(visit_datetime: str | None) -> int:
    hhmm = visit_datetime[11:16] if visit_datetime and len(visit_datetime) >= 16 else DEFAULT_START
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _clock(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def build_course(
    title: str,
    picks: list[Pick],
    travel: list[int],
    visit_datetime: str | None,
    available_minutes: int | None,
) -> Course | None:
    """순서·이동시간이 정해진 장소로 코스를 만든다.

    외출 가능 시간을 넘으면 뒤에서부터 장소를 뺀다 — 모델을 다시 부르지 않고
    코드로 맞춘다(재호출할 시간이 15초 deadline 안에 없다). 한 곳도 못 남으면
    None.
    """
    while picks:
        total = sum(travel[: len(picks)]) + sum(p.stay_minutes for p in picks)
        if available_minutes is None or total <= available_minutes:
            break
        picks = picks[:-1]
    if not picks:
        return None

    now = _start_minutes(visit_datetime)
    places = []
    for p, move in zip(picks, travel):
        now += move
        places.append(
            CoursePlace(
                place_id=p.candidate.place_id,
                place_name=p.candidate.place_name,
                travel_minutes=move,
                arrival_time=_clock(now),
                stay_minutes=p.stay_minutes,
                reason=p.reason,
            )
        )
        now += p.stay_minutes
    total = sum(p.travel_minutes + p.stay_minutes for p in places)
    return Course(title=title, places=places, total_duration_minutes=total)


def rule_based_groups(
    ranked: list[RecommendCandidate],
    stay_minutes: int,
    available_minutes: int | None,
) -> list[list[RecommendCandidate]]:
    """모델 없이 코스를 짠다(docs/6 5-6 코스 조합 폴백).

    적합도 순으로 코스의 첫 장소를 고르고, 그 주변에서 가까운 순으로 시간이
    허락하는 만큼 붙인다. 코스끼리 장소가 겹치지 않게 해 서로 다른 코스를 만든다.
    좌표가 없으면 거리를 잴 수 없어 적합도 순으로 붙이고 이동 시간은 0분으로 본다.
    """
    limit = available_minutes
    pool = list(ranked)
    groups = []
    while pool and len(groups) < MAX_COURSES:
        seed = pool.pop(0)
        group, used = [seed], stay_minutes
        while pool and len(group) < MAX_PLACES_PER_COURSE:
            here = coord(group[-1])
            if here is None or any(coord(c) is None for c in pool):
                nearest, move = pool[0], 0
            else:
                nearest = min(pool, key=lambda c: distance_m(here, coord(c)))
                move = estimate_minutes(here, coord(nearest))
            cost = move + stay_minutes
            if limit is not None and used + cost > limit:
                break
            pool.remove(nearest)
            group.append(nearest)
            used += cost
        groups.append(group)
    return groups


def template_title(first_place: str, query: str, category: str | None) -> str:
    """모델 없이 쓰는 제목. 요청에 지역명이 없어 docs/6 의 "지역명 기반"
    대신 코스의 중심 장소명으로 코스끼리 구분하고, 조건·카테고리를 붙인다.
    방문 순서는 나중에 거리로 다시 정렬되므로 "~에서 시작"이라고 쓰지 않는다."""
    theme = " ".join(p for p in (query.strip(), category or "") if p)
    return f"{first_place} 주변 {theme} 코스" if theme else f"{first_place} 주변 코스"
