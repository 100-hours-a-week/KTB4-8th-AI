"""후보 좌표 없이 코스 추천 시험. LLM·지도 API 를 부르지 않는 계산만 쓴다.

BE 가 장소 좌표를 아직 모으지 못해 candidates 의 lat·lng 를 비워 보낸다(2026-10-02 임시조치).
좌표가 필수였을 때는 요청 전체가 422 로 거부돼 추천이 전혀 동작하지 않았다.
"""

from app.schemas.recommend_courses import RecommendCandidate, RecommendCoursesRequest
from app.services import course_builder as cb

# BE RecommendCourseRequest 가 실제로 만드는 요청 모양
BE_REQUEST = {
    "query": "",
    "candidates": [
        {"place_id": "3", "place_name": "어라운드 성수", "summary": "", "lat": None, "lng": None,
         "business_hours": "정보 없음"},
        {"place_id": "5", "place_name": "을지로 산수갑산", "summary": "노포", "lat": None, "lng": None,
         "business_hours": "정보 없음"},
    ],
    "history_place_ids": [{"place_id": "3", "saved_at": "2026-10-02"}],
    "available_time": "180",
    "category": ["카페"],
    "datetime": "2026-10-09 13:00",
    "origin": {"lat": 37.5, "lng": 127.0364},
}


def candidate(place_id, lat=None, lng=None):
    return RecommendCandidate(place_id=place_id, place_name=f"장소{place_id}", summary="", lat=lat, lng=lng,
                              business_hours="정보 없음")


def pick(place_id, lat=None, lng=None):
    return cb.Pick(candidate(place_id, lat, lng), 60, "")


def test_be_request_without_coords_is_valid():
    req = RecommendCoursesRequest.model_validate(BE_REQUEST)
    assert req.candidates[0].lat is None
    assert req.datetime == "2026-10-09 13:00"


def test_order_keeps_model_order_without_coords():
    picks = [pick("b"), pick("a", 37.5, 127.0)]
    assert [p.candidate.place_id for p in cb.order_by_distance(picks, (37.5, 127.0))] == ["b", "a"]


def test_legs_without_coords_are_zero_minute():
    legs = cb.legs([pick("a"), pick("b", 37.5, 127.0)], (37.5, 127.0))
    assert legs == [None, None]


def test_build_course_without_coords():
    picks = [pick("a"), pick("b")]
    course = cb.build_course("코스", picks, [0, 0], "2026-10-09 13:00", 180)
    assert [p.travel_minutes for p in course.places] == [0, 0]
    assert [p.arrival_time for p in course.places] == ["13:00", "14:00"]


def test_rule_based_groups_without_coords():
    ranked = [candidate("a"), candidate("b"), candidate("c")]
    groups = cb.rule_based_groups(ranked, 60, 120)
    assert [[c.place_id for c in g] for g in groups] == [["a", "b"], ["c"]]


def test_coords_still_used_when_present():
    picks = [pick("far", 37.6, 127.1), pick("near", 37.5001, 127.0001)]
    ordered = cb.order_by_distance(picks, (37.5, 127.0))
    assert [p.candidate.place_id for p in ordered] == ["near", "far"]
