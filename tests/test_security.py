"""Tests for local security hardening (host allowlist, CORS, keyring secrets, CSV export)."""

import keyring

from mi_fitness_mcp.api import _to_csv
from mi_fitness_mcp.security import (
    default_allowed_hosts,
    delete_api_key_secret,
    host_allowed,
    load_api_key_secret,
    store_api_key_secret,
)


def test_host_allowed_strips_ports():
    allowed = default_allowed_hosts()
    assert host_allowed("127.0.0.1:8321", allowed)
    assert host_allowed("localhost:9999", allowed)
    assert host_allowed("LOCALHOST", allowed)
    assert host_allowed("[::1]:8321", allowed)


def test_host_allowed_rejects_foreign_domains():
    allowed = default_allowed_hosts()
    # DNS 重绑定场景：外部域名解析到 127.0.0.1，但 Host 头仍是外部域名
    assert not host_allowed("evil.example.com", allowed)
    assert not host_allowed("evil.example.com:8321", allowed)
    assert not host_allowed(None, allowed)
    assert not host_allowed("", allowed)


def test_host_allowed_env_override():
    # monkeypatch env via keyring-free approach: direct set check
    import os

    old = os.environ.get("MI_FITNESS_ALLOWED_HOSTS")
    try:
        os.environ["MI_FITNESS_ALLOWED_HOSTS"] = "lanhost.local,*"
        allowed = default_allowed_hosts()
        assert host_allowed("lanhost.local:8321", allowed)
        assert host_allowed("anything.com", allowed)  # * 放行
    finally:
        if old is None:
            os.environ.pop("MI_FITNESS_ALLOWED_HOSTS", None)
        else:
            os.environ["MI_FITNESS_ALLOWED_HOSTS"] = old


def test_api_key_secret_roundtrip(monkeypatch):
    store = {}

    monkeypatch.setattr(keyring, "set_password", lambda s, a, v: store.__setitem__((s, a), v))
    monkeypatch.setattr(keyring, "get_password", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda s, a: store.pop((s, a), None),
    )

    api_key = "mif_sk_" + "a" * 40
    assert store_api_key_secret(api_key, "V1:secret-token")
    assert load_api_key_secret(api_key) == "V1:secret-token"
    # 不同 Key 的存储隔离（账号名含 key 哈希）
    other = "mif_sk_" + "b" * 40
    assert load_api_key_secret(other) is None
    delete_api_key_secret(api_key)
    assert load_api_key_secret(api_key) is None


def test_store_api_key_secret_keyring_failure(monkeypatch):
    def boom(_s, _a, _v):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(keyring, "set_password", boom)
    assert not store_api_key_secret("mif_sk_x", "tok")
    assert load_api_key_secret("mif_sk_x") is None


def test_to_csv_flattens_and_adds_bom():
    rows = [
        {"date": "2026-08-30", "steps": 100, "stages": [{"stage": "deep", "minutes": 60}]},
        {"date": "2026-08-31", "steps": None, "stages": []},
    ]
    csv_text = _to_csv(rows)
    assert csv_text.startswith("\ufeff")
    assert "date,steps,stages" in csv_text
    # CSV 规范内层引号转义为两个引号
    assert '"[{""stage"": ""deep"", ""minutes"": 60}]"' in csv_text
    assert "2026-08-31" in csv_text
