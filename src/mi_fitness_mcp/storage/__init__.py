"""SQLite storage layer. Table shapes live in `schema.py`; this module does the I/O."""

import contextlib
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mi_fitness_mcp.storage.schema import COMMON_COLUMNS, TABLES, Table

UPSERT_CHUNK = 400  # 2 bound params per row for the existence probe stays far below SQLite limits
LEGACY_TABLE_SUFFIX = "_legacy_v2"


def validate_date(value: str, name: str = "date") -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为 YYYY-MM-DD 格式，收到 {value!r}") from exc
    return value


def _next_day(value: str) -> str:
    return (datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


class Database:
    """SQLite database manager (WAL mode, one short-lived connection per call)."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ schema

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.db_path.exists()
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for table in TABLES.values():
                self._migrate_legacy_table(conn, table)
                conn.execute(table.ddl())
                conn.execute(table.index_ddl())
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_cursor (
                    user_id TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    last_sync_at TIMESTAMP,
                    last_record_timestamp TIMESTAMP,
                    PRIMARY KEY (user_id, data_type)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key TEXT PRIMARY KEY,
                    label TEXT,
                    user_id TEXT NOT NULL,
                    pass_token TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'cn',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._migrate_legacy_cursor(conn)
            conn.commit()
        if fresh:
            # 健康数据属于敏感文件：仅当前用户可读写（Windows 上 chmod 基本无效，忽略即可）
            with contextlib.suppress(OSError):
                os.chmod(self.db_path, 0o600)

    @staticmethod
    def _migrate_legacy_table(conn: sqlite3.Connection, table: Table) -> None:
        """v0.2 之前主键只有 id，多账号会互相覆盖；改为 (user_id, id) 并保留全部数据。"""
        info = conn.execute(f"PRAGMA table_info({table.name})").fetchall()
        if not info:
            return
        pk_cols = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
        if pk_cols == ["user_id", "id"]:
            return
        legacy = table.name + LEGACY_TABLE_SUFFIX
        conn.execute(f"DROP TABLE IF EXISTS {legacy}")
        conn.execute(f"ALTER TABLE {table.name} RENAME TO {legacy}")
        conn.execute(table.ddl())
        cols = ", ".join((*table.insert_columns, "created_at", "updated_at"))
        conn.execute(f"INSERT OR REPLACE INTO {table.name} ({cols}) SELECT {cols} FROM {legacy}")
        conn.execute(f"DROP TABLE {legacy}")

    @staticmethod
    def _migrate_legacy_cursor(conn: sqlite3.Connection) -> None:
        """旧 sync_state 不区分账号：库里只有一个账号的数据时，把游标归给该账号，否则丢弃。"""
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'"
        ).fetchone():
            return
        for row in conn.execute("SELECT * FROM sync_state").fetchall():
            table = TABLES.get(row["data_type"])
            if table is None:
                continue
            users = conn.execute(f"SELECT DISTINCT user_id FROM {table.name} LIMIT 2").fetchall()
            if len(users) == 1:
                conn.execute(
                    "INSERT OR IGNORE INTO sync_cursor VALUES (?, ?, ?, ?)",
                    (
                        users[0]["user_id"],
                        row["data_type"],
                        row["last_sync_at"],
                        row["last_record_timestamp"],
                    ),
                )
        conn.execute("DROP TABLE sync_state")

    # ------------------------------------------------------------------ writes

    def upsert(self, data_type: str, records: Iterable[BaseModel]) -> dict[str, int]:
        """批量幂等写入，一个事务完成。

        返回 {"added": 新增, "updated": 内容有变化的已有记录, "skipped": 完全相同或批内重复}。
        """
        table = TABLES[data_type]
        unique: dict[tuple[str, str], BaseModel] = {}
        total = 0
        for record in records:
            total += 1
            unique[(record.user_id, record.id)] = record
        stats = {"added": 0, "updated": 0, "skipped": total - len(unique)}
        if not unique:
            return stats

        sql = self._upsert_sql(table)
        batch = list(unique.items())
        with self._get_connection() as conn:
            for i in range(0, len(batch), UPSERT_CHUNK):
                chunk = batch[i : i + UPSERT_CHUNK]
                probe = ",".join(["(?,?)"] * len(chunk))
                existing = conn.execute(
                    f"SELECT COUNT(*) FROM {table.name} WHERE (user_id, id) IN (VALUES {probe})",
                    [part for key, _ in chunk for part in key],
                ).fetchone()[0]
                before = conn.total_changes
                conn.executemany(sql, [table.encode(rec) for _, rec in chunk])
                changed = conn.total_changes - before
                added = len(chunk) - existing
                updated = max(0, changed - added)
                stats["added"] += added
                stats["updated"] += updated
                stats["skipped"] += existing - updated
            conn.commit()
        return stats

    @staticmethod
    def _upsert_sql(table: Table) -> str:
        cols = table.insert_columns
        mutable = [c for c in cols if c not in ("id", "user_id")]
        return (
            f"INSERT INTO {table.name} ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(user_id, id) DO UPDATE SET "
            f"{', '.join(f'{c} = excluded.{c}' for c in mutable)}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE ({', '.join(mutable)}) IS NOT ({', '.join(f'excluded.{c}' for c in mutable)})"
        )

    def update_sync_state(
        self, user_id: str, data_type: str, last_record_ts: datetime | str | None = None
    ) -> None:
        """记录同步时间；游标只前进不后退（补拉历史区间不会把增量起点拉回去）。"""
        ts = (
            last_record_ts.isoformat() if isinstance(last_record_ts, datetime) else last_record_ts
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_cursor (user_id, data_type, last_sync_at, last_record_timestamp)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(user_id, data_type) DO UPDATE SET
                    last_sync_at = CURRENT_TIMESTAMP,
                    last_record_timestamp = MAX(
                        COALESCE(last_record_timestamp, ''), COALESCE(excluded.last_record_timestamp, '')
                    )
                """,
                (user_id, data_type, ts),
            )
            conn.commit()

    # ------------------------------------------------------------------- reads

    def get_sync_state(self, user_id: str, data_type: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sync_cursor WHERE user_id = ? AND data_type = ?",
                (user_id, data_type),
            ).fetchone()
        if row is None:
            return None
        state = dict(row)
        state["last_record_timestamp"] = state["last_record_timestamp"] or None
        return state

    @staticmethod
    def _range_filter(
        table: Table,
        user_id: str,
        start_date: str,
        end_date: str,
        where: dict[str, Any] | None,
    ) -> tuple[str, list[Any]]:
        """日期闭区间 + 等值过滤的 WHERE 子句。列名只接受表内已知字段，值一律参数化。"""
        validate_date(start_date, "start_date")
        validate_date(end_date, "end_date")
        clauses = ["user_id = ?", f"{table.time_col} >= ?", f"{table.time_col} < ?"]
        params: list[Any] = [user_id, start_date, _next_day(end_date)]
        for key, value in (where or {}).items():
            if key not in table.field_names:
                raise ValueError(f"{table.data_type} 没有可过滤的字段 {key!r}")
            clauses.append(f"{key} = ?")
            params.append(value)
        return " AND ".join(clauses), params

    def query(
        self,
        data_type: str,
        user_id: str,
        start_date: str,
        end_date: str,
        *,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        """按日期闭区间读取。时间列保存本地时区 ISO 串，字符串区间比较即可走 (user_id, 时间) 索引。"""
        table = TABLES[data_type]
        where_sql, params = self._range_filter(table, user_id, start_date, end_date, where)
        sql = (
            f"SELECT * FROM {table.name} WHERE {where_sql} "
            f"ORDER BY {table.time_col} {'DESC' if descending else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._get_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def aggregate(
        self,
        data_type: str,
        user_id: str,
        start_date: str,
        end_date: str,
        column: str,
        where: dict[str, Any] | None = None,
        group: str | None = None,
    ) -> list[dict[str, Any]]:
        """数值列的 count/sum/min/avg/max，在 SQL 里完成，避免把逐分钟样本读进内存。

        group 取 None（整段一行）、"day" 或 "hour"。
        """
        table = TABLES[data_type]
        if column not in table.field_names:
            raise ValueError(f"{data_type} 没有字段 {column!r}")
        if group not in (None, "day", "hour"):
            raise ValueError("group 必须是 None、'day' 或 'hour'")
        where_sql, params = self._range_filter(table, user_id, start_date, end_date, where)
        stats = (
            f"COUNT({column}) AS count, SUM({column}) AS sum, MIN({column}) AS min, "
            f"ROUND(AVG({column}), 1) AS avg, MAX({column}) AS max"
        )
        if group is None:
            sql = f"SELECT {stats} FROM {table.name} WHERE {where_sql}"
        else:
            span = 10 if group == "day" else 13
            sql = (
                f"SELECT substr({table.time_col}, 1, {span}) AS bucket, {stats} "
                f"FROM {table.name} WHERE {where_sql} GROUP BY bucket ORDER BY bucket"
            )
        with self._get_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def bucket_counts(
        self,
        data_type: str,
        user_id: str,
        start_date: str,
        end_date: str,
        column: str,
        edges: tuple[tuple[str, float, float], ...],
        where: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """按 [low, high) 区间统计某数值列的样本数，一次扫描完成（edges 为 (名称, 下界, 上界)）。"""
        table = TABLES[data_type]
        if column not in table.field_names:
            raise ValueError(f"{data_type} 没有字段 {column!r}")
        where_sql, params = self._range_filter(table, user_id, start_date, end_date, where)
        cases = ", ".join(
            f"SUM(CASE WHEN {column} >= ? AND {column} < ? THEN 1 ELSE 0 END)" for _ in edges
        )
        bounds = [bound for _, low, high in edges for bound in (low, high)]
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {cases} FROM {table.name} WHERE {where_sql}",
                [*bounds, *params],
            ).fetchone()
        return {name: int(row[i] or 0) for i, (name, _, _) in enumerate(edges)}

    def count(self, data_type: str, user_id: str, start_date: str, end_date: str) -> int:
        table = TABLES[data_type]
        validate_date(start_date, "start_date")
        validate_date(end_date, "end_date")
        with self._get_connection() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table.name} "
                f"WHERE user_id = ? AND {table.time_col} >= ? AND {table.time_col} < ?",
                (user_id, start_date, _next_day(end_date)),
            ).fetchone()[0]

    def get_data_coverage(self, user_id: str) -> list[dict[str, Any]]:
        """每类数据的首/末日期、有数据的天数与记录条数。"""
        results = []
        with self._get_connection() as conn:
            for data_type, table in TABLES.items():
                expr = f"substr({table.time_col}, 1, 10)"
                row = conn.execute(
                    f"""
                    SELECT MIN({expr}) AS first_date, MAX({expr}) AS last_date,
                           COUNT(DISTINCT {expr}) AS days_with_data, COUNT(*) AS record_count
                    FROM {table.name} WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
                if row["first_date"]:
                    results.append({"data_type": data_type, **dict(row)})
        return results

    def distinct_dates(
        self, data_type: str, user_id: str, start_date: str, end_date: str
    ) -> list[str]:
        table = TABLES[data_type]
        validate_date(start_date, "start_date")
        validate_date(end_date, "end_date")
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT substr({table.time_col}, 1, 10) AS d FROM {table.name} "
                f"WHERE user_id = ? AND {table.time_col} >= ? AND {table.time_col} < ? ORDER BY d",
                (user_id, start_date, _next_day(end_date)),
            ).fetchall()
        return [r["d"] for r in rows]

    def top(
        self,
        data_type: str,
        user_id: str,
        column: str,
        *,
        descending: bool = True,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """某数值列（忽略 NULL 与 0）的极值所在整行，用于个人纪录。"""
        table = TABLES[data_type]
        if column not in table.field_names:
            raise ValueError(f"{data_type} 没有字段 {column!r}")
        clauses = ["user_id = ?", f"{column} > 0"]
        params: list[Any] = [user_id]
        for key, value in (where or {}).items():
            if key not in table.field_names:
                raise ValueError(f"{data_type} 没有可过滤的字段 {key!r}")
            clauses.append(f"{key} = ?")
            params.append(value)
        order = "DESC" if descending else "ASC"
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {table.name} WHERE {' AND '.join(clauses)} "
                f"ORDER BY {column} {order} LIMIT 1",
                params,
            ).fetchone()
        return dict(row) if row else None

    def table_stats(self) -> dict[str, dict[str, Any]]:
        """每张表的总行数与账号数（status 命令用，不区分账号）。"""
        stats = {}
        with self._get_connection() as conn:
            for data_type, table in TABLES.items():
                row = conn.execute(
                    f"SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS users FROM {table.name}"
                ).fetchone()
                stats[data_type] = {"records": row["n"], "users": row["users"]}
        return stats

    # --------------------------------------------------------------- API keys

    def api_key_row(self, key: str) -> sqlite3.Row | None:
        with self._get_connection() as conn:
            return conn.execute("SELECT * FROM api_keys WHERE key = ?", (key,)).fetchone()


__all__ = ["COMMON_COLUMNS", "TABLES", "Database", "validate_date"]
