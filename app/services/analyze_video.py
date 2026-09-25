import asyncio
import time
from urllib.parse import urlparse

from app.core.config import settings
from app.core.exceptions import InvalidRequestError, LLMInvalidResponseError
from app.core.logging import log_llm
from app.models.factory import RETRY, get_llm, to_domain_error
from app.prompts.analyze_video import ANALYZE_VIDEO_PROMPT, build_batch_messages
from app.schemas.analyze_video import (
    AnalyzeVideoBatchLLM,
    AnalyzeVideoData,
    AnalyzeVideoLLM,
    AnalyzeVideoRequest,
    AnalyzeVideosData,
    AnalyzeVideosRequest,
    VideoAnalysisResult,
)

_ENDPOINT = "analyze-video"
_BATCH_ENDPOINT = "analyze-videos"

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
        # 백엔드는 동기로 이 시간만큼만 기다린다(docs/1 비고 120초). 재시도가
        # 있어도 이 상한은 못 넘는다 — 4분 대기(docs/6 5-3)를 막는 장치.
        result = await asyncio.wait_for(
            chain.ainvoke({"video_url": req.video_url}),
            timeout=settings.TIMEOUT_ANALYZE_VIDEO,
        )
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

# ── 배치: /v1/analyze-videos ─────────────────────────────────────────

# 묶음 호출을 동시에 몇 개까지 보낼지. 서버 전체에 하나라, 배치 요청이 두 개
# 동시에 와도 Gemini 로 가는 묶음 호출은 합쳐서 이 수를 넘지 않는다(429 방지).
# 영상 분석 전용이다 — extract 와 공유하면 배치 뒤에 챗봇 요청이 줄을 선다
# (docs/6 5-5 "배치가 실시간을 굶기면 안 된다").
_BATCH_SLOTS = asyncio.Semaphore(settings.ANALYZE_VIDEO_PARALLEL)


def _failed(url: str, code: str) -> VideoAnalysisResult:
    return VideoAnalysisResult(video_url=url, status="failed", error=code)


async def _analyze_group(urls: list[str]) -> list[VideoAnalysisResult]:
    """영상 최대 10개를 Gemini 호출 1번으로 분석한다.

    실패 단위가 묶음이라는 점에 주의 — 호출이 실패하면 안의 영상이 전부
    실패로 나간다. 모델이 일부 영상을 빠뜨리면 그 영상만 실패로 표시한다.
    """
    chain = get_llm().with_structured_output(
        AnalyzeVideoBatchLLM, method="json_schema", include_raw=True
    ).with_retry(**RETRY)

    started = time.perf_counter()
    try:
        # 슬롯을 잡은 뒤에 재므로 줄 서는 시간은 빠진다 — 뒤 묶음이 대기만으로
        # 타임아웃 나지 않게.
        result = await asyncio.wait_for(
            chain.ainvoke(build_batch_messages(urls)),
            timeout=settings.TIMEOUT_ANALYZE_VIDEO,
        )
    except Exception as exc:
        log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, time.perf_counter() - started,
                success=False, detail=str(exc))
        code = to_domain_error(exc).error_code
        return [_failed(u, code) for u in urls]

    latency = time.perf_counter() - started

    if result["parsing_error"] is not None:
        log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, latency,
                success=False, detail=str(result["parsing_error"]))
        return [_failed(u, "llm_invalid_response") for u in urls]

    log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, latency, success=True)

    # 결과 순서를 믿지 않고 video_index 로 짝짓는다. 번호가 없는 영상은
    # 모델이 빠뜨린 것이다.
    by_index = {item.video_index: item for item in result["parsed"].results}
    return [
        VideoAnalysisResult(video_url=url, status="success", result=by_index[i].to_data())
        if i in by_index
        else _failed(url, "llm_invalid_response")
        for i, url in enumerate(urls, start=1)
    ]


async def analyze_videos(req: AnalyzeVideosRequest) -> AnalyzeVideosData:
    size = settings.ANALYZE_VIDEO_BATCH_SIZE
    urls = req.video_urls
    groups = [urls[i:i + size] for i in range(0, len(urls), size)]

    async def run(group: list[str]) -> list[VideoAnalysisResult]:
        async with _BATCH_SLOTS:
            return await _analyze_group(group)

    # _analyze_group 이 예외를 다 잡지만, 예상 못 한 버그 하나가 다른 묶음까지
    # 날리지 않도록 한 번 더 격리한다(스프린트 d2-4).
    outcomes = await asyncio.gather(*(run(g) for g in groups), return_exceptions=True)

    results: list[VideoAnalysisResult] = []
    for group, outcome in zip(groups, outcomes):
        if isinstance(outcome, BaseException):
            code = to_domain_error(outcome).error_code
            results.extend(_failed(u, code) for u in group)
        else:
            results.extend(outcome)

    return AnalyzeVideosData(results=results)
