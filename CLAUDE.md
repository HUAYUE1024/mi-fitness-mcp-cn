# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e '.[all,dev]'          # all extras (FastAPI, Flask) + dev tools
ruff check src tests                 # lint (CI runs this; E501 is ignored, line length 100)
pytest -q                            # whole suite, fully offline
pytest tests/test_storage.py::test_legacy_database_is_migrated_without_data_loss   # one test
python -m build                      # sdist + wheel

mi-fitness-mcp dashboard --demo      # API + web UI on synthetic data, no Xiaomi account needed
mi-fitness-mcp overview --demo       # any read-only CLI command takes --demo / --json
```

`asyncio_mode = "auto"` in `pyproject.toml`, so `async def test_*` needs no marker. Tests never touch the network or the real keyring; use `--demo` / `generate_demo_data` for fixtures instead of mocking the cloud.

## Architecture

One local SQLite database sits between a Xiaomi cloud adapter (write side) and four front-ends (read side): MCP server, REST API, CLI, web dashboard. Front-ends never talk to Xiaomi except to sync.

- **`storage/schema.py`** is the single source of truth for the eight data tables (`TABLES`). It generates DDL, the upsert column list, the date-range filter and coverage queries. Adding a data type means adding one `_table(...)` entry here plus a model, an adapter `iter_*` method, and an entry in `models.DATA_TYPES` and `sync_service.ADAPTER_ITERATORS`.
- **`storage/__init__.py`** (`Database`) does the I/O. `upsert()` is the only write path: one transaction, returns accurate `added/updated/skipped`. Primary key is `(user_id, id)`; `_init_db` migrates databases from before that (v0.2.x) automatically. Time columns hold local-offset ISO strings, so a date range is a string range on the column (`>= start`, `< day after end`) and uses the `(user_id, time)` index. Do not wrap the column in `substr()`/`date()` in a WHERE.
- **`services/sync_service.py`** chunks a date range (default 7 days), upserts each chunk, and only then advances the per-account cursor in `sync_cursor` (cursor never moves backwards). With no start date it re-pulls from the cursor's *day*, not its timestamp. `sync_many` is shared by MCP and REST. Overlapping syncs are rejected, not queued.
- **`services/query_service.py` -> `analytics.py`**: `QueryService` reads and builds time series (`SERIES_METRICS` maps a metric name to table/column/natural aggregation); `AnalyticsService` subclasses it with reports, personal records, sleep summary and gap detection. Every front-end instantiates `AnalyticsService`.
- **`adapters/mi_fitness_cloud.py`** is the reverse-engineered Xiaomi protocol (passToken login, RC4 + SHA signatures, paginated `get_fitness_data_by_time`). Record IDs are deterministic (`mi_fitness_hr_<ts>`), which is what makes re-syncing idempotent. `get_available_data_types()` just returns `DATA_TYPES`; the cloud has no capability discovery.
- **`server.py`** is the MCP server. Tools are registered with the `@tool(name, description, properties, required)` decorator into the `TOOLS` registry, so schema and handler live together. `call_tool` turns `ValueError`/`KeyError` into `{"status": "error"}` payloads. Module-level globals (`query_service`, `adapter`, ...) are set in `main()`; tests monkeypatch them.
- **`api.py`** is FastAPI. Auth: `_gate` (X-API-Key) on every route, `_require_admin` on key/QR routes. Per-key contexts (`UserContext`) cache an adapter + services; issued keys keep their passToken in the OS keyring (`security.py`), not SQLite. `ValueError` from services becomes HTTP 400 via one exception handler.
- **`web.py`** is a Flask reverse proxy plus the static dashboard in `web_assets/`. It adds a strict CSP (`script-src 'self'`, `frame-ancestors 'none'`): never add inline `<script>` or inline event handlers, they will silently do nothing. The dashboard front-end is framework-free ES modules: `html`` in `js/lib/dom.js` escapes interpolations (mark trusted markup with `raw()`), `charts.js` renders SVG with no dependency, and each page is a `views/*.js` module with `init`/`load`. `lib/api.js` unwraps the API's `{status,count,data}` envelope, so views receive the bare `data`.
- **`demo.py`** writes deterministic synthetic data through the real `Database.upsert`, into a separate `demo.db`. `MI_FITNESS_DEMO=1` makes the API use it.

## Conventions worth knowing

- Security defaults are deliberate (loopback bind, Host allowlist, restrictive CORS, constant-time key compare, keyring for secrets). See `docs/PRIVACY.md` before changing anything that adds an outbound request or stores a secret.
- Missing data is not zero. Analytics return `None`/empty for absent data, and `find_data_gaps` exists so callers can tell "not worn / not synced" from "value 0". Keep that distinction in new features.
- `mcp` is pinned `<2` (the 2.x SDK removed `Server.list_tools`).
- User-facing strings (CLI, errors, README) are Chinese; code and identifiers are English.
