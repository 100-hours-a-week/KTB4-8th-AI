from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    TIMEOUT_ANALYZE_VIDEO: int = 120
    TIMEOUT_VERIFY_PLACE: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
