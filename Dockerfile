# =========================================================
# AI Server Dockerfile
# - Python 3.13 / uv / FastAPI + Uvicorn
# - container port: 8000 (API), 9464 (metrics), entrypoint: app.main:app
# - health: GET /health
# - 쓰기 경로: /app/chroma (임베디드 Chroma, 호스트에 보존)
# =========================================================

ARG PYTHON_VERSION=3.13

# ---------- Stage 1: Builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# uv 버전 고정 (pip install uv는 매번 최신을 받는다)
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------- Stage 2: Runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CHROMA_PATH=/app/chroma

WORKDIR /app

# UID를 고정해 호스트 디렉터리 권한을 미리 맞출 수 있게 한다
RUN groupadd --gid 10002 appuser \
    && useradd --uid 10002 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# 코드와 venv는 root 소유(읽기 전용)로 두고 쓰기 경로만 appuser에게 준다.
# chown -R /app은 venv 전체를 레이어로 한 번 더 복사해 이미지를 키운다.
COPY --from=builder /app/.venv /app/.venv
COPY . .
RUN mkdir -p /app/chroma && chown appuser:appuser /app/chroma

USER appuser

# 8000: API, 9464: Prometheus 지표(문서용 — 실제 게시는 Cloud Compose 가 한다)
EXPOSE 8000 9464

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
