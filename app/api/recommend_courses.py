"""코스 추천·중단 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import Header
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.router import router
from app.core.exceptions import AIServerError
from app.core.logging import report_handled_error
from app.schemas.common import APIResponse
from app.schemas.recommend_courses import (
    RecommendCoursesData,
    RecommendCoursesRequest,
    RecommendCoursesResponse,
)
from app.services import recommend_courses as service


# API 시트: 코스 추천은 성공·코스 없음·중단·실패를 모두 200 으로 보내고 message 와
# data.error 로 구분한다. 그래서 실패를 공통 예외 핸들러(main.py, HTTP 4xx/5xx)로
# 올리지 않고 여기서 잡는다. 요청 형식 오류(422)만 공통 핸들러를 탄다.
@router.post("/v1/recommend-courses", response_model=RecommendCoursesResponse)
async def recommend_courses(
    req: RecommendCoursesRequest,
    x_request_id: str | None = Header(default=None),
) -> RecommendCoursesResponse:
    try:
        data = await service.run_cancellable(x_request_id, service.recommend_courses(req))
    except AIServerError as exc:
        # service.recommend_courses 가 모든 예외를 우리 예외로 바꿔 올린다.
        # 공통 처리기를 안 타서 Sentry 자동 수집이 안 되므로 직접 남긴다.
        report_handled_error("POST /v1/recommend-courses recommend_failed", exc.error_code, exc)
        return RecommendCoursesResponse(
            message="recommend_failed", data=RecommendCoursesData(error=exc.error_code)
        )

    if data is None:
        # 백엔드가 중단한 요청이라 이 응답은 쓰이지 않는다.
        return RecommendCoursesResponse(message="recommend_cancel_accepted", data=RecommendCoursesData())
    if not data.courses:
        # 정상 처리됐지만 만들 수 있는 코스가 없다(후보 없음, 맞는 곳 없음, 시간 부족).
        return RecommendCoursesResponse(message="recommend_not_found", data=data)
    return RecommendCoursesResponse(message="recommend_success", data=data)


_STREAM_PATH = "/v3/recommend-course/stream"


def _done(data: RecommendCoursesData | None) -> dict:
    """서비스 결과를 done 이벤트의 data 로 바꾼다. 메시지 구분은 /v1 과 같다."""
    if data is None:
        return {"message": "recommend_cancel_accepted", "error": None, "courses": []}
    message = "recommend_success" if data.courses else "recommend_not_found"
    return {"message": message, **data.model_dump(mode="json")}


# API 시트(AI v3 6행): 단계를 시작할 때마다 status, 끝나면 done 한 번(코스 전체). done 뒤 연결 종료.
# 실패·중단도 done 으로 보낸다 — 스트림은 첫 이벤트를 보낼 때 이미 HTTP 200 이라서다.
# 중단은 /v1 과 같이 X-Request-Id 헤더 + 중단 API 로 한다.
@router.post(_STREAM_PATH, response_class=EventSourceResponse)
async def recommend_courses_stream(
    req: RecommendCoursesRequest,
    x_request_id: str | None = Header(default=None),
) -> AsyncIterator[ServerSentEvent]:
    # aclosing: 연결이 끊겨 이 생성기가 닫힐 때 서비스 쪽 생성기도 같은 때 닫는다.
    # 없으면 서비스 쪽은 파이썬이 따로 치울 때 닫혀서 작업 취소가 한 단계 늦어질 수 있다.
    async with aclosing(service.recommend_courses_stream(x_request_id, req)) as items:
        try:
            async for kind, value in items:
                if kind == "status":
                    yield ServerSentEvent(event="status", data={"stage": value})
                else:
                    yield ServerSentEvent(event="done", data=_done(value))
        except AIServerError as exc:
            # 서비스가 모든 예외를 우리 예외로 바꿔 올린다. 공통 처리기를 안 타서 직접 남긴다.
            report_handled_error(f"POST {_STREAM_PATH} recommend_failed", exc.error_code, exc)
            yield ServerSentEvent(
                event="done", data={"message": "recommend_failed", "error": exc.error_code, "courses": []}
            )


@router.post("/v1/recommend-courses/{request_id}/cancel", response_model=APIResponse[None])
async def cancel_recommend_courses(request_id: str) -> APIResponse[None]:
    service.cancel(request_id)
    return APIResponse(message="cancel_accepted", data=None)
