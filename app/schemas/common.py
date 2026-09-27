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


# 명세(docs/1 2-1, API 시트)대로 message·data 두 필드만 내보낸다. 에러 원문은
# 응답에 싣지 않고 서버 로그에 남긴다(main.py 핸들러) — 내부 오류 문자열이
# 백엔드로 새지 않게 하고, 모르는 필드에 엄격한 파서도 깨지지 않게 한다.
class APIResponse(BaseModel, Generic[T]):
    message: ResponseMessage = Field(description="처리 결과 코드")
    data: T | None = Field(default=None, description="응답 데이터 (실패 시 null)")
