"""후보 없는 코스 추천(웹검색 임시 경로) 시험. LLM 은 부르지 않고 바꿔 끼운다.

사용자 저장 데이터를 쓰지 않는 임시조치(2026-10)로, 백엔드가 candidates 를 비워
보내면 Gemini 웹검색으로 코스를 짠다(app/services/recommend_web.py).
"""

import asyncio
import os

os.environ.setdefault("GOOGLE_API_KEY", "test")

from app.schemas.recommend_courses import RecommendCoursesData, RecommendCoursesRequest
from app.services import recommend_courses, recommend_web
from app.services.recommend_web import WebCourseLLM, WebCoursesLLM, WebPlaceLLM

# 백엔드가 web 모드에서 보내는 요청 모양
WEB_REQUEST = {
    "query": "조용한 곳",
    "candidates": [],
    "history_place_ids": [],
    "available_time": "180",
    "category": ["카페", "전시"],
    "datetime": "2026-10-03 15:00",
    "origin": {"lat": 37.4979, "lng": 127.0276},
    "region": "서울 강남구",
}


def place(name, travel, stay):
    return WebPlaceLLM(place_name=name, summary="특징", business_hours="10:00-22:00",
                       travel_minutes=travel, stay_minutes=stay, reason="조용함")


def run(coro):
    return asyncio.run(coro)


def fake_web(monkeypatch, parsed=None, error=None):
    calls = []

    async def search(req):
        calls.append(req)
        if error:
            raise error
        return "검색 결과"

    async def structure(req, text):
        return parsed

    monkeypatch.setattr(recommend_web, "_search", search)
    monkeypatch.setattr(recommend_web, "_structure", structure)
    return calls


def test_web_request_shape_is_valid():
    req = RecommendCoursesRequest.model_validate(WEB_REQUEST)
    assert req.candidates == [] and req.region == "서울 강남구"


def test_empty_candidates_use_web_and_keep_response_format(monkeypatch):
    parsed = WebCoursesLLM(courses=[WebCourseLLM(title="강남 조용한 카페·전시", places=[
        place("카페 A", 15, 60), place("전시 B", 10, 60),
    ])])
    calls = fake_web(monkeypatch, parsed)

    data = run(recommend_courses.recommend_courses(RecommendCoursesRequest.model_validate(WEB_REQUEST)))

    assert len(calls) == 1
    assert isinstance(data, RecommendCoursesData)
    course = data.courses[0]
    assert [p.place_id for p in course.places] == ["web-0-0", "web-0-1"]
    assert [p.arrival_time for p in course.places] == ["15:15", "16:25"]
    assert course.total_duration_minutes == 145


def test_course_over_available_time_is_trimmed(monkeypatch):
    parsed = WebCoursesLLM(courses=[WebCourseLLM(title="긴 코스", places=[
        place("A", 10, 90), place("B", 10, 90), place("C", 10, 90),
    ])])
    fake_web(monkeypatch, parsed)

    data = run(recommend_courses.recommend_courses(RecommendCoursesRequest.model_validate(WEB_REQUEST)))

    assert [p.place_name for p in data.courses[0].places] == ["A"]


def test_web_failure_returns_empty_courses(monkeypatch):
    fake_web(monkeypatch, error=RuntimeError("grounding unavailable"))

    data = run(recommend_courses.recommend_courses(RecommendCoursesRequest.model_validate(WEB_REQUEST)))

    assert data.courses == []


def test_candidates_present_do_not_use_web(monkeypatch):
    calls = fake_web(monkeypatch, WebCoursesLLM(courses=[]))

    async def existing(req):
        return RecommendCoursesData(courses=[])

    monkeypatch.setattr(recommend_courses, "_recommend", existing)
    req = RecommendCoursesRequest.model_validate({**WEB_REQUEST, "candidates": [
        {"place_id": "1", "place_name": "카페", "summary": "", "business_hours": "확인 불가"},
    ]})

    run(recommend_courses.recommend_courses(req))

    assert calls == []


def test_web_courses_are_capped(monkeypatch):
    parsed = WebCoursesLLM(courses=[
        WebCourseLLM(title=f"코스{c}", places=[place(f"{c}-{i}", 0, 20) for i in range(4)]) for c in range(3)
    ])
    fake_web(monkeypatch, parsed)

    data = run(recommend_courses.recommend_courses(RecommendCoursesRequest.model_validate(WEB_REQUEST)))

    assert len(data.courses) == 2
    assert all(len(c.places) == 3 for c in data.courses)
