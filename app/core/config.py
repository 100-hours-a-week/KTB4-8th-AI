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

    # 지도 API (주소 → 좌표) — 네이버 클라우드 Application Services > Maps.
    # .env 에 콘솔 Application 의 Client ID / Client Secret 두 개만 넣으면 된다.
    # 벤더는 하나만 쓴다 — 벤더마다 주소 체계·좌표 정밀도가 달라 섞으면 결과가
    # 들쭉날쭉해진다(docs/6 8-4 "반드시 한 벤더만 사용").
    NAVER_MAP_CLIENT_ID: str | None = None
    NAVER_MAP_CLIENT_SECRET: str | None = None
    # 새 Maps 상품의 도메인. 옛 상품(AI·NAVER API > 지도 API)은 2025-03 부터 신규
    # 신청이 막혀서 새로 받는 키는 전부 이쪽이다.
    NAVER_MAP_BASE_URL: str = "https://maps.apigw.ntruss.com"
    TIMEOUT_MAP_API: int = 5  # docs/6 5-2 — 단순 조회라 이보다 길면 벤더 이상

    # 임베디드 Chroma 의 저장 디렉터리. MySQL 에서 파생된 인덱스라 원본이
    # 아니며, 유실되어도 재생성할 수 있다(docs/3). .gitignore 대상.
    CHROMA_PATH: str = "./chroma"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
