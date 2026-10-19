import logging

import sentry_sdk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-server")


def report_handled_error(where: str, exc: BaseException, code: str) -> None:
    """잡아서 응답 안에 담은 실패를 로그와 Sentry 에 남긴다.

    공통 예외 처리기(main.py)를 타는 실패는 Sentry 가 자동으로 수집하지만, 코스
    추천처럼 실패를 200 응답 안에 담는 곳은 예외가 라우트 밖으로 나가지 않아서
    아무 데도 안 잡힌다. 모니터링 도구를 부르는 곳을 여기 한 곳에 모아 둔다 —
    도구를 바꿀 때 api/ 를 고치지 않게(docs/3: 로깅은 core 책임).
    Sentry 를 초기화하지 않았으면(SENTRY_DSN 없음) capture_exception 은 아무것도 안 한다.
    """
    logger.error("%s -> %s detail=%s", where, code, getattr(exc, "detail", None) or exc)
    sentry_sdk.capture_exception(exc)


def log_llm(
    endpoint: str, model: str, latency: float, success: bool, detail: str | None = None
):
    if success:
        logger.info(
            "llm_call endpoint=%s model=%s, latency=%.2fs status=success",
            endpoint,
            model,
            latency,
        )
    else:
        logger.error(
            "llm_call endpoint=%s model=%s, latency=%.2fs status=failed detail=%s",
            endpoint,
            model,
            latency,
            detail,
        )
