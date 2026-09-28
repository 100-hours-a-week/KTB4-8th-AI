import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.config import settings
from app.core.exceptions import AIServerError
from app.core.logging import logger
from app.schemas.common import APIResponse

# FastAPI 앱을 만들기 전에 초기화해야 FastAPI 통합이 자동으로 붙는다(Sentry 문서).
# send_default_pii=False — IP·헤더·쿠키를 보내지 않는다. 호출자가 백엔드뿐이라
# 필요 없는 정보다.
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, send_default_pii=False)

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
    # 핸들러는 except 블록 밖에서 불려 logger.exception 이 스택을 못 찾는다.
    # 예외를 직접 넘겨야 트레이스백이 로그에 남는다.
    logger.error("%s %s -> internal_server_error", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=APIResponse(message="internal_server_error").model_dump(),
    )


app.include_router(router)
