"""코스 추천·중단 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from fastapi import Header

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
        report_handled_error("POST /v1/recommend-courses recommend_failed", exc, exc.error_code)
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


@router.post("/v1/recommend-courses/{request_id}/cancel", response_model=APIResponse[None])
async def cancel_recommend_courses(request_id: str) -> APIResponse[None]:
    service.cancel(request_id)
    return APIResponse(message="cancel_accepted", data=None)
