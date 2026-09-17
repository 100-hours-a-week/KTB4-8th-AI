from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.exceptions import AIServerError

app = FastAPI()


@app.exception_handler(AIServerError)
def handle_ai_server_error(request: Request, exc: AIServerError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.error_code, "detail": exc.detail},
    )


app.include_router(router)
