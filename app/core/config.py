from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GOOGLE_API_KEY: str
    GOOGLE_MODEL: str = "gemini-3.5-flash-lite"
    LLM_PROVIDER: Literal["google", "local"] = "google"
    TIMEOUT_ANALYZE_VIDEO: int = 120
    TIMEOUT_VERIFY_PLACE: int = 60
    TIMEOUT_EXTRACT: int = 30
    TIMEOUT_EMBED_PLACES: int = 60

    # 배치 분석: Gemini 호출 1번에 영상 몇 개를 넣을지. Gemini 2.5 이상은
    # 요청당 최대 10개라 이보다 크게는 못 한다 — 품질을 보고 줄이는 것만 가능.
    ANALYZE_VIDEO_BATCH_SIZE: int = 10
    # 위 묶음 호출을 동시에 몇 개까지 보낼지. 임의 초기값이다 — 30건 부하
    # 테스트(d5-3)로 429 여부를 보고 조정한다.
    ANALYZE_VIDEO_PARALLEL: int = 3

    EMBEDDING_PROVIDER: Literal["google", "local"] = "google"
    EMBEDDING_MODEL: str = "models/gemini-embedding-001"

    # 임베디드 Chroma 의 저장 디렉터리. MySQL 에서 파생된 인덱스라 원본이
    # 아니며, 유실되어도 재생성할 수 있다(docs/3). .gitignore 대상.
    CHROMA_PATH: str = "./chroma"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
