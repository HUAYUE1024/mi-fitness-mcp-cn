"""CLI smoke tests on demo data: every read-only command runs end to end and prints sensible output."""

import json

import pytest

from mi_fitness_mcp import demo as demo_module
from mi_fitness_mcp.main import build_parser, main
from mi_fitness_mcp.storage import Database


@pytest.fixture(autouse=True)
def demo_db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    monkeypatch.setattr(demo_module, "demo_database", lambda: _fresh(path))


def _fresh(path):
    db = Database(path)
    demo_module.generate_demo_data(db, days=60)
    return db


def run(capsys, *argv):
    main(list(argv))
    return capsys.readouterr().out


def test_status_lists_every_data_type(capsys):
    out = run(capsys, "status", "--demo")
    for name in ("daily_activity", "heart_rate", "sleep", "spo2", "stress"):
        assert name in out


def test_overview_text_and_json(capsys):
    assert "健康快照" in run(capsys, "overview", "--demo")
    data = json.loads(run(capsys, "overview", "--demo", "--json"))
    assert data["last_7_days"]["days_with_data"] > 0


def test_report_week_and_month(capsys):
    assert "总步数" in run(capsys, "report", "--demo")
    month = json.loads(run(capsys, "report", "--demo", "--period", "month", "--json"))
    assert month["period"]["days"] == 30


def test_records_and_gaps(capsys):
    assert "个人纪录" in run(capsys, "records", "--demo", "--step-goal", "5000")
    assert "数据缺口" in run(capsys, "gaps", "--demo", "--type", "sleep")


def test_export_csv_to_file(tmp_path, capsys):
    target = tmp_path / "out.csv"
    main(["export", "--demo", "--type", "daily_activity", "-o", str(target)])
    text = target.read_text(encoding="utf-8")
    assert text.startswith("﻿") and "steps" in text.splitlines()[0]


def test_bad_date_exits_with_message_not_traceback(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["report", "--demo", "--start-date", "tomorrow", "--end-date", "2025-01-01"])
    assert exc.value.code == 1
    assert "YYYY-MM-DD" in capsys.readouterr().out


def test_parser_covers_every_command_and_version():
    parser = build_parser()
    names = set(parser._subparsers._group_actions[0].choices)
    assert {"serve", "setup", "doctor", "sync", "status", "overview", "report", "records",
            "gaps", "export", "api", "web", "dashboard"} <= names
