import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from functools import lru_cache

import httpx
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langsmith import trace

from app.core.config import settings
from app.core.exceptions import InternalServerError, LLMRateLimitedError, LLMTimeoutError
from app.core.logging import logger, report_handled_error, report_handled_error_throttled

# docs/6 5-2절: 영상 분석(멀티모달) 60초. 시도 1번당 상한이다.
_VIDEO_TIMEOUT = 120.0

# 재시도는 SDK 한 층에서만 한다. 랭체인의 max_retries 는 이름과 달리 "총 시도
# 횟수"라 2 = 재시도 1회다. SDK 가 408·429·5xx·일시적 네트워크 오류를 지수
# 백오프로 재시도한다 — 우리가 따로 재시도 목록을 관리할 필요가 없다.
# 기본값(6)을 그대로 두면 모르는 사이 최대 6번 두드려서, 요금제상 영원히 안
# 되는 요청에도 429 하나 받는 데 35초가 걸렸다(verify-place 실측).
# 전체 시간은 각 서비스의 asyncio.wait_for 가 자른다.
_ATTEMPTS = 1


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    if settings.LLM_PROVIDER == "google":
        return ChatGoogleGenerativeAI(
            model=settings.GOOGLE_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            timeout=_VIDEO_TIMEOUT,
            max_retries=_ATTEMPTS,
        )

    raise ValueError(f"지원하지 않는 LLM_PROVIDER입니다 : {settings.LLM_PROVIDER!r}")


def _gpu_root() -> str:
    # 끝의 "/" 나 "/v1" 이 남으면 //v1, /v1/v1 로 나가 Modal 이 500·404 를 낸다(10/28 운영).
    return settings.GPU_BASE_URL.strip().rstrip("/").removesuffix("/v1")


@lru_cache(maxsize=1)
def get_gpu_llm() -> ChatOpenAI | None:
    """Modal 의 vLLM(OpenAI 호환 API). GPU_ENABLED 가 꺼져 있거나 주소·키가 비어 있으면 None.

    재시도는 하지 않는다 — 실패하면 호출하는 쪽이 Gemini 로 넘어간다.
    temperature 0 은 10/27 실측과 같은 값이다.
    max_tokens 는 시간 초과로 끊긴 요청이 GPU 에서 계속 생성하며 자리를 차지하는 걸 막는다
    (10/28 운영: 끊긴 요청 2개가 70초 넘게 돌았다). 가장 긴 조합 출력 413토큰의 약 2.5배다.
    """
    if not settings.GPU_ENABLED:
        return None
    # 주소나 키가 비면 아래에서 None.strip()·OpenAIError 가 Gemini 대체 바깥에서 터져 요청이 실패한다.
    # 공백만 있으면 요청마다 "Connection error" 로 실패해 네트워크 장애처럼 보인다(11/2 실측).
    # 서버는 살리고 GPU 만 끈 채 Sentry 로 알린다 — lru_cache 라 프로세스마다 한 번 보고한다.
    missing = [name for name in ("GPU_BASE_URL", "GPU_API_KEY") if not (getattr(settings, name) or "").strip()]
    if missing:
        # 예외를 붙여 보낸다. 메시지만 보내면 다른 처리된 실패와 한 이슈로 묶일 수 있다.
        report_handled_error("get_gpu_llm", "gpu_config_missing", ValueError(f"{', '.join(missing)} 비어 있음"))
        return None
    return ChatOpenAI(
        model=settings.GPU_MODEL,
        base_url=f"{_gpu_root()}/v1",
        api_key=settings.GPU_API_KEY,
        temperature=0,
        max_tokens=1024,
        max_retries=0,
    )


