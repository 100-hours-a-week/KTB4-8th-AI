"""영상 분석 엔드포인트. 라우팅과 응답 조립만 맡는다."""

from app.api.router import router
from app.schemas.analyze_video import AnalyzeVideoRequest, AnalyzeVideoResponse
from app.services.analyze_video import analyze_video as run_analysis


@router.post("/analyze-video", response_model=AnalyzeVideoResponse)
async def analyze_video(req: AnalyzeVideoRequest) -> AnalyzeVideoResponse:
    # 예외는 잡지 않는다. main.py 의 AIServerError 핸들러가 상태 코드와
    # 에러 메시지로 바꾼다.
    data = await run_analysis(req)
    return AnalyzeVideoResponse(message="analyze_success", data=data)
