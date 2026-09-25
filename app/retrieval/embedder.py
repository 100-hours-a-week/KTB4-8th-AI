"""텍스트 → 임베딩 변환.

임베딩 모델 교체 시 비즈니스 로직이 영향받지 않도록 여기서 격리한다(docs/3).
"""

from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import settings


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """설정에 맞는 임베딩 모델을 돌려준다. lru_cache 는 싱글턴 목적이다.

    저장할 땐 embed_documents(), 검색할 땐 embed_query() 를 쓴다.
    gemini-embedding-001 은 두 용도의 벡터를 다르게 만들어서(task_type),
    섞어 쓰면 검색 품질이 떨어진다(docs/5).
    """
    if settings.EMBEDDING_PROVIDER == "google":
        return GoogleGenerativeAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
        )

    raise ValueError(f"지원하지 않는 EMBEDDING_PROVIDER입니다: {settings.EMBEDDING_PROVIDER!r}")
