# =========================================================
# AI Server Dockerfile
#
# 확정:
# - Python 기반
# - dependency manager: uv
# - dependency definition: pyproject.toml
# - FastAPI + Uvicorn
# - container port: 8000
# - application entrypoint: app.main:app
#
# TODO:
# - uv 버전 고정
# - GET /health 구현
# - Chroma 저장 경로와 volume 정책 확정
# =========================================================


# =========================================================
# Stage 1: Builder
# =========================================================

# 현재 코드베이스 요구 버전: Python 3.13
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# TODO:
# uv 버전 검증 후 특정 버전으로 고정
RUN pip install --no-cache-dir uv

# 의존성 정의 파일을 source보다 먼저 복사하여
# source 변경 시 dependency layer cache 재사용
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# =========================================================
# Stage 2: Runtime
# =========================================================

FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Builder에서 설치된 runtime dependency만 복사
COPY --from=builder /app/.venv /app/.venv

# 애플리케이션 source 복사
COPY . .

# Secret은 Docker Image에 저장하지 않음.
# GOOGLE_API_KEY 등은 Compose / CI/CD / Secret 관리 계층에서
# Runtime 환경변수로 주입한다.

# Non-root 실행
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# TODO(AI): app/main.py에 FastAPI 애플리케이션이 추가되면 활성화한다.
# Compose는 실행 명령을 중복 정의하지 않고 이 CMD를 사용한다.
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
