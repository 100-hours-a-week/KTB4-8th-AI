from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.exceptions import AIServerError
from app.core.logging import logger
from app.schemas.common import APIResponse

app = FastAPI()


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
    logger.exception("%s %s -> internal_server_error", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=APIResponse(message="internal_server_error").model_dump(),
    )


app.include_router(router)
