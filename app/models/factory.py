from functools import lru_cache

import httpx
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings
from app.core.exceptions import InternalServerError, LLMRateLimitedError, LLMTimeoutError

# docs/6 5-2절: 영상 분석(멀티모달) 60초. 시도 1번당 상한이다.
_VIDEO_TIMEOUT = 60.0

# 재시도는 SDK 한 층에서만 한다. 랭체인의 max_retries 는 이름과 달리 "총 시도
# 횟수"라 2 = 재시도 1회다. SDK 가 408·429·5xx·일시적 네트워크 오류를 지수
# 백오프로 재시도한다 — 우리가 따로 재시도 목록을 관리할 필요가 없다.
# 기본값(6)을 그대로 두면 모르는 사이 최대 6번 두드려서, 요금제상 영원히 안
# 되는 요청에도 429 하나 받는 데 35초가 걸렸다(verify-place 실측).
# 전체 시간은 각 서비스의 asyncio.wait_for 가 자른다.
_ATTEMPTS = 2


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
