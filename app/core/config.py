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

    # recommend-courses 는 챗봇 화면의 동기 경로라 전체 15초 deadline 안에서
    # 구간별 예산을 나눠 쓴다(docs/6 5-3). 예산을 넘긴 구간은 실패 대신 LLM 을
    # 쓰지 않는 폴백으로 넘어간다(docs/6 5-6).
    # 고정 분할이 아니라 "남은 시간"에서 떼어 준다 — gemini-3.8-flash 실측이
    # 재순위 4~5초·조합 5초라, 고정 4/5초로 자르면 거의 항상 폴백이 됐다.
    TIMEOUT_RECOMMEND_COURSES: float = 15.0
    RECOMMEND_BUDGET_RETRIEVAL: float = 1.5  # 쿼리 임베딩 + Chroma 검색, 상한
    RECOMMEND_BUDGET_RERANK: float = 6.5  # 상한. 조합 몫을 남기도록 더 줄어들 수 있다
    RECOMMEND_BUDGET_MAP: float = 3.0  # 상한. 남은 시간이 적으면 줄어든다
    # 남은 시간이 이보다 적으면 LLM 을 부르지 않고 바로 폴백한다
    RECOMMEND_MIN_LLM_SECONDS: float = 2.0
    # 코스 추천 호출의 thinking 토큰 상한. gemini-3.8-flash 재순위 실측(각 3회):
    # 기본 평균 5.8초 → 512 로 3.8초, 휴무 판단 정확도는 같았다. 0 은 효과 없음.
    RECOMMEND_THINKING_BUDGET: int = 512
    # 벡터 검색으로 압축한 뒤 LLM 에 넘길 후보 수
    RECOMMEND_TOP_K: int = 15

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

    # 에러 수집(Sentry). 비어 있으면 Sentry 를 켜지 않는다 — 로컬·테스트에서는
    # 안 보내도 되고, 운영에서는 인프라가 환경변수로 주입한다.
    SENTRY_DSN: str | None = None

    # LangSmith 추적. .env 에는 키만 두면 된다 — 키가 있으면 켜지고, 없으면
    # 조용히 꺼진다. 끄고 싶을 때만 LANGSMITH_TRACING=false. 운영은 인프라가
    # LANGSMITH_PROJECT 를 덮어써 로컬 기록과 섞이지 않게 한다.
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_TRACING: bool = True
    LANGSMITH_PROJECT: str = "keepgo_local"
    # 서비스 키(lsv2_sk_)는 워크스페이스를 지정해야 한다 — 없으면 trace 가
    # 403 으로 거부되는데 API 응답은 정상이라 티가 안 난다. 개인 키(lsv2_pt_)는 비워 둔다.
    LANGSMITH_WORKSPACE_ID: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
