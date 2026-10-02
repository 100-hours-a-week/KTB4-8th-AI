import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.config import settings
from app.core.exceptions import AIServerError
from app.core.logging import logger
from app.core.metrics import MetricsMiddleware, start_metrics_server
from app.schemas.common import APIResponse

# FastAPI 앱을 만들기 전에 초기화해야 FastAPI 통합이 자동으로 붙는다(Sentry 문서).
# send_default_pii=False — IP·헤더·쿠키를 보내지 않는다. 호출자가 백엔드뿐이라
# 필요 없는 정보다.
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, send_default_pii=False)


def enable_langsmith() -> bool:
    """키가 있으면 LangSmith 추적을 켠다.

    langsmith 는 config 가 아니라 프로세스 환경변수만 읽는다. pydantic-settings 는
    .env 를 설정값으로만 읽고 환경변수로 올리지 않으므로 여기서 옮겨 준다 —
    이게 없으면 로컬에서 --env-file 없이 띄울 때 추적이 꺼져 있었다.
    """
    if not (settings.LANGSMITH_API_KEY and settings.LANGSMITH_TRACING):
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
    if settings.LANGSMITH_WORKSPACE_ID:
        os.environ["LANGSMITH_WORKSPACE_ID"] = settings.LANGSMITH_WORKSPACE_ID
    return True


enable_langsmith()


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics_server = start_metrics_server(settings.METRICS_PORT)
    yield
    if metrics_server:
        metrics_server.shutdown()


app = FastAPI(lifespan=lifespan)
app.add_middleware(MetricsMiddleware)


# 응답은 명세대로 message·data 만 보낸다. 원인 파악용 상세는 로그로.
@app.exception_handler(AIServerError)
def handle_ai_server_error(request: Request, exc: AIServerError):
    logger.error("%s %s -> %s detail=%s", request.method, request.url.path, exc.error_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=APIResponse(message=exc.error_code).model_dump(),
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError):
    logger.warning("%s %s -> validation_error detail=%s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content=APIResponse(message="validation_error").model_dump(),
    )


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception):
    # 핸들러는 except 블록 밖에서 불려 logger.exception 이 스택을 못 찾는다.
    # 예외를 직접 넘겨야 트레이스백이 로그에 남는다.
    logger.error("%s %s -> internal_server_error", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=APIResponse(message="internal_server_error").model_dump(),
    )


app.include_router(router)
