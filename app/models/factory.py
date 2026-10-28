import asyncio
import time
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
from app.core.logging import logger

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


@lru_cache(maxsize=1)
def get_gpu_llm() -> ChatOpenAI | None:
    """Modal 의 vLLM(OpenAI 호환 API). GPU_ENABLED 가 꺼져 있으면 None.

    재시도는 하지 않는다 — 실패하면 호출하는 쪽이 Gemini 로 넘어간다.
    temperature 0 은 10/27 실측과 같은 값이다.
    """
    if not settings.GPU_ENABLED:
        return None
    return ChatOpenAI(
        model=settings.GPU_MODEL,
        base_url=f"{settings.GPU_BASE_URL}/v1",
        api_key=settings.GPU_API_KEY,
        temperature=0,
        max_retries=0,
    )


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
    """
    started = time.monotonic()
    with trace(label, run_type="chain", inputs={"total": total, "gpu_timeout": gpu_timeout}) as run:
        reason = None
        if gpu is not None and gpu_timeout > 0:
            try:
                out = await asyncio.wait_for(gpu.ainvoke(prompt), timeout=min(gpu_timeout, total))
                run.end(outputs={"model": settings.GPU_MODEL, "fallback_reason": None})
                return out, settings.GPU_MODEL
            except Exception as exc:
                reason = "timeout" if isinstance(exc, TimeoutError) else f"error: {exc!r}"
                logger.warning("%s GPU 실패 %.1fs, Gemini 로 대체: %r", label, time.monotonic() - started, exc)
        left = total - (time.monotonic() - started)
        out = await asyncio.wait_for(gemini.ainvoke(prompt), timeout=left)
        run.end(outputs={"model": settings.GOOGLE_MODEL, "fallback_reason": reason})
        return out, settings.GOOGLE_MODEL


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
