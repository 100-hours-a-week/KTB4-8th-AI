from pydantic import BaseModel

from app.core.config import settings
from app.models.base import LLMClient, LLMResponse, Message


class GeminiClient(LLMClient):

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._model = model or settings.GEMINI_MODEL
        self._api_key = api_key or settings.GEMINI_API_KEY

    async def complete(
        self,
        messages: list[Message],
        *,
        media_uri: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        raise NotImplementedError