async def wake_gpu() -> None:
    """GPU 컨테이너를 깨운다. 생성하지 않는 요청(GET /v1/models)이라 GPU 자리를 쓰지 않는다.

    부르는 쪽이 create_task 로 띄우고 기다리지 않는다. 깨우기용이라 실패해도 조용히 넘어간다 —
    GPU 가 정말 고장이면 뒤따르는 추출 호출이 실패하고 거기서 보고한다.
    타임아웃은 Modal 웹 요청 상한(150초)이다 — 콜드 스타트(12~106초) 동안 기다려야 깨워진다.
    """
    if get_gpu_llm() is None:
        return
    try:
        async with httpx.AsyncClient(timeout=150) as client:
            await client.get(f"{_gpu_root()}/v1/models",
                             headers={"Authorization": f"Bearer {settings.GPU_API_KEY}"})
    except httpx.HTTPError as exc:
        logger.warning("wake_gpu 실패 detail=%r", exc)


async def invoke_gpu_first(
    label: str, prompt, gpu: Runnable | None, gemini: Runnable, total: float, gpu_timeout: float
) -> tuple[object, str]:
    """GPU 를 먼저 부르고, 실패하거나 gpu_timeout 을 넘기면 남은 시간으로 Gemini 를 부른다.

    콜드 상태의 GPU 도 요청을 받으면 컨테이너를 띄우므로, 이 요청은 Gemini 로
    답하더라도 GPU 를 깨우는 역할을 한다. Gemini 의 예외는 그대로 올린다 —
    호출하는 쪽이 지금까지처럼 에러 코드로 바꾼다.
    돌려주는 두 번째 값은 실제로 답한 모델 이름이다(로그용).

    LangSmith 에는 label 이름의 부모 기록 아래 GPU·Gemini 호출이 묶이고, 부모 결과에
    답한 모델과 대체 이유가 남는다. 시간 초과로 끊긴 GPU 호출은 CancelledError 로만
    찍혀서, 부모 기록 없이는 콜드 대체인지 장애인지 열어 봐야 알 수 있었다.
    대체 이유가 없는데 model 이 Gemini 면 GPU 를 부르지 않은 것이다(꺼짐·예산 부족).

    호출마다 gpu_route 로그 한 줄을 남긴다(지연 분포·대체율 집계용). GPU 가 시간 초과가
    아닌 이유로 실패하면 고장 신호라 Sentry 로 보낸다 — 10/28 운영에서 주소 오류로 GPU 가
    전부 500 이었는데 Gemini 대체가 덮어 응답이 정상으로 보였다. 시간 초과는 콜드 스타트
    때 정상이라 보내지 않는다.
    """
    started = time.monotonic()
    gpu_outcome, gpu_latency, answered = "skipped", 0.0, "none"
    try:
        with trace(label, run_type="chain", inputs={"total": total, "gpu_timeout": gpu_timeout}) as run:
            reason = None
            if gpu is not None and gpu_timeout > 0:
                try:
                    out = await asyncio.wait_for(gpu.ainvoke(prompt), timeout=min(gpu_timeout, total))
                    gpu_outcome, gpu_latency, answered = "success", time.monotonic() - started, "gpu"
                    run.end(outputs={"model": settings.GPU_MODEL, "fallback_reason": None})
                    return out, settings.GPU_MODEL
                except Exception as exc:
                    gpu_latency = time.monotonic() - started
                    if isinstance(exc, TimeoutError):
                        gpu_outcome = reason = "timeout"
                    else:
                        gpu_outcome, reason = "error", f"error: {exc!r}"
                        report_handled_error_throttled(label, "gpu_error", exc)
            left = total - (time.monotonic() - started)
            out = await asyncio.wait_for(gemini.ainvoke(prompt), timeout=left)
            answered = "gemini"
            run.end(outputs={"model": settings.GOOGLE_MODEL, "fallback_reason": reason})
            return out, settings.GOOGLE_MODEL
    finally:
        # answered=none 이면 Gemini 까지 실패한 것이다(예외는 호출하는 쪽이 처리한다).
        log = logger.info if answered == "gpu" else logger.warning
        log("gpu_route label=%s answered=%s gpu_outcome=%s gpu_latency=%.2fs total_latency=%.2fs",
            label, answered, gpu_outcome, gpu_latency, time.monotonic() - started)


