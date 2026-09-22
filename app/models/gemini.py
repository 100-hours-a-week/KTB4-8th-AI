from google import genai
from pydantic import BaseModel

from app.core.config import settings
from app.models.base import LLMClient, LLMResponse, Message


class GeminiClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._model = model or settings.GEMINI_MODEL
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._client = genai.Client(api_key=self._api_key)

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
        config = {}
        if response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_schema

        response = self._client.models.generate_content(
            model=self._model, contents=[m.content for m in messages], config=config
        )

        return LLMResponse(text=response.text, parsed=response.parsed)
