"""Tests for Flask Web UI and reverse proxy."""

import httpx
import respx
from httpx import Response

from mi_fitness_mcp.web import create_app


def test_index_route():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert "Mi Fitness" in response.text or "Mi Fitness Web UI" in response.text


def test_proxy_status_route():
    app = create_app(backend_url="http://127.0.0.1:8321")
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.get("/api/proxy_status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["proxy"] == "running"
    assert data["proxy_type"] == "Flask (Python)"
    assert data["backend_url"] == "http://127.0.0.1:8321"


@respx.mock
def test_proxy_forwarding():
    backend_url = "http://test-backend:8321"
    respx.get(f"{backend_url}/api/summary").mock(
        return_value=Response(200, json=[{"date": "2026-08-30", "steps": 8500}])
    )

    app = create_app(backend_url=backend_url, api_key="mif_sk_secret_test")
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/proxy/api/summary?start_date=2026-08-30&end_date=2026-08-31")
    assert resp.status_code == 200
    assert "X-Upstream-Time-Ms" in resp.headers
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["steps"] == 8500


@respx.mock
def test_proxy_upstream_error_502():
    backend_url = "http://non-existent-backend:8321"
    respx.get(f"{backend_url}/api/status").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    app = create_app(backend_url=backend_url)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/proxy/api/status")
    assert resp.status_code == 502
    data = resp.get_json()
    assert "error" in data
    assert "Flask 代理无法访问后端" in data["error"]
