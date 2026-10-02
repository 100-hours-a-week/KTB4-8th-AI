"""Prometheus 지표. Cloud 레포 docs/monitoring-metrics-contract.md(계약 v1) 4절의 기본 구현.

AI API(8000)는 인증이 없어 모니터링 호스트에 열 수 없다. 지표는 별도 포트(9464)로만
노출하고, 그 포트는 Cloud 가 모니터링 보안 그룹에만 연다. uvicorn worker 가 1개라
(임베디드 Chroma 때문에 다중 프로세스 금지) 기본 registry 하나로 값이 모인다.
worker 를 늘리면 multiprocess 수집으로 다시 설계해야 한다.
"""

import time

from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics, start_http_server

# 계약에 없는 *_created 시계열을 만들지 않는다 — 지표마다 시계열이 두 배가 된다.
disable_created_metrics()

# 공통 HTTP 경계(BE 와 같다). 단위는 초.
HTTP_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300)

# 완료까지 기다리는 동기 AI 생성 경로. 나머지는 interactive 다. 스트리밍 경로는 없다.
# embed-places 는 생성이 아니라 임베딩 저장 후 결과만 알리는 짧은 호출이라 interactive 다.
GENERATION_ROUTES = frozenset(
    {
        "/v1/analyze-video",
        "/v1/extract",
        "/v1/verify-place",
        "/v1/recommend-courses",
    }
)
# 인프라 헬스체크는 요청량·오류율에 섞지 않는다.
EXCLUDED_ROUTES = frozenset({"/health"})

HTTP_REQUESTS = Counter(
    "keepgo_http_requests",
    "서버 HTTP 요청 완료 수",
    ["method", "route", "status", "traffic_class"],
)
HTTP_DURATION = Histogram(
    "keepgo_http_request_duration_seconds",
    "요청 수신부터 응답 완료까지 걸린 시간",
    ["method", "route", "traffic_class"],
    buckets=HTTP_BUCKETS,
)
OBSERVABILITY_INFO = Gauge(
    "keepgo_observability_info",
    "구현한 계측 범위. 정상 동작 여부가 아니다",
    ["contract", "capability"],
)
# 구현한 capability 만 1로 둔다. ai_provider·ai_usage 는 SDK 시도별 계측을 검증한 뒤 추가한다.
OBSERVABILITY_INFO.labels(contract="1", capability="http").set(1)


def traffic_class(route: str) -> str:
    return "generation" if route in GENERATION_ROUTES else "interactive"


class MetricsMiddleware:
    """요청 하나를 응답이 끝난 뒤 한 번 기록하는 ASGI 미들웨어.

    route 는 실제 경로가 아니라 라우터가 매칭한 템플릿이다 — cancel 경로의 request_id 처럼
    요청마다 다른 값을 label 로 쓰면 시계열이 끝없이 늘어난다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 응답을 시작하지 못하고 연결이 끊기면 unknown 으로 남긴다(5xx 로 바꾸지 않는다).
        status = "unknown"

        async def send_with_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = str(message["status"])
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_status)
        except Exception:
            # 처리되지 않은 예외는 이 미들웨어 바깥의 ServerErrorMiddleware 가 500 으로
            # 바꾼다. 여기서 500 을 기록하지 않으면 5xx 가 지표에서 빠진다.
            if status == "unknown":
                status = "500"
            raise
        finally:
            self._record(scope, status, time.perf_counter() - start)

    @staticmethod
    def _record(scope, status: str, elapsed: float) -> None:
        # 라우터가 매칭에 성공하면 scope["route"] 에 라우트 객체를 넣는다.
        matched = scope.get("route")
        route = getattr(matched, "path", None) or "unmatched"
        if route in EXCLUDED_ROUTES:
            return
        method = scope["method"]
        cls = traffic_class(route)
        HTTP_REQUESTS.labels(method=method, route=route, status=status, traffic_class=cls).inc()
        HTTP_DURATION.labels(method=method, route=route, traffic_class=cls).observe(elapsed)


def start_metrics_server(port: int):
    """지표 전용 HTTP 서버를 띄운다. port 가 0 이면 띄우지 않는다(로컬·테스트)."""
    if not port:
        return None
    server, _thread = start_http_server(port)
    return server
