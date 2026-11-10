"""슬롯 추출 엔드포인트. 라우팅, 요청 원본 로그, 응답 조립만 맡는다."""

from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.router import router
from app.core.exceptions import AIServerError
from app.core.logging import logger, report_handled_error
from app.schemas.extract import ExtractData, ExtractRequest, ExtractResponse
from app.services.extract import extract as run_extract
from app.services.extract import extract_stream as run_extract_stream


async def _log_request(request: ExtractRequest, http_request: Request | None) -> None:
    # 백엔드가 보낸 원본(raw)과 파싱 후 값(used)을 같이 남긴다. Slots 는 모르는 키를
    # 조용히 버려서, 키 이름이 다르면 used 만으로는 "전부 null"로만 보인다.
    raw = (await http_request.json()).get("prev_slot") if http_request else None
    logger.info("extract prev_slot raw=%s used=%s prev_query=%r",
                raw, request.prev_slot.model_dump_json(), request.prev_query)


@router.post("/v1/extract")
async def extract(request: ExtractRequest, http_request: Request = None) -> ExtractResponse:
    await _log_request(request, http_request)
    data = await run_extract(request)
    return ExtractResponse(message="extract_success", data=data)


_STREAM_PATH = "/v3/extract/stream"


def _done(data: ExtractData) -> dict:
    """done 이벤트의 data. 시트(AI v3 5행)대로 방문 일시는 "YYYY-MM-DD HH:MM:SS" 로 낸다(/v1 은 그대로)."""
    out = data.model_dump(mode="json")
    if out["slot"]["datetime"]:
        out["slot"]["datetime"] = out["slot"]["datetime"].replace("T", " ")
    return out


# API 시트(AI v3 5행): delta(text 를 순서대로 이어 붙여 표시) → done(slot·query·bot_message) 또는
# error(message). done·error 를 보내면 연결 종료. 실패도 HTTP 200 + error 이벤트다 — 스트림은
# 첫 이벤트를 보낼 때 이미 200 이라서다.
@router.post(_STREAM_PATH, response_class=EventSourceResponse)
async def extract_stream(request: ExtractRequest, http_request: Request) -> AsyncIterator[ServerSentEvent]:
    await _log_request(request, http_request)
    # aclosing: 연결이 끊겨 이 생성기가 닫힐 때 서비스 쪽 생성기(모델 호출)도 같은 때 닫는다.
    async with aclosing(run_extract_stream(request)) as items:
        try:
            async for kind, value in items:
                if kind == "delta":
                    yield ServerSentEvent(event="delta", data={"text": value})
                else:
                    yield ServerSentEvent(event="done", data=_done(value))
        except AIServerError as exc:
            # 서비스가 모든 예외를 우리 예외로 바꿔 올린다. 공통 처리기를 안 타서 직접 남긴다.
            report_handled_error(f"POST {_STREAM_PATH} extract_failed", exc.error_code, exc)
            yield ServerSentEvent(event="error", data={"message": exc.error_code})
