"""
SQLite-based time-series metrics store with automatic 7-day retention.

Stores snapshots (collection runs), individual metric data points for
time-series graphing, and full check results for drill-down.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "metrics.db",
)
RETENTION_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT NOT NULL DEFAULT 'default',
    timestamp TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    cluster_info TEXT,
    total_duration_sec REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS time_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    cluster_id TEXT NOT NULL DEFAULT 'default',
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    check_name TEXT NOT NULL,
    item_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    status TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    items TEXT,
    duration_sec REAL,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL,
    check_name TEXT NOT NULL,
    item_name TEXT,
    message TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    acknowledged_at TEXT,
    resolved_at TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ts_timestamp ON time_series(timestamp);
CREATE INDEX IF NOT EXISTS idx_ts_category ON time_series(category);
CREATE INDEX IF NOT EXISTS idx_ts_metric ON time_series(metric_name);
CREATE INDEX IF NOT EXISTS idx_ts_item ON time_series(item_name);
CREATE INDEX IF NOT EXISTS idx_ts_created ON time_series(created_at);
CREATE INDEX IF NOT EXISTS idx_ts_cluster ON time_series(cluster_id);
-- Composite index for /api/overview's per-snapshot metric query.
-- Without this, that query scans ~50K rows of the matching metric_name
-- across all snapshots and filters by snapshot_id post-hoc — pegs to
-- ~10s on a 1GB+ DB and freezes the asyncio loop.
CREATE INDEX IF NOT EXISTS idx_ts_snap_metric ON time_series(snapshot_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_snap_created ON snapshots(created_at);
CREATE INDEX IF NOT EXISTS idx_snap_cluster ON snapshots(cluster_id);
CREATE INDEX IF NOT EXISTS idx_cr_snapshot ON check_results(snapshot_id);
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT '>',
    warning REAL,
    critical REAL,
    item_pattern TEXT DEFAULT '',
    message TEXT DEFAULT '',
    notify TEXT DEFAULT 'both',
    channels TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'read',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    webhook_url TEXT NOT NULL,
    categories TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS datasources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    scrape_interval INTEGER DEFAULT 30,
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_cluster ON alerts(cluster_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
"""


