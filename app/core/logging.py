import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-server")

# report_handled_error_throttled 가 (where, code)별로 마지막 보고 시각과 그 뒤 생략한 건수를 기억한다.
_last_reported: dict[tuple[str, str], tuple[float, int]] = {}


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


def report_handled_error_throttled(
    where: str, code: str, exc: BaseException | None = None, window: float = 300.0
) -> None:
    """report_handled_error 와 같지만, 같은 (where, code)는 window 초에 한 번만 Sentry 로 보낸다.

    GPU 처럼 실패해도 대체 경로로 응답이 나가는 곳은, 고장 나면 요청마다 같은 실패가 난다.
    그때마다 이벤트를 보내면 Sentry 한도만 쓰므로, 사이의 실패는 warning 으로만 남기고
    다음 보고에 생략한 건수를 붙인다.
    """
    now = time.monotonic()
    last, skipped = _last_reported.get((where, code), (None, 0))
    if last is not None and now - last < window:
        _last_reported[(where, code)] = (last, skipped + 1)
        logger.warning("%s -> %s (Sentry 보고 생략) detail=%r", where, code, exc)
        return
    _last_reported[(where, code)] = (now, 0)
    if skipped:
        code = f"{code} (직전 보고 뒤 {skipped}건 더 있었음)"
    report_handled_error(where, code, exc)


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
