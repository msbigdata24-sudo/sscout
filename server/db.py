from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from server.config import DB_PATH


def _parse_queries(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(q).strip() for q in raw if str(q).strip()]
    text = str(raw or "")
    out: list[str] = []
    for part in text.replace(",", "\n").split("\n"):
        q = part.strip()
        if q and q not in out:
            out.append(q)
    return out


def _status_label(status: str) -> str:
    labels = {
        "done": "готово",
        "error": "ошибка",
        "stopped": "остановлен",
        "running": "идёт",
        "pending": "ожидание",
    }
    return labels.get((status or "").lower(), status or "—")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_operator_name(raw: str) -> str:
    text = (raw or "").strip()
    return text if text else "не указан"


def _operator_sql_expr() -> str:
    return (
        "lower(trim(coalesce(nullif(trim(json_extract(brief_json, '$.operatorName')), ''), 'не указан')))"
    )


def _row_to_list_item(row: sqlite3.Row) -> dict[str, Any]:
    brief = json.loads(row["brief_json"])
    results = json.loads(row["results_json"] or "[]")
    queries = _parse_queries(brief.get("queries"))
    regions_raw = (brief.get("regions") or "").strip()
    region_list = [r.strip() for r in regions_raw.split(",") if r.strip()]
    return {
        "id": row["id"],
        "status": row["status"],
        "status_label": _status_label(row["status"]),
        "operator_name": normalize_operator_name(brief.get("operatorName") or ""),
        "client_name": brief.get("clientName", ""),
        "client_site": brief.get("clientSite", ""),
        "niche": (brief.get("niche") or "")[:120],
        "queries": queries,
        "queries_count": len(queries),
        "regions": region_list,
        "regions_count": len(region_list),
        "region_mode": brief.get("regionMode", "include"),
        "sites_count": len(results),
        "phones_count": sum(1 for r in results if r.get("p1")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_demo": bool(row["is_demo"]),
    }


class Database:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        brief_json TEXT NOT NULL,
                        pipeline_json TEXT,
                        results_json TEXT,
                        error TEXT,
                        is_demo INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def create_run(self, run_id: str, brief: dict[str, Any]) -> None:
        now = _utc_now()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO runs (id, status, brief_json, pipeline_json, results_json, error, is_demo, created_at, updated_at)
                    VALUES (?, 'pending', ?, '{}', '[]', NULL, 0, ?, ?)
                    """,
                    (run_id, json.dumps(brief, ensure_ascii=False), now, now),
                )
                conn.commit()
            finally:
                conn.close()

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        pipeline: dict[str, Any] | None = None,
        results: list[dict[str, Any]] | None = None,
        error: str | None = None,
        is_demo: bool | None = None,
        brief: dict[str, Any] | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if brief is not None:
            fields.append("brief_json = ?")
            values.append(json.dumps(brief, ensure_ascii=False))
        if pipeline is not None:
            fields.append("pipeline_json = ?")
            values.append(json.dumps(pipeline, ensure_ascii=False))
        if results is not None:
            fields.append("results_json = ?")
            values.append(json.dumps(results, ensure_ascii=False))
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if is_demo is not None:
            fields.append("is_demo = ?")
            values.append(1 if is_demo else 0)
        values.append(run_id)
        sql = f"UPDATE runs SET {', '.join(fields)} WHERE id = ?"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, values)
                conn.commit()
            finally:
                conn.close()

    def find_active_run(self, client_site: str) -> dict[str, Any] | None:
        site = (client_site or "").strip().lower().rstrip("/")
        if not site:
            return None
        for it in self.list_runs(30):
            cs = (it.get("client_site") or "").strip().lower().rstrip("/")
            if cs != site:
                continue
            if it.get("status") in ("running", "pending"):
                return it
        return None

    def count_runs(self, operator: str | None = None) -> int:
        with self._lock:
            conn = self._connect()
            try:
                if operator:
                    row = conn.execute(
                        f"SELECT COUNT(*) AS c FROM runs WHERE {_operator_sql_expr()} = lower(trim(?))",
                        (normalize_operator_name(operator),),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
                return int(row["c"]) if row else 0
            finally:
                conn.close()

    def list_runs(self, limit: int = 50, operator: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                if operator:
                    rows = conn.execute(
                        "SELECT id, status, brief_json, results_json, created_at, updated_at, is_demo "
                        f"FROM runs WHERE {_operator_sql_expr()} = lower(trim(?)) "
                        "ORDER BY created_at DESC LIMIT ?",
                        (normalize_operator_name(operator), limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, status, brief_json, results_json, created_at, updated_at, is_demo "
                        "FROM runs ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            finally:
                conn.close()
        return [_row_to_list_item(row) for row in rows]

    def run_operator_name(self, run_id: str) -> str | None:
        run = self.get_run(run_id)
        if not run:
            return None
        brief = run.get("brief") or {}
        return normalize_operator_name(brief.get("operatorName") or "")

    def operator_can_access_run(self, run_id: str, operator: str) -> bool:
        run_op = self.run_operator_name(run_id)
        if run_op is None:
            return False
        return run_op.casefold() == normalize_operator_name(operator).casefold()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "brief": json.loads(row["brief_json"]),
            "pipeline": json.loads(row["pipeline_json"] or "{}"),
            "results": json.loads(row["results_json"] or "[]"),
            "error": row["error"],
            "is_demo": bool(row["is_demo"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


db = Database()
