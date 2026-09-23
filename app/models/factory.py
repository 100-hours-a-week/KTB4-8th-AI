from functools import lru_cache
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from app.core.config import settings
from app.core.exceptions import InternalServerError, LLMTimeoutError, LLMRateLimitedError

_VIDEO_TIMEOUT = 60.0

RETRY = {
    "retry_if_exception_type" : (ConnectionError, TimeoutError),
    "stop_after_attempt" : 3,
    "wait_exponential_jitter" : True,
}

_RATE_LIMIT_HINTS = ("429", "quota", "rate limit", "resource_exhausted")

@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    if settings.LLM_PROVIDER == "google":
        return ChatGoogleGenerativeAI(
            model=settings.GOOGLE_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            timeout=_VIDEO_TIMEOUT,
        )

    raise ValueError(f"지원하지 않는 LLM_PROVIDER입니다 : {settings.LLM_PROVIDER!r}")

def to_domain_error(exc : Exception) -> Exception:
    if isinstance(exc, TimeoutError):
        return LLMTimeoutError(str(exc))
    
    text = str(exc).lower()
    if any(hint in text for hint in _RATE_LIMIT_HINTS):
        return LLMRateLimitedError(str(exc))
    if "deadline" in text or "timeout" in text:
        return LLMTimeoutError(str(exc))
    return InternalServerError(str(exc))