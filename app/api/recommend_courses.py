"""코스 추천·중단 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from fastapi import Header

from app.api.router import router
from app.schemas.common import APIResponse
from app.schemas.recommend_courses import RecommendCoursesRequest, RecommendCoursesResponse
from app.services import recommend_courses as service


@router.post("/v1/recommend-courses", response_model=RecommendCoursesResponse)
async def recommend_courses(
    req: RecommendCoursesRequest,
    x_request_id: str | None = Header(default=None),
) -> RecommendCoursesResponse:
    data = await service.run_cancellable(x_request_id, service.recommend_courses(req))
    if data is None:
        # 백엔드가 중단한 요청이라 이 응답은 쓰이지 않는다. 명세에 따로 정의된
        # 코드가 없어 중단 API 와 같은 코드로 답한다.
        return RecommendCoursesResponse(message="cancel_accepted", data=None)
    return RecommendCoursesResponse(message="recommend_success", data=data)


@router.post("/v1/recommend-courses/{request_id}/cancel", response_model=APIResponse[None])
async def cancel_recommend_courses(request_id: str) -> APIResponse[None]:
    service.cancel(request_id)
    return APIResponse(message="cancel_accepted", data=None)
