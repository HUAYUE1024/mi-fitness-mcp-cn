"""REST API: auth gate, key lifecycle guards, analytics endpoints (demo database, no network)."""

import pytest
from fastapi.testclient import TestClient

from mi_fitness_mcp import api as api_module
from mi_fitness_mcp.demo import generate_demo_data
from mi_fitness_mcp.storage import Database


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    generate_demo_data(Database(db_path), days=30)
    monkeypatch.setenv("MI_FITNESS_DEMO", "1")
    monkeypatch.setattr(api_module, "demo_database", lambda: Database(db_path))
    monkeypatch.delenv("MI_FITNESS_API_KEY", raising=False)
    monkeypatch.delenv("MI_FITNESS_ADMIN_KEY", raising=False)
    monkeypatch.setattr(api_module, "store_api_key_secret", lambda k, t: True)
    monkeypatch.setattr(api_module, "delete_api_key_secret", lambda k: None)
    monkeypatch.setattr(api_module, "load_api_key_secret", lambda k: None)
    with TestClient(api_module.app, base_url="http://127.0.0.1") as c:
        yield c


def window(client):
    last = client.get("/api/coverage").json()["data"][0]["last_date"]
    return {"start_date": last[:8] + "01", "end_date": last}


def test_demo_status_and_root(client):
    assert client.get("/api/status").json()["mode"] == "demo"
    assert "steps" in client.get("/").json()["metrics"]


def test_analytics_endpoints_return_data(client):
    w = window(client)
    assert client.get("/api/overview").json()["data"]["date"]
    assert client.get("/api/report", params=w).json()["data"]["metrics"]["total_steps"] >= 0
    assert client.get("/api/sleep-summary", params=w).json()["data"]["nights"] > 0
    assert client.get("/api/heart-rate-summary", params=w).json()["data"]["days"]
    assert client.get("/api/records").json()["data"]["step_goal"] == 8000
    assert client.get("/api/gaps", params=w).json()["count"] == 5


def test_metric_series_validation_and_new_metrics(client):
    w = window(client)
    ok = client.get("/api/metric-series", params={**w, "metric": "resting_heart_rate"})
    assert ok.status_code == 200 and ok.json()["count"] > 0
    assert client.get("/api/metric-series", params={**w, "metric": "bogus"}).status_code == 400
    assert client.get("/api/metric-series", params={**w, "metric": "steps", "granularity": "year"}).status_code == 400


def test_bad_dates_are_rejected_not_500(client):
    assert client.get("/api/summary", params={"start_date": "yesterday", "end_date": "2025-01-01"}).status_code == 422
    assert client.get("/api/summary", params={"start_date": "2025-13-45", "end_date": "2025-14-01"}).status_code == 400


def test_export_csv_and_json(client):
    w = window(client)
    csv_resp = client.get("/api/export", params={**w, "data_type": "daily_activity", "format": "csv"})
    assert csv_resp.status_code == 200 and csv_resp.text.startswith("﻿")
    assert "steps" in csv_resp.text.splitlines()[0]
    assert client.get("/api/export", params={**w, "data_type": "nope"}).status_code == 400


def test_static_api_key_is_enforced(client, monkeypatch):
    monkeypatch.setenv("MI_FITNESS_API_KEY", "s3cret")
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/overview", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_foreign_host_is_blocked(client):
    resp = client.get("/api/overview", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403


def test_admin_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("MI_FITNESS_ADMIN_KEY", "adm")
    assert client.get("/api/auth/keys").status_code == 401
    assert client.get("/api/auth/keys", headers={"X-Admin-Key": "adm"}).status_code == 200


def test_revoke_requires_a_unique_long_prefix(client):
    api_module._create_key_record(client.app.state.db, "u1", "tok", "cn", "a")
    api_module._create_key_record(client.app.state.db, "u2", "tok", "cn", "b")
    assert client.delete("/api/auth/keys/mif_").status_code == 400  # 过短：不会一次吊销全部
    keys = client.get("/api/auth/keys").json()["data"]
    assert all(not k["revoked"] for k in keys)
    first = keys[0]["prefix"]
    resp = client.delete(f"/api/auth/keys/{first}")
    assert resp.status_code == 200 and len(resp.json()["revoked"]) == 1
    remaining = [k for k in client.get("/api/auth/keys").json()["data"] if not k["revoked"]]
    assert len(remaining) == 1


def test_list_keys_exposes_prefix_for_the_dashboard(client):
    api_module._create_key_record(client.app.state.db, "u1", "tok", "cn", "lbl")
    item = client.get("/api/auth/keys").json()["data"][0]
    assert item["prefix"].startswith("mif_sk_") and "…" in item["key_masked"]
    assert "pass_token" not in item and "key" not in item
