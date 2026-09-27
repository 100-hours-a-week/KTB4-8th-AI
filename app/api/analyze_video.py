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


@router.post("/v1/analyze-video", response_model=AnalyzeVideoResponse)
async def analyze_video(req: AnalyzeVideoRequest) -> AnalyzeVideoResponse:
    data = await run_analysis(req)
    return AnalyzeVideoResponse(message="analyze_success", data=data)


@router.post("/v1/analyze-videos", response_model=AnalyzeVideosResponse)
async def analyze_videos(req: AnalyzeVideosRequest) -> AnalyzeVideosResponse:
    data = await run_batch(req)
    return AnalyzeVideosResponse(message="analyze_success", data=data)
