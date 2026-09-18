#V1 추론 인터페이스 — Gemini API 기준.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResponse:

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "UNKNOWN"
    parsed: BaseModel | None = None


class LLMClient(ABC):

    @abstractmethod
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
