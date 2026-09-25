from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings


@lru_cache(maxsize=1)
def get_llm_client() -> ChatGoogleGenerativeAI:
    # v2에서 분기 추가, v1에서는 Gemini만 사용하므로 바로 반환한다.
    provider = settings.MODEL_PROVIDER

    if provider == "gemini":
        return ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL, google_api_key=settings.GEMINI_API_KEY)

    raise ValueError(f"지원하지 않는 MODEL_PROVIDER입니다: {provider!r}")
