#영상분석 엔드포인트
from app.api.router import router

@router.post("/v1/analyze-video")
async def analyze_video():
    return