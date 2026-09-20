from functools import lru_cache

from app.core.config import settings
from app.models.base import LLMClient
from app.models.gemini import GeminiClient


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    # v2에서 분기 추기, v1에서는 Gemini만 사용하므로 바로 반환한다.
    provider = settings.MODEL_PROVIDER

    if provider == "gemini":
        return GeminiClient()

    raise ValueError(f"지원하지 않는 MODEL_PROVIDER입니다: {provider!r}")
