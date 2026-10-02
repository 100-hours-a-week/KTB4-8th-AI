"""코스 추천 임시 경로 — 후보 없이 Gemini 웹검색으로 바로 코스를 짠다 (2026-10 임시).

백엔드가 candidates 를 비워 보내면 이 경로를 탄다. 사용자 저장 데이터(영상 분석
결과)가 대부분 장소명 없이 저장돼 후보로 쓸 수 없어서, 대화에서 모은 조건만으로
추천하기 위한 임시조치다. 백엔드 설정(keepgo.recommendation.candidate-source)을
saved 로 되돌리면 후보가 다시 채워져 이 경로는 타지 않는다.

흐름: 웹검색(그라운딩)으로 코스 작성 → 형식에 맞춰 구조화 → course_builder 로
도착 시각·총 소요 시간·외출 가능 시간 검증. 검색과 구조화를 두 번에 나눈 이유는
verify_place.py 와 같다 — 랭체인에서 구조화 출력을 걸면 그라운딩이 조용히 빠진다.

장소 검증(verify-place)은 거치지 않는다. 폐업·오기재 장소가 섞일 수 있고, 이동·
체류 시간은 모델 추정이다. 좌표가 없어 거리 정렬·지도 API 는 쓰지 않는다.
"""

import time

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import LLMInvalidResponseError
from app.core.logging import log_llm
from app.models.factory import get_llm
from app.prompts.loader import load_prompt
from app.schemas.recommend_courses import RecommendCandidate, RecommendCoursesData, RecommendCoursesRequest
from app.services import course_builder as cb

_ENDPOINT = "recommend-courses:web"
_SEARCH_TOOL = {"google_search": {}}


# LLM 전용. 응답으로는 RecommendCoursesData 로 바꿔 내보낸다.
class WebPlaceLLM(BaseModel):
    place_name: str = Field(description="검색으로 확인한 정확한 상호 (지점명 포함)")
    summary: str = Field(description="장소 특징 한 줄")
    business_hours: str = Field(description="영업시간 원문. 모르면 '확인 불가'")
    travel_minutes: int = Field(description="직전 지점(첫 장소는 출발지)에서 오는 이동 시간 추정(분)")
    stay_minutes: int = Field(description="예상 체류 시간(분)")
    reason: str = Field(description="이 장소를 고른 이유")


class WebCourseLLM(BaseModel):
    title: str = Field(description="코스 제목")
    places: list[WebPlaceLLM] = Field(description="방문 순서대로")


class WebCoursesLLM(BaseModel):
    courses: list[WebCourseLLM]


def _or_unknown(value) -> str:
    return str(value) if value else "모름"


def _fast_llm():
    # 검색·구조화 모두 thinking 을 줄인다. 기존 추천 실측에서 기본 5.8초 → 512 로 3.8초였다.
    return get_llm().model_copy(update={"thinking_budget": settings.RECOMMEND_THINKING_BUDGET})


def _conditions(req: RecommendCoursesRequest) -> dict:
    return {
        "region": _or_unknown(req.region),
        "datetime": _or_unknown(req.datetime),
        "available_time": f"{req.available_time}분" if req.available_time else "제한 없음",
        "category": ", ".join(req.category) if req.category else "상관없음",
        "query": req.query.strip() or "없음",
        "origin": f"위도 {req.origin.lat:.5f}, 경도 {req.origin.lng:.5f}" if req.origin else "모름",
        "max_courses": settings.RECOMMEND_WEB_MAX_COURSES,
        "max_places": settings.RECOMMEND_WEB_MAX_PLACES,
    }


async def _search(req: RecommendCoursesRequest) -> str:
    """1단계: 그라운딩으로 웹검색해 코스를 자유 서술로 받는다."""
    prompt = load_prompt("recommend_web_search", **_conditions(req))
    llm = _fast_llm().bind_tools([_SEARCH_TOOL])
    started = time.perf_counter()
    try:
        message = await llm.ainvoke(prompt)
    except BaseException as exc:
        # 제한 시간에 걸려 취소(CancelledError)돼도 걸린 시간을 남긴다.
        log_llm(f"{_ENDPOINT}:search", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=repr(exc))
        raise
    log_llm(f"{_ENDPOINT}:search", settings.GOOGLE_MODEL, time.perf_counter() - started, success=True)
    return message.text


async def _structure(req: RecommendCoursesRequest, search_result: str) -> WebCoursesLLM:
    """2단계: 1단계 결과를 형식에 옮긴다. 검색 도구는 쓰지 않는다."""
    prompt = load_prompt("recommend_web_structure", search_result=search_result, **_conditions(req))
    chain = _fast_llm().with_structured_output(WebCoursesLLM, method="json_schema", include_raw=True)
    started = time.perf_counter()
    try:
        result = await chain.ainvoke(prompt)
    except BaseException as exc:
        log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=repr(exc))
        raise
    latency = time.perf_counter() - started
    if result["parsing_error"] is not None:
        detail = str(result["parsing_error"])
        log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL, latency, success=False, detail=detail)
        raise LLMInvalidResponseError(detail)
    log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL, latency, success=True)
    return result["parsed"]


def to_courses(req: RecommendCoursesRequest, parsed: WebCoursesLLM) -> RecommendCoursesData:
    """모델이 정한 장소·이동·체류 추정을 기존 응답 형식으로 바꾼다.

    도착 시각·총 소요 시간·외출 가능 시간 초과분 제외는 course_builder 가 계산한다.
    place_id 는 DB 에 없는 값이라 "web-<코스>-<순서>" 로 붙인다.
    """
    available = int(req.available_time) if req.available_time else None
    courses = []
    for ci, course in enumerate(parsed.courses[: settings.RECOMMEND_WEB_MAX_COURSES]):
        places = [p for p in course.places if p.place_name.strip()][: settings.RECOMMEND_WEB_MAX_PLACES]
        picks = [
            cb.Pick(
                RecommendCandidate(
                    place_id=f"web-{ci}-{pi}",
                    place_name=p.place_name,
                    summary=p.summary,
                    business_hours=p.business_hours,
                ),
                p.stay_minutes if p.stay_minutes > 0 else cb.DEFAULT_STAY_MINUTES,
                p.reason,
            )
            for pi, p in enumerate(places)
        ]
        travel = [max(0, p.travel_minutes) for p in places]
        if travel and req.origin is None:
            travel[0] = 0  # 출발지를 모르면 첫 장소까지의 이동은 계산하지 않는다
        built = cb.build_course(course.title, picks, travel, req.datetime, available)
        if built is not None:
            courses.append(built)
    return RecommendCoursesData(courses=courses)


async def recommend(req: RecommendCoursesRequest) -> RecommendCoursesData:
    search_result = await _search(req)
    parsed = await _structure(req, search_result)
    return to_courses(req, parsed)
