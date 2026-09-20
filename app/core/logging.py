import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-server")


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