def _chunk_text(chunk) -> str:
    return chunk.text or ""


async def astream_gpu_first(
    label: str, prompt, gpu: Runnable | None, gemini: Runnable, total: float, gpu_first_timeout: float
) -> AsyncIterator[str]:
    """invoke_gpu_first 의 스트리밍판. 모델이 만드는 글자를 생기는 대로 내준다.

    GPU 의 첫 글자가 gpu_first_timeout 안에 오지 않거나 그 전에 실패하면 Gemini 로 넘어간다.
    첫 글자를 내준 뒤에는 넘어가지 않는다 — 이미 보낸 글자를 되돌릴 수 없어서다(11/10 결정 3a).
    그 뒤의 실패·시간 초과는 그대로 올린다. 전체 제한은 total 초.
    gpu_route 로그 한 줄은 invoke_gpu_first 와 같은 모양으로 남긴다.
    """
    started = time.monotonic()
    deadline = started + total
    gpu_outcome, gpu_latency, answered = "skipped", 0.0, "none"

    def left() -> float:
        return deadline - time.monotonic()

    async def first_text(stream: AsyncIterator, timeout: float) -> str | None:
        """빈 조각(역할만 담긴 첫 조각 등)을 건너뛰고 첫 글자를 기다린다. 끝까지 없으면 None."""
        end = time.monotonic() + timeout
        while True:
            try:
                chunk = await asyncio.wait_for(anext(stream), timeout=end - time.monotonic())
            except StopAsyncIteration:
                return None
            text = _chunk_text(chunk)
            if text:
                return text

    async def rest(stream: AsyncIterator) -> AsyncIterator[str]:
        while True:
            try:
                chunk = await asyncio.wait_for(anext(stream), timeout=left())
            except StopAsyncIteration:
                return
            text = _chunk_text(chunk)
            if text:
                yield text

    try:
        if gpu is not None and gpu_first_timeout > 0:
            async with aclosing(gpu.astream(prompt)) as stream:
                try:
                    first = await first_text(stream, min(gpu_first_timeout, left()))
                except Exception as exc:
                    first = None
                    gpu_latency = time.monotonic() - started
                    if isinstance(exc, TimeoutError):
                        gpu_outcome = "timeout"
                    else:
                        gpu_outcome = "error"
                        report_handled_error_throttled(label, "gpu_error", exc)
                if first is not None:
                    gpu_outcome, gpu_latency, answered = "success", time.monotonic() - started, "gpu"
                    yield first
                    async for text in rest(stream):
                        yield text
                    return
        async with aclosing(gemini.astream(prompt)) as stream:
            first = await first_text(stream, left())
            answered = "gemini"
            if first is not None:
                yield first
                async for text in rest(stream):
                    yield text
    finally:
        log = logger.info if answered == "gpu" else logger.warning
        log("gpu_route label=%s answered=%s gpu_outcome=%s gpu_latency=%.2fs total_latency=%.2fs stream=true",
            label, answered, gpu_outcome, gpu_latency, time.monotonic() - started)


def to_domain_error(exc: BaseException) -> Exception:
    """벤더 예외를 우리 타입으로 바꾼다.

    main.py 핸들러가 AIServerError 만 잡으므로, 그대로 올리면 docs/1 의 상태
    코드(llm_timeout·llm_rate_limited 등)가 백엔드에 전달되지 않는다.

    판별은 langchain_core 의 중립 타입으로 한다. 랭체인이 Gemini 의 429 를
    ModelRateLimitError 로 바꿔 던지므로, v2 에서 로컬 모델로 바꿔도 이
    함수는 그대로 쓸 수 있다.
    """
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return LLMTimeoutError(str(exc))
    if isinstance(exc, ModelRateLimitError):
        return LLMRateLimitedError(str(exc))
    return InternalServerError(str(exc))
