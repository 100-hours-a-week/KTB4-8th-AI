#영상분석 엔드포인트
from app.api import router


@router.post("/analyze-video")
async def analyze_video():
    return