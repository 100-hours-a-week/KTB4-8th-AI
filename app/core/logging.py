import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-server")


def report_handled_error(where: str, code: str, exc: BaseException | None = None) -> None:
    """잡아서 응답 안에 담은 실패(코스 추천 200 recommend_failed, 영상별 failed)를 남긴다.

    이런 실패는 예외가 라우트 밖으로 나가지 않아 공통 예외 처리기도, Sentry 의
    자동 예외 수집도 거치지 않는다. 그래서 error 로그로 남긴다 — Sentry 기본
    LoggingIntegration 이 error 이상 로그를 이벤트로 보낸다(sentry_sdk 2.70
    DEFAULT_EVENT_LEVEL). exc 를 붙이면 스택 트레이스까지 간다.
    capture_exception 을 따로 부르지 않는 건, 로그와 겹쳐 같은 실패가 두 건씩
    쌓였기 때문이다. 실패를 남기는 곳을 여기 한 곳에 모아 둔다(docs/3: 로깅은 core).
    """
    detail = (getattr(exc, "detail", None) or exc) if exc is not None else None
    logger.error("%s -> %s detail=%s", where, code, detail, exc_info=exc)


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
