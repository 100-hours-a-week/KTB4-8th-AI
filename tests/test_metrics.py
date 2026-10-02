"""Prometheus 지표(app/core/metrics.py) 시험. LLM 을 부르지 않는 경로만 쓴다."""

import os
import socket
import urllib.request

# app.core.config 가 import 시점에 설정을 읽으므로 그 전에 둔다.
os.environ.setdefault("GOOGLE_API_KEY", "test")
os.environ["METRICS_PORT"] = "0"
os.environ["SENTRY_DSN"] = ""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.api.router import router
from app.core import metrics
from app.main import app
from app.services import recommend_courses

CANCEL_ROUTE = "/v1/recommend-courses/{request_id}/cancel"


def count(route, status, method="POST"):
    value = REGISTRY.get_sample_value(
        "keepgo_http_requests_total",
        {"method": method, "route": route, "status": status, "traffic_class": metrics.traffic_class(route)},
    )
    return value or 0.0


def bucket_count(route, method="POST"):
    value = REGISTRY.get_sample_value(
        "keepgo_http_request_duration_seconds_count",
        {"method": method, "route": route, "traffic_class": metrics.traffic_class(route)},
    )
    return value or 0.0


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_every_api_route_has_a_deliberate_class():
    # 새 엔드포인트가 생기면 GENERATION_ROUTES 에 넣을지 정하도록 여기서 깨진다.
    # FastAPI 0.141 은 include_router 한 라우트를 app.routes 에 펼치지 않아 router 에서 읽는다.
    paths = {r.path for r in router.routes if isinstance(r, APIRoute)}
    assert paths - metrics.EXCLUDED_ROUTES == metrics.GENERATION_ROUTES | {CANCEL_ROUTE}


def test_route_label_is_template_not_request_id(client):
    before = count(CANCEL_ROUTE, "200")
    assert client.post("/v1/recommend-courses/req-123/cancel").status_code == 200
    assert client.post("/v1/recommend-courses/req-456/cancel").status_code == 200
    assert count(CANCEL_ROUTE, "200") == before + 2
    assert REGISTRY.get_sample_value(
        "keepgo_http_requests_total",
        {"method": "POST", "route": "/v1/recommend-courses/req-123/cancel", "status": "200", "traffic_class": "interactive"},
    ) is None


def test_validation_error_is_recorded_once_with_generation_class(client):
    before, before_hist = count("/v1/extract", "422"), bucket_count("/v1/extract")
    assert client.post("/v1/extract", json={}).status_code == 422
    assert count("/v1/extract", "422") == before + 1
    assert bucket_count("/v1/extract") == before_hist + 1


def test_unexpected_exception_is_recorded_as_500(client, monkeypatch):
    def boom(request_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(recommend_courses, "cancel", boom)
    before = count(CANCEL_ROUTE, "500")
    response = client.post("/v1/recommend-courses/req-1/cancel")
    assert response.status_code == 500
    assert response.json()["message"] == "internal_server_error"
    assert count(CANCEL_ROUTE, "500") == before + 1


def test_unmatched_path_uses_fixed_label(client):
    before = count("unmatched", "404", method="GET")
    assert client.get("/no/such/path/42").status_code == 404
    assert count("unmatched", "404", method="GET") == before + 1


def test_health_is_not_counted(client):
    assert client.get("/health").status_code == 200
    assert REGISTRY.get_sample_value(
        "keepgo_http_requests_total",
        {"method": "GET", "route": "/health", "status": "200", "traffic_class": "interactive"},
    ) is None


def test_metrics_server_exposes_contract_metrics(client):
    client.post("/v1/recommend-courses/req-9/cancel")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = metrics.start_metrics_server(port)
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    finally:
        server.shutdown()
    assert 'keepgo_observability_info{capability="http",contract="1"} 1.0' in body
    assert "keepgo_http_requests_total{" in body
    assert 'keepgo_http_request_duration_seconds_bucket{le="+Inf"' in body or 'le="+Inf"' in body
    assert "_created" not in body
