"""
Analytics engine — anomaly detection, SLA/uptime tracking, comparative analysis.
Works on stored time-series data in SQLite.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

log = logging.getLogger(__name__)


class Analytics:
    """Provides anomaly detection, SLA tracking, and comparative analysis."""

    def __init__(self, store):
        self.store = store

    # ── Anomaly Detection ────────────────────────────────────────────

    def detect_anomalies(self, hours: int = 24) -> List[Dict]:
        """
        Detect metrics that deviate significantly from their 7-day average.
        An anomaly = current value is more than 2 standard deviations from mean.
        """
        anomalies = []
        # Key metrics to analyze
        metrics_to_check = [
            ("cpu_percent", "CPU", "%", 15),
            ("memory_percent", "Memory", "%", 10),
            ("max_fs_usage_pct", "Disk", "%", 5),
            ("max_temp_celsius", "Temperature", "C", 5),
            ("load_1m", "Load", "", 2),
            ("total_drop_pct", "Net Drops", "%", 0.01),
            ("allocated_fds", "File Descriptors", "", 5000),
        ]

        for metric_name, label, unit, min_threshold in metrics_to_check:
            # Get 7-day history
            data = self.store.get_time_series(metric_name=metric_name, hours=168, limit=50000)
            if len(data) < 10:
                continue

            # Group by item (node)
            by_item = {}
            for d in data:
                name = d["item_name"]
                if name not in by_item:
                    by_item[name] = []
                by_item[name].append(d["metric_value"])

            for item_name, values in by_item.items():
                if len(values) < 5:
                    continue

                # Calculate stats
                mean = sum(values) / len(values)
                if mean < min_threshold:
                    continue  # Skip near-zero metrics

                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std_dev = variance ** 0.5
                current = values[-1]

                if std_dev == 0:
                    continue

                # Z-score: how many std deviations from mean
                z_score = abs(current - mean) / std_dev
                deviation_pct = round(abs(current - mean) / mean * 100, 1) if mean > 0 else 0

                if z_score >= 2.0 and deviation_pct >= 10:
                    direction = "above" if current > mean else "below"
                    severity = "critical" if z_score >= 3.0 else "warning"

                    anomalies.append({
                        "metric": metric_name,
                        "label": label,
                        "item": item_name,
                        "current": round(current, 2),
                        "mean_7d": round(mean, 2),
                        "std_dev": round(std_dev, 2),
                        "z_score": round(z_score, 2),
                        "deviation_pct": deviation_pct,
                        "direction": direction,
                        "severity": severity,
                        "unit": unit,
                        "message": f"{label} on {item_name} is {deviation_pct}% {direction} its 7-day average ({current:.1f}{unit} vs avg {mean:.1f}{unit})",
                    })

        # Sort by z_score descending
        anomalies.sort(key=lambda a: a["z_score"], reverse=True)
        return anomalies

    # ── SLA / Uptime Tracking ────────────────────────────────────────

    def get_uptime(self, hours: int = 168) -> Dict:
        """
        Calculate per-node uptime percentage over the given period.
        A node is "up" if its check status was HEALTHY or WARNING (not CRITICAL/UNKNOWN).
        """
        snapshots = self.store.get_snapshots(hours=hours)
        if not snapshots:
            return {"nodes": [], "cluster_uptime": 0, "period_hours": hours, "total_snapshots": 0}

        # Track per-node status across snapshots
        node_counts = {}  # {node: {total: N, healthy: N}}
        cluster_healthy = 0

        for snap_summary in snapshots:
            snap = self.store.get_snapshot_by_id(snap_summary["id"])
            if not snap:
                continue

            overall = snap.get("overall_status", "UNKNOWN")
            if overall in ("HEALTHY", "WARNING"):
                cluster_healthy += 1

            for check in snap.get("checks", []):
                if check["name"] == "Node Health & Readiness":
                    for item in check.get("items", []):
                        name = item["name"]
                        if name not in node_counts:
                            node_counts[name] = {"total": 0, "healthy": 0, "warning": 0, "critical": 0}
                        node_counts[name]["total"] += 1
                        st = item["status"]
                        if st == "HEALTHY":
                            node_counts[name]["healthy"] += 1
                        elif st == "WARNING":
                            node_counts[name]["warning"] += 1
                        elif st == "CRITICAL":
                            node_counts[name]["critical"] += 1

        # Calculate percentages
        nodes = []
        for name, counts in sorted(node_counts.items()):
            total = counts["total"]
            if total == 0:
                continue
            uptime = round((counts["healthy"] + counts["warning"]) / total * 100, 2)
            nodes.append({
                "name": name,
                "uptime_pct": uptime,
                "total_checks": total,
                "healthy": counts["healthy"],
                "warning": counts["warning"],
                "critical": counts["critical"],
                "sla_met": uptime >= 99.9,
            })

        total_snaps = len(snapshots)
        cluster_uptime = round(cluster_healthy / total_snaps * 100, 2) if total_snaps > 0 else 0

        return {
            "nodes": nodes,
            "cluster_uptime": cluster_uptime,
            "period_hours": hours,
            "total_snapshots": total_snaps,
        }

    # ── Capacity Forecast ────────────────────────────────────────────

    def get_forecast(self) -> Dict:
        """
        Linear-trend capacity forecast for percent-based metrics.
        Uses ~7 days of stored time-series, fits a simple slope per (metric, node),
        projects 7d/30d, and reports days until 100%.
        """
        # (metric_name, label, limit). limit=None means no ceiling forecast.
        targets = [
            ("cpu_percent", "CPU", 100.0),
            ("memory_percent", "Memory", 100.0),
            ("max_fs_usage_pct", "Disk", 100.0),
            ("usage_percent", "Ceph Storage", 100.0),
        ]
        forecasts = []
        for metric, label, limit in targets:
            data = self.store.get_time_series(metric_name=metric, hours=168, limit=50000)
            if len(data) < 10:
                continue
            by_item = {}
            for d in data:
                name = d["item_name"] or "cluster"
                by_item.setdefault(name, []).append({"t": d.get("created_at") or 0, "v": d["metric_value"]})
            for name, pts in by_item.items():
                if len(pts) < 5:
                    continue
                pts.sort(key=lambda p: p["t"])
                # If we don't have created_at, fall back to position-based time
                t0 = pts[0]["t"] or 0
                if t0 == 0:
                    # Use index as hours-since-start surrogate
                    xs = [i for i in range(len(pts))]
                    span_hours = max(1, len(pts) / 120.0)  # ~one sample per 30s, 120/hr
                else:
                    xs = [(p["t"] - t0) / 3600.0 for p in pts]  # hours since first
                    span_hours = max(1, xs[-1])
                ys = [p["v"] for p in pts]
                # Linear regression slope (per hour)
                n = len(xs)
                mean_x = sum(xs) / n
                mean_y = sum(ys) / n
                num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
                den = sum((xs[i] - mean_x) ** 2 for i in range(n)) or 1e-9
                slope_per_hr = num / den
                daily_change = round(slope_per_hr * 24, 2)
                current = round(ys[-1], 1)
                projected_7d = round(min(max(current + slope_per_hr * 24 * 7, 0), 999), 1)
                projected_30d = round(min(max(current + slope_per_hr * 24 * 30, 0), 999), 1)
                # Days until limit
                days_to_limit = 0
                if limit is not None and slope_per_hr > 1e-6 and current < limit:
                    days_to_limit = round(((limit - current) / slope_per_hr) / 24, 1)
                trend = "rising" if slope_per_hr > 0.001 else "falling" if slope_per_hr < -0.001 else "stable"
                # Status
                if days_to_limit and days_to_limit <= 7:
                    status = "CRITICAL"
                elif days_to_limit and days_to_limit <= 30:
                    status = "WARNING"
                else:
                    status = "HEALTHY"
                forecasts.append({
                    "name": f"{label} — {name}",
                    "status": status,
                    "details": {"trend": trend, "metric": metric, "samples": n, "span_hours": round(span_hours, 1)},
                    "metrics": {
                        "current_value": current,
                        "daily_change": daily_change,
                        "projected_7d": projected_7d,
                        "projected_30d": projected_30d,
                        "days_to_limit": days_to_limit,
                    },
                })
        # Sort: most urgent first (lowest non-zero days_to_limit, then trend rising)
        forecasts.sort(key=lambda f: (
            f["metrics"]["days_to_limit"] if f["metrics"]["days_to_limit"] > 0 else 1e9,
            -f["metrics"]["daily_change"],
        ))
        return {"forecasts": forecasts, "summary": f"{len(forecasts)} resources analyzed"}

    # ── Comparative Analysis ─────────────────────────────────────────

    def compare_periods(self, metric_name: str = "cpu_percent") -> Dict:
        """
        Compare this week vs last week for a given metric.
        Returns per-node comparison with change direction.
        """
        now_hours = 168  # this week
        # This week
        this_week = self.store.get_time_series(metric_name=metric_name, hours=168, limit=50000)
        # Last week (7-14 days ago) - get 14 days and filter
        all_data = self.store.get_time_series(metric_name=metric_name, hours=336, limit=100000)

        cutoff = time.time() - (168 * 3600)  # 7 days ago

        tw_by_node = {}
        lw_by_node = {}

        for d in all_data:
            name = d["item_name"]
            val = d["metric_value"]
            # Approximate: if timestamp is recent it's this week
            # Since we store created_at, we use the data order
            # this_week data is in the this_week list
            pass

        # Simpler approach: split by position
        for d in this_week:
            name = d["item_name"]
            if name not in tw_by_node:
                tw_by_node[name] = []
            tw_by_node[name].append(d["metric_value"])

        # For last week, get data that's NOT in this week
        tw_timestamps = set(d["timestamp"] for d in this_week)
        for d in all_data:
            if d["timestamp"] not in tw_timestamps:
                name = d["item_name"]
                if name not in lw_by_node:
                    lw_by_node[name] = []
                lw_by_node[name].append(d["metric_value"])

        comparisons = []
        for name in sorted(set(list(tw_by_node.keys()) + list(lw_by_node.keys()))):
            tw_vals = tw_by_node.get(name, [])
            lw_vals = lw_by_node.get(name, [])

            tw_avg = sum(tw_vals) / len(tw_vals) if tw_vals else 0
            lw_avg = sum(lw_vals) / len(lw_vals) if lw_vals else 0
            tw_max = max(tw_vals) if tw_vals else 0
            lw_max = max(lw_vals) if lw_vals else 0

            change = round(tw_avg - lw_avg, 2)
            change_pct = round(change / lw_avg * 100, 1) if lw_avg > 0 else 0

            comparisons.append({
                "node": name,
                "this_week_avg": round(tw_avg, 2),
                "last_week_avg": round(lw_avg, 2),
                "this_week_max": round(tw_max, 2),
                "last_week_max": round(lw_max, 2),
                "change": change,
                "change_pct": change_pct,
                "direction": "up" if change > 0.5 else "down" if change < -0.5 else "stable",
                "data_points_tw": len(tw_vals),
                "data_points_lw": len(lw_vals),
            })

        return {
            "metric": metric_name,
            "comparisons": comparisons,
        }
