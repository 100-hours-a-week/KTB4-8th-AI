"""모든 API 엔드포인트가 공유하는 응답 스키마."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

ResponseMessage = Literal[
    "analyze_success",
    "verify_success",
    "extract_success",
    "recommend_success",
    "embed_success",
    "cancel_accepted",
    "embed_delete_success",
    "invalid_request",
    "validation_error",
    "llm_rate_limited",
    "internal_server_error",
    "llm_invalid_response",
    "map_api_error",
    "llm_timeout",
]


class APIResponse(BaseModel, Generic[T]):
    message: ResponseMessage = Field(description="처리 결과 코드")
    data: T | None = Field(default=None, description="응답 데이터 (실패 시 null)")
    detail: str | None = Field(
        default=None, description="에러 상세 메시지 (성공 시 null)"
    )
