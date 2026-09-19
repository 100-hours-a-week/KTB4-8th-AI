class AIServerError(Exception):
    status_code = 500
    error_code = "internal_server_error"

    def __init__(self, detail=None):
        self.detail = detail
        super().__init__(detail)


class InvalidRequestError(AIServerError):
    status_code = 400
    error_code = "invalid_request"


class LLMRateLimitedError(AIServerError):
    status_code = 429
    error_code = "llm_rate_limited"


class InternalServerError(AIServerError):
    status_code = 500
    error_code = "internal_server_error"


class LLMInvalidResponseError(AIServerError):
    status_code = 502
    error_code = "llm_invalid_response"


class MapAPIError(AIServerError):
    status_code = 502
    error_code = "map_api_error"


class LLMTimeoutError(AIServerError):
    status_code = 504
    error_code = "llm_timeout"
