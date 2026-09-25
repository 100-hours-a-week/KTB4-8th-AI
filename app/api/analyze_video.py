"""영상 분석 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.analyze_video import (
    AnalyzeVideoRequest,
    AnalyzeVideoResponse,
    AnalyzeVideosRequest,
    AnalyzeVideosResponse,
)
from app.services.analyze_video import analyze_video as run_analysis
from app.services.analyze_video import analyze_videos as run_batch


@router.post("/analyze-video", response_model=AnalyzeVideoResponse)
async def analyze_video(req: AnalyzeVideoRequest) -> AnalyzeVideoResponse:
    # 예외는 잡지 않는다. main.py 의 AIServerError 핸들러가 상태 코드와
    # 에러 메시지로 바꾼다.
    data = await run_analysis(req)
    return AnalyzeVideoResponse(message="analyze_success", data=data)


@router.post("/analyze-videos", response_model=AnalyzeVideosResponse)
async def analyze_videos(req: AnalyzeVideosRequest) -> AnalyzeVideosResponse:
    # 영상별 성공/실패는 data.results 안에 있다. 일부가 실패해도 200 이다.
    data = await run_batch(req)
    return AnalyzeVideosResponse(message="analyze_success", data=data)
