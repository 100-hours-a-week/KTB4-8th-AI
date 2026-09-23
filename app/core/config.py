from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GOOGLE_API_KEY: str
    GOOGLE_MODEL: str = "gemini-3.5-flash-lite"
    LLM_PROVIDER: Literal["google", "local"] = "google"
    TIMEOUT_ANALYZE_VIDEO: int = 120
    TIMEOUT_VERIFY_PLACE: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
