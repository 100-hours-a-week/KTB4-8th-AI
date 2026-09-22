from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.exceptions import AIServerError
from app.schemas.common import APIResponse

app = FastAPI()


@app.exception_handler(AIServerError)
def handle_ai_server_error(request: Request, exc: AIServerError):
    return JSONResponse(
        status_code=exc.status_code,
        content=APIResponse(message=exc.error_code, detail=exc.detail).model_dump(),
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=APIResponse(message="validation_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=APIResponse(message="internal_server_error", detail=str(exc)).model_dump(),
    )


app.include_router(router)
