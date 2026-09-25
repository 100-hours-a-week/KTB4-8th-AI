import time
from urllib.parse import urlparse

from app.core.config import settings
from app.core.exceptions import InvalidRequestError, LLMInvalidResponseError
from app.core.logging import log_llm
from app.models.factory import RETRY, get_llm, to_domain_error
from app.prompts.analyze_video import ANALYZE_VIDEO_PROMPT
from app.schemas.analyze_video import (
    AnalyzeVideoData,
    AnalyzeVideoLLM,
    AnalyzeVideoRequest,
)

_ENDPOINT = "analyze-video"

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

#백엔드에서 1차적으로 걸러주므로 확인용
# def _validate(url: str) -> None:
#     parsed = urlparse(url)
#     if parsed.scheme not in ("http", "https"):
#         raise InvalidRequestError(f"http(s) URL이 아닙니다: {url}")
#     if parsed.netloc.lower() not in _YOUTUBE_HOSTS:
#         raise InvalidRequestError(f"유튜브 URL이 아닙니다: {url}")

async def analyze_video(req: AnalyzeVideoRequest) -> AnalyzeVideoData:
    chain = ANALYZE_VIDEO_PROMPT | get_llm().with_structured_output(
        AnalyzeVideoLLM, method="json_schema", include_raw=True
    ).with_retry(**RETRY)

    started = time.perf_counter()
    try:
        result = await chain.ainvoke({"video_url": req.video_url})
    except Exception as exc:
        log_llm(
            _ENDPOINT,
            settings.GOOGLE_MODEL,
            time.perf_counter() - started,
            success=False,
            detail=str(exc),
        )
        raise to_domain_error(exc) from exc

    latency = time.perf_counter() - started

    # include_raw 를 쓰면 파싱 실패가 예외로 오지 않고 필드에 담겨 온다.
    # 여기서 잡지 않으면 parsed=None 이 api/ 까지 흘러가 원인에서 먼 곳에서
    # 터진다.
    if result["parsing_error"] is not None:
        detail = str(result["parsing_error"])
        log_llm(_ENDPOINT, settings.GOOGLE_MODEL, latency, success=False, detail=detail)
        raise LLMInvalidResponseError(detail)

    # 토큰은 result["raw"].usage_metadata 로 꺼낼 수 있으나 log_llm 에 자리가
    # 없다. 사용량은 LangSmith 에서 본다. → v2
    log_llm(_ENDPOINT, settings.GOOGLE_MODEL, latency, success=True)

    return result["parsed"].to_data()