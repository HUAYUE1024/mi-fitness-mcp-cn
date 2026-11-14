"""MCP server: tool registry, schema sanity and the new analytics tools on demo data."""

import json

import pytest

from mi_fitness_mcp import server
from mi_fitness_mcp.demo import DEMO_USER_ID, generate_demo_data
from mi_fitness_mcp.services.analytics import AnalyticsService
from mi_fitness_mcp.storage import Database

EXPECTED_TOOLS = {
    "get_connection_status", "sync_data", "get_sync_status", "get_profile", "get_daily_summary",
    "query_metric_series", "query_heart_rate", "query_body_measurements", "query_sleep",
    "query_workouts", "query_spo2", "query_stress", "query_abnormal_heart_beat", "get_data_coverage",
    "get_health_overview", "get_period_report", "get_sleep_summary", "get_heart_rate_summary",
    "get_personal_records", "find_data_gaps",
}


@pytest.fixture
def demo_server(tmp_path, monkeypatch):
    db = Database(tmp_path / "mcp.db")
    generate_demo_data(db, days=45)
    monkeypatch.setattr(server, "query_service", AnalyticsService(db, DEMO_USER_ID))
    monkeypatch.setattr(server, "demo_mode", True)
    monkeypatch.setattr(server, "config", None)
    return db


async def call(name, arguments=None):
    (content,) = await server.call_tool(name, arguments)
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_every_tool_is_registered_with_a_valid_schema():
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description and len(t.description) >= 8, f"{t.name} 缺少有用的描述"
        schema = t.inputSchema
        assert schema["type"] == "object"
        assert set(schema.get("required", [])) <= set(schema["properties"]), t.name


@pytest.mark.asyncio
async def test_metric_enum_matches_what_the_service_supports():
    from mi_fitness_mcp.services.query_service import SERIES_METRICS

    spec = next(t for t in await server.list_tools() if t.name == "query_metric_series")
    assert spec.inputSchema["properties"]["metric"]["enum"] == list(SERIES_METRICS)


@pytest.mark.asyncio
async def test_overview_and_report_tools(demo_server):
    overview = await call("get_health_overview")
    assert overview["status"] == "ok" and overview["data"]["last_7_days"]["days_with_data"] > 0
    coverage = (await call("get_data_coverage"))["data"]["coverage"]
    last = max(c["last_date"] for c in coverage)
    first = last[:8] + "01"
    report = await call("get_period_report", {"start_date": first, "end_date": last})
    assert "metrics" in report["data"]
    gaps = await call("find_data_gaps", {"start_date": first, "end_date": last, "data_types": ["sleep"]})
    assert gaps["data"]["gaps"][0]["data_type"] == "sleep"


@pytest.mark.asyncio
async def test_bad_arguments_become_errors_not_crashes(demo_server):
    missing = await call("get_period_report", {"start_date": "2025-01-01"})
    assert missing["status"] == "error" and "end_date" in missing["error"]
    bad_date = await call("get_sleep_summary", {"start_date": "soon", "end_date": "2025-01-01"})
    assert bad_date["status"] == "error" and "YYYY-MM-DD" in bad_date["error"]
    bad_metric = await call(
        "query_metric_series", {"metric": "nope", "start_date": "2025-01-01", "end_date": "2025-01-02"}
    )
    assert bad_metric["status"] == "error"
    assert (await call("no_such_tool"))["status"] == "error"


@pytest.mark.asyncio
async def test_demo_mode_blocks_cloud_sync_and_reports_mode(demo_server):
    assert (await call("sync_data"))["status"] == "error"
    assert (await call("get_connection_status"))["mode"] == "demo"
    assert (await call("get_profile"))["data"]["profile"]["user_id"] == DEMO_USER_ID


@pytest.mark.asyncio
async def test_non_ascii_survives_json_encoding(demo_server):
    (content,) = await server.call_tool("get_connection_status", None)
    assert "演示模式" in content.text  # ensure_ascii=False：模型看到的是汉字而不是 \uXXXX