class MetricsStore:
    """Thread-safe SQLite metrics store with 7-day retention."""

    def __init__(self, db_path: str = None, retention_days: int = RETENTION_DAYS):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.retention_days = retention_days
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # Migrations — add columns to existing tables
            self._migrate(conn)

    def _migrate(self, conn):
        """Add missing columns to existing tables (safe for fresh and existing DBs)."""
        migrations = [
            ("alert_rules", "channels", "TEXT DEFAULT ''"),
            ("alert_rules", "notify", "TEXT DEFAULT 'both'"),
        ]
        for table, column, col_type in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except Exception:
                pass  # Column already exists

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Write operations ─────────────────────────────────────────────────

    def save_snapshot(self, report: Dict[str, Any], cluster_id: str = "default") -> int:
        """
        Save a full QC report. Extracts individual metrics into time_series
        for efficient time-based queries. Returns the snapshot ID.
        """
        now = time.time()
        timestamp = report.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        overall = report.get("overall_status", "UNKNOWN")
        cluster_info = json.dumps(report.get("cluster_info", {}))
        duration = report.get("total_duration_sec", 0)

        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO snapshots (cluster_id, timestamp, overall_status, cluster_info, total_duration_sec, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cluster_id, timestamp, overall, cluster_info, duration, now),
                )
                snap_id = cursor.lastrowid

                # Save check results
                for check in report.get("checks", []):
                    conn.execute(
                        "INSERT INTO check_results (snapshot_id, name, category, status, summary, items, duration_sec) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            snap_id,
                            check.get("name", ""),
                            check.get("category", ""),
                            check.get("status", "UNKNOWN"),
                            check.get("summary", ""),
                            json.dumps(check.get("items", [])),
                            check.get("duration_sec", 0),
                        ),
                    )

                    # Extract numeric metrics into time_series
                    for item in check.get("items", []):
                        metrics = item.get("metrics", {})
                        for metric_name, metric_value in metrics.items():
                            if isinstance(metric_value, (int, float)) and metric_value != -1:
                                conn.execute(
                                    "INSERT INTO time_series "
                                    "(snapshot_id, cluster_id, timestamp, category, check_name, item_name, "
                                    "metric_name, metric_value, status, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        snap_id,
                                        cluster_id,
                                        timestamp,
                                        check.get("category", ""),
                                        check.get("name", ""),
                                        item.get("name", ""),
                                        metric_name,
                                        float(metric_value),
                                        item.get("status", "UNKNOWN"),
                                        now,
                                    ),
                                )

                return snap_id

    def cleanup_old_data(self):
        """Remove data older than retention_days."""
        cutoff = time.time() - (self.retention_days * 86400)
        with self._lock:
            with self._connect() as conn:
                # Get old snapshot IDs
                rows = conn.execute(
                    "SELECT id FROM snapshots WHERE created_at < ?", (cutoff,)
                ).fetchall()
                old_ids = [r["id"] for r in rows]
                if not old_ids:
                    return 0

                placeholders = ",".join("?" * len(old_ids))
                conn.execute(f"DELETE FROM time_series WHERE snapshot_id IN ({placeholders})", old_ids)
                conn.execute(f"DELETE FROM check_results WHERE snapshot_id IN ({placeholders})", old_ids)
                conn.execute(f"DELETE FROM snapshots WHERE id IN ({placeholders})", old_ids)
                log.info("Cleaned up %d old snapshots", len(old_ids))
                return len(old_ids)

    # ── Read operations ──────────────────────────────────────────────────

    def save_alert(self, cluster_id: str, severity: str, check_name: str,
                   item_name: str, message: str):
        """Save an alert record."""
        now = time.time()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO alerts (cluster_id, timestamp, severity, check_name, item_name, message, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cluster_id, ts, severity, check_name, item_name or "", message, now),
                )

    def get_active_alerts(self, cluster_id: str = None) -> List[Dict]:
        """Return active (unresolved) alerts."""
        with self._connect() as conn:
            if cluster_id:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE status='active' AND cluster_id=? ORDER BY created_at DESC", (cluster_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE status='active' ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def resolve_alerts(self, cluster_id: str, check_name: str, item_name: str = None):
        """Resolve alerts. If item_name given, resolve only that item."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._connect() as conn:
                if item_name is not None:
                    conn.execute(
                        "UPDATE alerts SET status='resolved', resolved_at=? "
                        "WHERE cluster_id=? AND check_name=? AND item_name=? AND status='active'",
                        (ts, cluster_id, check_name, item_name),
                    )
                else:
                    conn.execute(
                        "UPDATE alerts SET status='resolved', resolved_at=? "
                        "WHERE cluster_id=? AND check_name=? AND status='active'",
                        (ts, cluster_id, check_name),
                    )

    def clear_all_active_alerts(self):
        """Resolve all active alerts (used for cleanup)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE alerts SET status='resolved', resolved_at=? WHERE status='active'", (ts,))

    # ── Notification Channels ────────────────────────────────────────

    def create_notification_channel(self, name: str, webhook_url: str, categories: str = "", enabled: bool = True) -> int:
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO notification_channels (name, webhook_url, categories, enabled, created_at) VALUES (?,?,?,?,?)",
                    (name, webhook_url, categories, 1 if enabled else 0, now))
                return cursor.lastrowid

    def update_notification_channel(self, ch_id: int, name: str, webhook_url: str, categories: str, enabled: bool):
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE notification_channels SET name=?, webhook_url=?, categories=?, enabled=? WHERE id=?",
                    (name, webhook_url, categories, 1 if enabled else 0, ch_id))

    def delete_notification_channel(self, ch_id: int):
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM notification_channels WHERE id=?", (ch_id,))

    def get_notification_channels(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notification_channels ORDER BY id").fetchall()
            return [{"id": r["id"], "name": r["name"], "webhook_url": r["webhook_url"],
                     "categories": r["categories"], "enabled": bool(r["enabled"])} for r in rows]

    # ── Data Sources ──────────────────────────────────────────────────

    def save_datasource(self, name: str, ds_type: str, url: str, interval: int = 30, enabled: bool = True) -> int:
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                # Upsert by name
                existing = conn.execute("SELECT id FROM datasources WHERE name=?", (name,)).fetchone()
                if existing:
                    conn.execute("UPDATE datasources SET url=?, scrape_interval=?, enabled=? WHERE id=?",
                                 (url, interval, 1 if enabled else 0, existing["id"]))
                    return existing["id"]
                cursor = conn.execute(
                    "INSERT INTO datasources (name, type, url, scrape_interval, enabled, created_at) VALUES (?,?,?,?,?,?)",
                    (name, ds_type, url, interval, 1 if enabled else 0, now))
                return cursor.lastrowid

    def get_datasources(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM datasources ORDER BY id").fetchall()
            return [{"id": r["id"], "name": r["name"], "type": r["type"], "url": r["url"],
                     "scrape_interval": r["scrape_interval"], "enabled": bool(r["enabled"])} for r in rows]

    def delete_datasource(self, ds_id: int):
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM datasources WHERE id=?", (ds_id,))

    def toggle_datasource(self, ds_id: int, enabled: bool):
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE datasources SET enabled=? WHERE id=?", (1 if enabled else 0, ds_id))

    # ── App Settings (key/value) ──────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, value, time.time()))

    # ── Users ─────────────────────────────────────────────────────────

    def create_user(self, username: str, password_hash: str, role: str = "read") -> int:
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, password_hash, role, now))
                return cursor.lastrowid

    def get_user(self, username: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                return None
            return {"id": row["id"], "username": row["username"],
                    "password_hash": row["password_hash"], "role": row["role"]}

    def get_all_users(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
            return [{"id": r["id"], "username": r["username"], "role": r["role"]} for r in rows]

    def delete_user(self, user_id: int):
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    def update_user_password(self, user_id: int, password_hash: str):
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))

    def update_user_role(self, user_id: int, role: str):
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))

    def user_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]

    # ── Alert Rules CRUD ───────────────────────────────────────────────

    def create_alert_rule(self, rule: Dict) -> int:
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO alert_rules (name, metric_name, operator, warning, critical, item_pattern, message, notify, channels, enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rule["name"], rule["metric_name"], rule.get("operator", ">"),
                     rule.get("warning"), rule.get("critical"),
                     rule.get("item_pattern", ""), rule.get("message", ""),
                     rule.get("notify", "both"), rule.get("channels", ""),
                     1 if rule.get("enabled", True) else 0, now, now),
                )
                return cursor.lastrowid

    def update_alert_rule(self, rule_id: int, rule: Dict):
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE alert_rules SET name=?, metric_name=?, operator=?, warning=?, critical=?, "
                    "item_pattern=?, message=?, notify=?, channels=?, enabled=?, updated_at=? WHERE id=?",
                    (rule["name"], rule["metric_name"], rule.get("operator", ">"),
                     rule.get("warning"), rule.get("critical"),
                     rule.get("item_pattern", ""), rule.get("message", ""),
                     rule.get("notify", "both"), rule.get("channels", ""),
                     1 if rule.get("enabled", True) else 0, now, rule_id),
                )

    def delete_alert_rule(self, rule_id: int):
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))

    def toggle_alert_rule(self, rule_id: int, enabled: bool):
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE alert_rules SET enabled=?, updated_at=? WHERE id=?",
                             (1 if enabled else 0, time.time(), rule_id))

    def get_alert_rules(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
            return [
                {"id": r["id"], "name": r["name"], "metric_name": r["metric_name"],
                 "operator": r["operator"], "warning": r["warning"], "critical": r["critical"],
                 "item_pattern": r["item_pattern"], "message": r["message"],
                 "notify": r["notify"] if "notify" in r.keys() else "both",
                 "channels": r["channels"] if "channels" in r.keys() else "",
                 "enabled": bool(r["enabled"])}
                for r in rows
            ]

    def get_latest_snapshot(self, cluster_id: str = None) -> Optional[Dict]:
        """Return the most recent snapshot with full check results."""
        with self._connect() as conn:
            if cluster_id:
                row = conn.execute(
                    "SELECT * FROM snapshots WHERE cluster_id=? ORDER BY created_at DESC LIMIT 1", (cluster_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM snapshots ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if not row:
                return None
            return self._expand_snapshot(conn, row)

    def get_snapshots(self, hours: int = 168, cluster_id: str = None) -> List[Dict]:
        """Return snapshot summaries for the given time window (default 7 days = 168h)."""
        cutoff = time.time() - (hours * 3600)
        with self._connect() as conn:
            if cluster_id:
                rows = conn.execute(
                    "SELECT id, timestamp, overall_status, cluster_info, total_duration_sec "
                    "FROM snapshots WHERE created_at > ? AND cluster_id=? ORDER BY created_at ASC",
                    (cutoff, cluster_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, timestamp, overall_status, cluster_info, total_duration_sec "
                    "FROM snapshots WHERE created_at > ? ORDER BY created_at ASC",
                    (cutoff,),
                ).fetchall()
            return [
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "overall_status": r["overall_status"],
                    "cluster_info": json.loads(r["cluster_info"] or "{}"),
                    "total_duration_sec": r["total_duration_sec"],
                }
                for r in rows
            ]

    def get_snapshot_by_id(self, snap_id: int) -> Optional[Dict]:
        """Return a specific snapshot with full check results."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snap_id,)).fetchone()
            if not row:
                return None
            return self._expand_snapshot(conn, row)

    def get_time_series(
        self,
        metric_name: str = None,
        item_name: str = None,
        category: str = None,
        check_name: str = None,
        cluster_id: str = None,
        hours: int = 168,
        start: str = None,
        end: str = None,
        limit: int = 5000,
    ) -> List[Dict]:
        """
        Query time-series metric data points. Supports filtering by metric name,
        item, category, and time range (hours or start/end timestamps).
        """
        conditions = []
        params: list = []

        if start and end:
            # Custom date range: start/end are "YYYY-MM-DD HH:MM" strings
            conditions.append("timestamp >= ?")
            params.append(start)
            conditions.append("timestamp <= ?")
            params.append(end)
        else:
            cutoff = time.time() - (hours * 3600)
            conditions.append("created_at > ?")
            params.append(cutoff)

        if metric_name:
            conditions.append("metric_name = ?")
            params.append(metric_name)
        if item_name:
            conditions.append("item_name = ?")
            params.append(item_name)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if check_name:
            conditions.append("check_name = ?")
            params.append(check_name)
        if cluster_id:
            conditions.append("cluster_id = ?")
            params.append(cluster_id)

        where = " AND ".join(conditions)
        query = f"SELECT * FROM time_series WHERE {where} ORDER BY created_at ASC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "timestamp": r["timestamp"],
                    "created_at": r["created_at"],
                    "category": r["category"],
                    "check_name": r["check_name"],
                    "item_name": r["item_name"],
                    "metric_name": r["metric_name"],
                    "metric_value": r["metric_value"],
                    "status": r["status"],
                }
                for r in rows
            ]

    def get_available_metrics(self) -> List[Dict]:
        """Return distinct metric names with their categories and item names."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category, check_name, item_name, metric_name "
                "FROM time_series ORDER BY category, check_name, item_name, metric_name"
            ).fetchall()
            return [
                {
                    "category": r["category"],
                    "check_name": r["check_name"],
                    "item_name": r["item_name"],
                    "metric_name": r["metric_name"],
                }
                for r in rows
            ]

    def get_metric_summary(self, hours: int = 24, cluster_id: str = None) -> Dict:
        """Return aggregated metric stats for the overview dashboard."""
        cutoff = time.time() - (hours * 3600)
        with self._connect() as conn:
            if cluster_id:
                status_rows = conn.execute(
                    "SELECT timestamp, overall_status FROM snapshots "
                    "WHERE created_at > ? AND cluster_id=? ORDER BY created_at ASC",
                    (cutoff, cluster_id),
                ).fetchall()
                latest_snap = conn.execute(
                    "SELECT id FROM snapshots WHERE cluster_id=? ORDER BY created_at DESC LIMIT 1", (cluster_id,)
                ).fetchone()
            else:
                status_rows = conn.execute(
                    "SELECT timestamp, overall_status FROM snapshots "
                    "WHERE created_at > ? ORDER BY created_at ASC",
                    (cutoff,),
                ).fetchall()
                latest_snap = conn.execute(
                    "SELECT id FROM snapshots ORDER BY created_at DESC LIMIT 1"
                ).fetchone()

            node_metrics = []
            if latest_snap:
                snap_id = latest_snap["id"]
                node_rows = conn.execute(
                    "SELECT item_name, metric_name, metric_value FROM time_series "
                    "WHERE snapshot_id = ? AND metric_name IN ('cpu_percent', 'memory_percent')",
                    (snap_id,),
                ).fetchall()
                # Group by node
                nodes = {}
                for r in node_rows:
                    name = r["item_name"]
                    if name not in nodes:
                        nodes[name] = {}
                    nodes[name][r["metric_name"]] = r["metric_value"]
                node_metrics = [
                    {"name": n, **m} for n, m in sorted(nodes.items())
                ]

            return {
                "status_timeline": [
                    {"timestamp": r["timestamp"], "status": r["overall_status"]}
                    for r in status_rows
                ],
                "node_metrics": node_metrics,
                "snapshot_count": len(status_rows),
            }

    def get_stats(self) -> Dict:
        """Return database statistics."""
        with self._connect() as conn:
            snap_count = conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()["c"]
            ts_count = conn.execute("SELECT COUNT(*) as c FROM time_series").fetchone()["c"]
            cr_count = conn.execute("SELECT COUNT(*) as c FROM check_results").fetchone()["c"]

            oldest = conn.execute(
                "SELECT MIN(timestamp) as t FROM snapshots"
            ).fetchone()["t"]
            newest = conn.execute(
                "SELECT MAX(timestamp) as t FROM snapshots"
            ).fetchone()["t"]

            db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

            return {
                "snapshots": snap_count,
                "time_series_points": ts_count,
                "check_results": cr_count,
                "oldest_data": oldest,
                "newest_data": newest,
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "retention_days": self.retention_days,
            }

    # ── Internal helpers ─────────────────────────────────────────────────

    def _expand_snapshot(self, conn, row) -> Dict:
        """Expand a snapshot row with its check results."""
        snap_id = row["id"]
        checks = conn.execute(
            "SELECT * FROM check_results WHERE snapshot_id = ? ORDER BY id",
            (snap_id,),
        ).fetchall()

        return {
            "id": snap_id,
            "timestamp": row["timestamp"],
            "overall_status": row["overall_status"],
            "cluster_info": json.loads(row["cluster_info"] or "{}"),
            "total_duration_sec": row["total_duration_sec"],
            "checks": [
                {
                    "name": c["name"],
                    "category": c["category"],
                    "status": c["status"],
                    "summary": c["summary"],
                    "items": json.loads(c["items"] or "[]"),
                    "duration_sec": c["duration_sec"],
                }
                for c in checks
            ],
        }
