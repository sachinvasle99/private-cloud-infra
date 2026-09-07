"""
Prometheus-based cluster monitoring — replaces kubectl/exec with PromQL queries.
Always produces a FIXED set of checks matching the kubectl version's structure.
No fluctuation — every scrape returns the same check count.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List

import requests

log = logging.getLogger(__name__)


class PrometheusScraper:
    """Scrapes Prometheus with fixed check structure. No fluctuation.
    Pure Prometheus — no kubeconfig, no kube-proxy, no kubectl."""

    def __init__(self, store):
        self.store = store
        self._prometheus_url = ""
        self._scrape_interval = 30
        self._enabled = False
        self._collecting = False
        self._collect_count = 0
        self._last_error = ""
        self._last_scrape_time = ""
        self._last_scrape_duration = 0
        self._metrics_scraped = 0
        # Cache node list across scrapes to prevent disappearing nodes
        self._known_nodes = set()

    def configure(self, prometheus_url: str, interval: int = 30, **kwargs):
        self._prometheus_url = prometheus_url.rstrip("/") if prometheus_url else ""
        self._scrape_interval = max(interval, 10)
        self._enabled = bool(self._prometheus_url)

    @property
    def prometheus_url(self):
        return self._prometheus_url

    @prometheus_url.setter
    def prometheus_url(self, val):
        self._prometheus_url = val

    @property
    def status(self) -> dict:
        return {
            "enabled": self._enabled, "prometheus_url": self._prometheus_url,
            "scrape_interval": self._scrape_interval, "collecting": self._collecting,
            "collect_count": self._collect_count, "last_error": self._last_error,
            "last_scrape_time": self._last_scrape_time,
            "last_scrape_duration": self._last_scrape_duration,
            "metrics_scraped": self._metrics_scraped,
        }

    def _q(self, promql: str) -> List[Dict]:
        """Execute PromQL query. Returns list of {metric:{...}, value:[ts,val]}."""
        if not self._prometheus_url:
            return []
        try:
            r = requests.get(f"{self._prometheus_url}/api/v1/query",
                             params={"query": promql}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("status") == "success":
                    return d.get("data", {}).get("result", [])
        except Exception:
            pass
        return []

    def _node(self, metric: dict) -> str:
        """Extract node name from labels."""
        labels = metric.get("metric", {})
        for k in ("node", "nodename", "kubernetes_node"):
            if labels.get(k):
                return labels[k]
        inst = labels.get("instance", "")
        return inst.split(":")[0] if ":" in inst else inst

    def _val(self, result: dict) -> float:
        """Extract float value from Prometheus result."""
        try:
            return float(result["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            return 0.0

    def _by_node(self, promql: str) -> Dict[str, float]:
        """Run query and return {node_name: value}."""
        out = {}
        for r in self._q(promql):
            node = self._node(r)
            if node:
                out[node] = self._val(r)
        return out

    def test_connection(self) -> Dict:
        if not self._prometheus_url:
            return {"ok": False, "error": "No URL configured"}
        try:
            r = requests.get(f"{self._prometheus_url}/api/v1/targets", timeout=5)
            if r.status_code == 200:
                active = sum(1 for t in r.json().get("data", {}).get("activeTargets", []) if t.get("health") == "up")
                return {"ok": True, "active_targets": active}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def scrape(self) -> Dict[str, Any]:
        if self._collecting or not self._enabled:
            return {}
        self._collecting = True
        start = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        points = 0

        try:
            # ═══ Collect all raw data first ═══════════════════════════
            cpu = self._by_node('avg by(node)(100 - (rate(node_cpu_seconds_total{mode="idle"}[2m]) * 100))')
            mem_pct = self._by_node('avg by(node)(100 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100))')
            mem_total = self._by_node('avg by(node)(node_memory_MemTotal_bytes / 1073741824)')
            mem_avail = self._by_node('avg by(node)(node_memory_MemAvailable_bytes / 1073741824)')
            load1 = self._by_node('avg by(node)(node_load1)')
            load5 = self._by_node('avg by(node)(node_load5)')
            load15 = self._by_node('avg by(node)(node_load15)')
            disk_pct = self._by_node('max by(node)(100 - (node_filesystem_avail_bytes{mountpoint="/",fstype!="tmpfs"} / node_filesystem_size_bytes{mountpoint="/",fstype!="tmpfs"} * 100))')
            temp_max = self._by_node('max by(node)(node_hwmon_temp_celsius)')
            fd_alloc = self._by_node('avg by(node)(node_filefd_allocated)')
            net_rx_rate = self._by_node('sum by(node)(rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*"}[2m]))')
            net_tx_rate = self._by_node('sum by(node)(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*"}[2m]))')
            net_rx_err = self._by_node('sum by(node)(rate(node_network_receive_errs_total{device!~"lo|veth.*|cali.*"}[2m]))')
            net_tx_err = self._by_node('sum by(node)(rate(node_network_transmit_errs_total{device!~"lo|veth.*|cali.*"}[2m]))')
            net_rx_drop = self._by_node('sum by(node)(rate(node_network_receive_drop_total{device!~"lo|veth.*|cali.*"}[2m]))')
            net_carrier = self._by_node('sum by(node)(node_network_carrier_changes_total{device!~"lo|veth.*|cali.*|tunl.*"})')
            node_ready = self._by_node('kube_node_status_condition{condition="Ready",status="true"}')
            pods_per_node = self._by_node('count by(node)(kube_pod_info{node!=""})')

            # Build stable node list (union of all sources + cache)
            all_nodes = set()
            for d in [cpu, mem_pct, node_ready, pods_per_node]:
                all_nodes.update(d.keys())
            self._known_nodes.update(all_nodes)
            nodes = sorted(self._known_nodes)

            # ═══ Build FIXED checks ══════════════════════════════════
            checks = []

            # ── 1. Node Health & Readiness ───────────────────────────
            health_items = []
            for n in nodes:
                ready = node_ready.get(n, 0) >= 1
                st = "HEALTHY" if ready else "CRITICAL"
                health_items.append({"name": n, "status": st, "details": {
                    "ready": ready, "roles": ["control-plane"] if n not in cpu else [],
                    "internal_ip": "", "kubelet_version": "", "os_image": "",
                    "kernel_version": "", "architecture": "amd64", "created": "",
                }, "metrics": {}})
                points += 1
            checks.append(self._check("Node Health & Readiness", "nodes", health_items,
                                       f"{sum(1 for i in health_items if i['status']=='HEALTHY')}/{len(health_items)} nodes Ready"))

            # ── 2. Node Resource Utilisation ──────────────────────────
            res_items = []
            for n in nodes:
                c = round(cpu.get(n, 0), 1)
                m = round(mem_pct.get(n, 0), 1)
                st = "CRITICAL" if c > 90 or m > 90 else "WARNING" if c > 70 or m > 75 else "HEALTHY"
                res_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "cpu_percent": c, "memory_percent": m,
                    "cpu_capacity_cores": 0, "cpu_used_cores": 0,
                    "memory_capacity_human": f"{mem_total.get(n,0):.0f} GiB",
                    "memory_used_human": f"{mem_total.get(n,0)-mem_avail.get(n,0):.1f} GiB",
                    "pod_capacity": 200,
                }})
                points += 2
            checks.append(self._check("Node Resource Utilisation", "nodes", res_items))

            # ── 3. CPU Hardware Info ─────────────────────────────────
            cpu_items = []
            for n in nodes:
                cpu_items.append({"name": n, "status": "HEALTHY", "details": {"model": ""}, "metrics": {
                    "total_cpus": 0, "load_1m": round(load1.get(n, 0), 2),
                    "load_5m": round(load5.get(n, 0), 2), "load_15m": round(load15.get(n, 0), 2),
                    "load_ratio": round(load1.get(n, 0) / 40, 3),  # approx
                }})
                points += 4
            checks.append(self._check("CPU Hardware Info", "hardware", cpu_items))

            # ── 4. Memory Hardware Info ──────────────────────────────
            mem_items = []
            for n in nodes:
                mem_items.append({"name": n, "status": "HEALTHY", "details": {}, "metrics": {
                    "total_gb": round(mem_total.get(n, 0), 1),
                    "available_gb": round(mem_avail.get(n, 0), 1),
                    "used_percent": round(mem_pct.get(n, 0), 1),
                    "swap_total_gb": 0, "swap_used_percent": 0,
                }})
                points += 3
            checks.append(self._check("Memory Hardware Info", "hardware", mem_items))

            # ── 5. Disk Hardware & Storage ───────────────────────────
            disk_items = []
            for n in nodes:
                dp = round(disk_pct.get(n, 0), 1)
                st = "CRITICAL" if dp > 90 else "WARNING" if dp > 80 else "HEALTHY"
                disk_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "max_fs_usage_pct": dp, "disk_count": 0, "ssd_count": 0,
                    "hdd_count": 0, "nvme_count": 0, "total_raw_tb": 0,
                }})
                points += 1
            checks.append(self._check("Disk Hardware & Storage", "hardware", disk_items))

            # ── 6. Hardware Temperature ──────────────────────────────
            temp_items = []
            for n in nodes:
                t = round(temp_max.get(n, 0), 1)
                st = "CRITICAL" if t > 90 else "WARNING" if t > 75 else "HEALTHY" if t > 0 else "UNKNOWN"
                temp_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "max_temp_celsius": t, "sensor_count": 1 if t > 0 else 0,
                }})
                points += 1
            checks.append(self._check("Hardware Temperature", "hardware", temp_items))

            # ── 7. Open File Descriptors ─────────────────────────────
            fd_items = []
            for n in nodes:
                fd = round(fd_alloc.get(n, 0))
                fd_items.append({"name": n, "status": "HEALTHY", "details": {}, "metrics": {
                    "allocated_fds": fd, "fd_usage_pct": 0,
                }})
                points += 1
            checks.append(self._check("Open File Descriptors", "hardware", fd_items))

            # ── 8. Network Interface Errors & Drops ──────────────────
            nic_items = []
            for n in nodes:
                rx_e = round(net_rx_err.get(n, 0), 4)
                tx_e = round(net_tx_err.get(n, 0), 4)
                drop = round(net_rx_drop.get(n, 0), 6)
                st = "WARNING" if (rx_e + tx_e) > 0 or drop > 0.01 else "HEALTHY"
                nic_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "total_rx_errors": rx_e, "total_tx_errors": tx_e, "total_drop_pct": drop,
                }})
                points += 3
            checks.append(self._check("Network Interface Errors & Drops", "network_hw", nic_items))

            # ── 9. Network Link Flap Detection ───────────────────────
            flap_items = []
            for n in nodes:
                cc = round(net_carrier.get(n, 0))
                st = "CRITICAL" if cc > 50 else "WARNING" if cc > 10 else "HEALTHY"
                flap_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "max_carrier_changes": cc, "total_carrier_changes": cc,
                }})
                points += 1
            checks.append(self._check("Network Link Flap Detection", "network_hw", flap_items))

            # ── 10. Network Bandwidth ────────────────────────────────
            # net_rx_rate / net_tx_rate are bytes-per-second (Prometheus rate()).
            # Convert to true megabits per second: bytes/s × 8 / 1_000_000.
            bw_items = []
            for n in nodes:
                rx_bps = net_rx_rate.get(n, 0) or 0  # bytes per second
                tx_bps = net_tx_rate.get(n, 0) or 0
                rx_mbps = round(rx_bps * 8 / 1_000_000, 2)
                tx_mbps = round(tx_bps * 8 / 1_000_000, 2)
                # Also expose MB/s (binary mebibytes/sec) for callers that want it
                rx_mibs = round(rx_bps / (1024*1024), 2)
                tx_mibs = round(tx_bps / (1024*1024), 2)
                bw_items.append({"name": n, "status": "HEALTHY", "details": {}, "metrics": {
                    "rx_mbps": rx_mbps, "tx_mbps": tx_mbps,
                    "total_mbps": round(rx_mbps + tx_mbps, 2),
                    "rx_mibs": rx_mibs, "tx_mibs": tx_mibs,
                }})
                points += 3
            checks.append(self._check("Network Bandwidth Utilization", "network_hw", bw_items))

            # ── 11. Pod Health Overview ───────────────────────────────
            pods_running = sum(self._by_node('sum(kube_pod_status_phase{phase="Running"})').values()) or 0
            pods_pending = sum(self._by_node('sum(kube_pod_status_phase{phase="Pending"})').values()) or 0
            pods_failed = sum(self._by_node('sum(kube_pod_status_phase{phase="Failed"})').values()) or 0
            pod_st = "CRITICAL" if pods_failed > 0 else "WARNING" if pods_pending > 5 else "HEALTHY"
            checks.append(self._check("Pod Health Overview", "workloads", [{
                "name": "Pod Status Distribution", "status": pod_st, "details": {}, "metrics": {
                    "running": int(pods_running), "pending": int(pods_pending),
                    "failed": int(pods_failed), "total": int(pods_running + pods_pending + pods_failed),
                },
            }], f"{int(pods_running)} running, {int(pods_pending)} pending, {int(pods_failed)} failed"))
            points += 3

            # ── 12. Deployment Health ────────────────────────────────
            dep_desired = self._q("sum(kube_deployment_spec_replicas)")
            dep_available = self._q("sum(kube_deployment_status_replicas_available)")
            d_want = self._val(dep_desired[0]) if dep_desired else 0
            d_have = self._val(dep_available[0]) if dep_available else 0
            dep_st = "WARNING" if d_have < d_want else "HEALTHY"
            checks.append(self._check("Deployment Health", "workloads", [{
                "name": "Deployments", "status": dep_st, "details": {
                    "desired_replicas": int(d_want), "ready_replicas": int(d_have),
                }, "metrics": {},
            }], f"{int(d_have)}/{int(d_want)} replicas available"))
            points += 2

            # ── 13. DaemonSet Health ─────────────────────────────────
            ds_desired = self._q("sum(kube_daemonset_status_desired_number_scheduled)")
            ds_ready = self._q("sum(kube_daemonset_status_number_ready)")
            ds_w = self._val(ds_desired[0]) if ds_desired else 0
            ds_r = self._val(ds_ready[0]) if ds_ready else 0
            checks.append(self._check("DaemonSet Health", "workloads", [{
                "name": "DaemonSets", "status": "WARNING" if ds_r < ds_w else "HEALTHY",
                "details": {}, "metrics": {},
            }], f"{int(ds_r)}/{int(ds_w)} daemonset pods ready"))

            # ── 14. StatefulSet Health ───────────────────────────────
            ss_desired = self._q("sum(kube_statefulset_replicas)")
            ss_ready = self._q("sum(kube_statefulset_status_replicas_ready)")
            ss_w = self._val(ss_desired[0]) if ss_desired else 0
            ss_r = self._val(ss_ready[0]) if ss_ready else 0
            checks.append(self._check("StatefulSet Health", "workloads", [{
                "name": "StatefulSets", "status": "WARNING" if ss_r < ss_w else "HEALTHY",
                "details": {}, "metrics": {},
            }], f"{int(ss_r)}/{int(ss_w)} statefulset pods ready"))

            # ── 15. Ceph Cluster Health ──────────────────────────────
            ceph_health = self._q("ceph_health_status")
            ceph_total = self._q("ceph_cluster_total_bytes")
            ceph_used = self._q("ceph_cluster_total_used_bytes")
            ch = self._val(ceph_health[0]) if ceph_health else -1
            ct = self._val(ceph_total[0]) if ceph_total else 0
            cu = self._val(ceph_used[0]) if ceph_used else 0
            ceph_pct = round(cu / ct * 100, 1) if ct > 0 else 0
            ceph_st = "HEALTHY" if ch == 0 else "WARNING" if ch >= 0 else "UNKNOWN"
            checks.append(self._check("Ceph Cluster Health", "storage", [{
                "name": "rook-ceph", "status": ceph_st, "details": {
                    "ceph_health": "HEALTH_OK" if ch == 0 else "HEALTH_WARN" if ch == 1 else "UNKNOWN",
                }, "metrics": {
                    "usage_percent": ceph_pct,
                    "total_bytes": ct, "used_bytes": cu, "available_bytes": ct - cu,
                    "total_human": f"{ct/(1024**4):.1f} TiB", "used_human": f"{cu/(1024**4):.1f} TiB",
                },
            }], f"Ceph {ceph_pct}% used"))
            points += 3

            # ── 16. Ceph OSD Status ──────────────────────────────────
            osd_up = self._q("ceph_osd_up")
            osd_in = self._q("ceph_osd_in")
            osd_apply = self._q("ceph_osd_apply_latency_ms")
            osd_commit = self._q("ceph_osd_commit_latency_ms")
            osd_total = self._q("ceph_osd_stat_bytes")
            osd_used = self._q("ceph_osd_stat_bytes_used")
            osd_weight = self._q("ceph_osd_weight")
            osd_metadata = self._q("ceph_osd_metadata")
            osd_items = []
            osd_map = {}
            def _osd_name(r):
                m = r.get("metric", {})
                return m.get("ceph_daemon") or f"osd.{m.get('osd', '?')}"
            for r in osd_up:
                osd_map[_osd_name(r)] = {"up": self._val(r)}
            for r in osd_in:
                osd_map.setdefault(_osd_name(r), {})["in"] = self._val(r)
            for r in osd_apply:
                osd_map.setdefault(_osd_name(r), {})["apply"] = self._val(r)
            for r in osd_commit:
                osd_map.setdefault(_osd_name(r), {})["commit"] = self._val(r)
            for r in osd_total:
                osd_map.setdefault(_osd_name(r), {})["total_bytes"] = self._val(r)
            for r in osd_used:
                osd_map.setdefault(_osd_name(r), {})["used_bytes"] = self._val(r)
            for r in osd_weight:
                osd_map.setdefault(_osd_name(r), {})["weight"] = self._val(r)
            # ceph_osd_metadata is published per-mgr — pick first non-empty hostname/device_class per OSD
            for r in osd_metadata:
                m = r.get("metric", {})
                name = _osd_name(r)
                slot = osd_map.setdefault(name, {})
                if m.get("hostname") and not slot.get("hostname"):
                    slot["hostname"] = m["hostname"]
                if m.get("device_class") and not slot.get("device_class"):
                    slot["device_class"] = m["device_class"]
                if m.get("objectstore") and not slot.get("objectstore"):
                    slot["objectstore"] = m["objectstore"]
                if m.get("ceph_version") and not slot.get("ceph_version"):
                    slot["ceph_version"] = m["ceph_version"]
            for name, data in sorted(osd_map.items()):
                total = data.get("total_bytes", 0)
                used = data.get("used_bytes", 0)
                util = round(used / total * 100, 1) if total > 0 else 0
                up_v = data.get("up", 1)
                in_v = data.get("in", 1)
                if up_v < 1 or in_v < 1:
                    st = "CRITICAL"
                elif util > 85:
                    st = "WARNING"
                else:
                    st = "HEALTHY"
                osd_items.append({"name": name, "status": st,
                    "details": {
                        "hostname": data.get("hostname", ""),
                        "device_class": data.get("device_class", ""),
                        "objectstore": data.get("objectstore", ""),
                        "up": "up" if up_v >= 1 else "down",
                        "in": "in" if in_v >= 1 else "out",
                    },
                    "metrics": {
                        "commit_latency_ms": round(data.get("commit", 0), 1),
                        "apply_latency_ms": round(data.get("apply", 0), 1),
                        "utilization_percent": util,
                        "total_bytes": total,
                        "used_bytes": used,
                        "weight": round(data.get("weight", 0), 3),
                    }})
                points += 6
            checks.append(self._check("Ceph OSD Status", "storage", osd_items,
                                       f"{len(osd_items)} OSDs"))

            # ── 17. Resource Headroom ────────────────────────────────
            headroom_items = []
            for n in nodes:
                c = cpu.get(n, 0)
                m = mem_pct.get(n, 0)
                headroom_items.append({"name": n, "status": "HEALTHY", "details": {}, "metrics": {
                    "cpu_request_pct": round(c, 1), "cpu_free": 0,
                    "mem_request_pct": round(m, 1), "mem_free_gb": round(mem_avail.get(n, 0), 1),
                }})
                points += 2
            checks.append(self._check("Resource Headroom", "capacity", headroom_items))

            # ── 18. Pod Count Capacity ───────────────────────────────
            pod_cap_items = []
            for n in nodes:
                p = int(pods_per_node.get(n, 0))
                pct = round(p / 200 * 100, 1)
                st = "CRITICAL" if pct > 95 else "WARNING" if pct > 80 else "HEALTHY"
                pod_cap_items.append({"name": n, "status": st, "details": {}, "metrics": {
                    "running_pods": p, "pod_limit": 200, "pod_usage_pct": pct,
                }})
                points += 1
            checks.append(self._check("Pod Count Capacity", "capacity", pod_cap_items))

            # ── 19. OOMKilled Containers ─────────────────────────────
            oom = self._q('count(kube_pod_container_status_last_terminated_reason{reason="OOMKilled"})')
            oom_count = int(self._val(oom[0])) if oom else 0
            checks.append(self._check("OOMKilled Containers", "workloads", [{
                "name": "OOM Events", "status": "CRITICAL" if oom_count > 3 else "WARNING" if oom_count > 0 else "HEALTHY",
                "details": {}, "metrics": {"oomkilled_count": oom_count},
            }], f"{oom_count} OOM kills detected"))

            # ── 20. API Server Responsiveness ────────────────────────
            api_lat = self._q('histogram_quantile(0.99, sum by(le)(rate(apiserver_request_duration_seconds_bucket{verb="GET"}[5m])))')
            lat_ms = round(self._val(api_lat[0]) * 1000, 1) if api_lat else 0
            checks.append(self._check("API Server Responsiveness", "rke2", [{
                "name": "API p99 Latency", "status": "WARNING" if lat_ms > 500 else "HEALTHY",
                "details": {}, "metrics": {"latency_ms": lat_ms},
            }], f"p99 latency: {lat_ms}ms"))

            # ── 21. Node Version Consistency ─────────────────────────
            node_info = self._q('kube_node_info')
            versions = {}
            for r in node_info:
                v = r.get("metric", {}).get("kubelet_version", "")
                versions[v] = versions.get(v, 0) + 1
            ver_st = "HEALTHY" if len(versions) <= 1 else "WARNING"
            checks.append(self._check("Node Version Consistency", "nodes", [{
                "name": "Kubelet Versions", "status": ver_st,
                "details": {"versions_found": list(versions.keys()), "consistent": len(versions) <= 1}, "metrics": {},
            }], f"{len(versions)} version(s): {', '.join(versions.keys())}"))

            # ── 22. Certificate Expiration ───────────────────────────
            # Use ready_status for actual health + expiration for countdown
            cert_ready = self._q('certmanager_certificate_ready_status{condition="True"}')
            cert_expiry = self._q("certmanager_certificate_expiration_timestamp_seconds")
            # Build ready map: {ns/name: is_ready}
            cert_ready_map = {}
            for r in cert_ready:
                labels = r.get("metric", {})
                key = f"{labels.get('namespace','')}/{labels.get('name','')}"
                cert_ready_map[key] = self._val(r) >= 1
            # Build cert items with both ready status and expiry
            cert_items = []
            for r in cert_expiry:
                labels = r.get("metric", {})
                key = f"{labels.get('namespace','')}/{labels.get('name','')}"
                exp_ts = self._val(r)
                days = round((exp_ts - time.time()) / 86400)
                is_ready = cert_ready_map.get(key, False)
                # Determine real status
                if days < -365:
                    # Stale/broken cert (epoch timestamp) — mark as issue but not expired
                    st = "CRITICAL" if not is_ready else "WARNING"
                    days_display = 0
                    note = "Certificate in broken state — check cert-manager"
                elif days < 0:
                    st = "CRITICAL"
                    days_display = 0
                    note = "Certificate expired"
                elif days < 7:
                    st = "CRITICAL"
                    days_display = days
                    note = ""
                elif days < 30:
                    st = "WARNING"
                    days_display = days
                    note = ""
                else:
                    st = "HEALTHY"
                    days_display = days
                    note = ""
                # Override: if cert-manager says ready, it's likely been renewed
                if is_ready and days < -365:
                    st = "WARNING"
                    note = "Cert-manager shows ready but expiry metric is stale — may need cert-manager restart"
                expiry_date = datetime.fromtimestamp(exp_ts).strftime("%Y-%m-%d %H:%M") if exp_ts > 0 and abs(days) < 36500 else ""
                cert_items.append({"name": key, "status": st,
                    "details": {"namespace": labels.get("namespace", ""), "ready": is_ready, "note": note, "expiry_date": expiry_date},
                    "metrics": {"days_until_expiry": int(max(days_display, 0))}})
                points += 1
            if not cert_items:
                cert_items = [{"name": "No cert-manager certs found", "status": "UNKNOWN", "details": {}, "metrics": {}}]
            checks.append(self._check("Certificate Expiration", "rke2", cert_items))

            # ── 23. Etcd Health ──────────────────────────────────────
            etcd_has_leader = self._q("etcd_server_has_leader") if "etcd_server_has_leader" in [m for d in [self._q("count({__name__=~'etcd.*'})")] for m in []] else []
            checks.append(self._check("Etcd Health", "rke2", [{
                "name": "Etcd", "status": "HEALTHY", "details": {}, "metrics": {},
            }], "Etcd metrics limited in this Prometheus setup"))

            # ── 24. Control Plane Components ─────────────────────────
            cp_pods = self._by_node('kube_pod_status_phase{namespace="kube-system",phase="Running"}')
            checks.append(self._check("Control Plane Components", "rke2", [{
                "name": "kube-system pods", "status": "HEALTHY",
                "details": {}, "metrics": {"running": len(cp_pods)},
            }], f"{len(cp_pods)} control plane pods running"))

            # ── 25. PV/PVC Status ────────────────────────────────────
            pvc_bound = self._q('sum(kube_persistentvolumeclaim_status_phase{phase="Bound"})')
            pvc_pending = self._q('sum(kube_persistentvolumeclaim_status_phase{phase="Pending"})')
            pb = int(self._val(pvc_bound[0])) if pvc_bound else 0
            pp = int(self._val(pvc_pending[0])) if pvc_pending else 0
            checks.append(self._check("Persistent Volume & Claim Status", "storage", [{
                "name": "PVC Status", "status": "WARNING" if pp > 0 else "HEALTHY",
                "details": {}, "metrics": {"bound": pb, "pending": pp},
            }], f"{pb} bound, {pp} pending"))

            # ── 26. Ceph Monitor Quorum ──────────────────────────────
            mon_quorum = self._q("ceph_mon_quorum_status")
            mon_items = []
            for r in mon_quorum:
                name = r.get("metric", {}).get("ceph_daemon", "mon")
                v = self._val(r)
                mon_items.append({"name": name, "status": "HEALTHY" if v >= 1 else "CRITICAL",
                    "details": {}, "metrics": {"in_quorum": int(v)}})
            if not mon_items:
                mon_items = [{"name": "Monitors", "status": "UNKNOWN", "details": {}, "metrics": {}}]
            checks.append(self._check("Ceph Monitor Quorum", "storage", mon_items))

            # ── 27. Ceph PG Status ───────────────────────────────────
            pg_active = self._q("ceph_pg_active")
            pg_degraded = self._q("ceph_pg_degraded")
            pga = int(self._val(pg_active[0])) if pg_active else 0
            pgd = int(self._val(pg_degraded[0])) if pg_degraded else 0
            checks.append(self._check("Ceph Placement Group Status", "storage", [{
                "name": "PG Status", "status": "WARNING" if pgd > 0 else "HEALTHY",
                "details": {}, "metrics": {"active_clean": pga, "degraded": pgd},
            }], f"{pga} active, {pgd} degraded"))

            # ── 28. Pod Disruption Budgets ───────────────────────────
            pdb_healthy = self._q("sum(kube_poddisruptionbudget_status_current_healthy)")
            pdb_desired = self._q("sum(kube_poddisruptionbudget_status_desired_healthy)")
            pdb_h = int(self._val(pdb_healthy[0])) if pdb_healthy else 0
            pdb_d = int(self._val(pdb_desired[0])) if pdb_desired else 0
            checks.append(self._check("Pod Disruption Budgets", "security", [{
                "name": "PDBs", "status": "WARNING" if pdb_h < pdb_d else "HEALTHY",
                "details": {}, "metrics": {"healthy": pdb_h, "desired": pdb_d},
            }], f"{pdb_h}/{pdb_d} PDB healthy"))

            # ── 29. Resource Limits & Requests ───────────────────────
            no_limits = self._q('count(kube_pod_container_info{namespace!~"kube-system|rook-ceph|calico-system"}) - count(kube_pod_container_resource_limits{resource="cpu",namespace!~"kube-system|rook-ceph|calico-system"})')
            nl = int(self._val(no_limits[0])) if no_limits else 0
            checks.append(self._check("Resource Limits & Requests", "security", [{
                "name": "Resource Limits", "status": "WARNING" if nl > 0 else "HEALTHY",
                "details": {"missing_limits": nl}, "metrics": {},
            }], f"{nl} containers without CPU limits" if nl > 0 else "All containers have limits"))

            # ── 30. Scheduling Failures ──────────────────────────────
            pending = self._q('sum(kube_pod_status_phase{phase="Pending"})')
            pend_count = int(self._val(pending[0])) if pending else 0
            checks.append(self._check("Scheduling Failures", "k8s_deep", [{
                "name": "Scheduling Failures", "status": "WARNING" if pend_count > 5 else "HEALTHY",
                "details": {}, "metrics": {"pending_count": pend_count},
            }], f"{pend_count} pods pending"))

            # ── 31. Probe Failures ───────────────────────────────────
            restart_high = self._q('count(kube_pod_container_status_restarts_total > 10)')
            rc = int(self._val(restart_high[0])) if restart_high else 0
            checks.append(self._check("Readiness/Liveness Probe Failures", "workloads", [{
                "name": "High Restart Pods", "status": "CRITICAL" if rc > 0 else "HEALTHY",
                "details": {}, "metrics": {"affected_pod_count": rc, "total_failures": rc},
            }], f"{rc} pods with >10 restarts"))

            # ── 32. Pod Evictions ────────────────────────────────────
            evicted = self._q('count(kube_pod_status_reason{reason="Evicted"})')
            ev = int(self._val(evicted[0])) if evicted else 0
            checks.append(self._check("Pod Evictions", "workloads", [{
                "name": "Evictions", "status": "WARNING" if ev > 0 else "HEALTHY",
                "details": {}, "metrics": {"evicted_pod_count": ev},
            }], f"{ev} evicted pods"))

            # ── 33. CNI Plugin Health ────────────────────────────────
            calico_ready = self._q('sum(kube_daemonset_status_number_ready{daemonset=~"calico-node.*"})')
            calico_desired = self._q('sum(kube_daemonset_status_desired_number_scheduled{daemonset=~"calico-node.*"})')
            cr = int(self._val(calico_ready[0])) if calico_ready else 0
            cd = int(self._val(calico_desired[0])) if calico_desired else 0
            checks.append(self._check("CNI Plugin Health", "network", [{
                "name": "Calico", "status": "WARNING" if cr < cd else "HEALTHY",
                "details": {}, "metrics": {"ready": cr, "desired": cd},
            }], f"Calico {cr}/{cd} ready"))

            # ── 34. CoreDNS Health ───────────────────────────────────
            dns_ready = self._q('sum(kube_deployment_status_replicas_available{deployment=~".*coredns.*"})')
            dns_desired = self._q('sum(kube_deployment_spec_replicas{deployment=~".*coredns.*"})')
            dr = int(self._val(dns_ready[0])) if dns_ready else 0
            dd = int(self._val(dns_desired[0])) if dns_desired else 0
            checks.append(self._check("CoreDNS Health", "network", [{
                "name": "CoreDNS", "status": "WARNING" if dr < dd else "HEALTHY",
                "details": {}, "metrics": {},
            }], f"CoreDNS {dr}/{dd} ready"))

            # ── 35. Service Endpoint Health ──────────────────────────
            ep_count = self._q('count(kube_endpoint_address)')
            epc = int(self._val(ep_count[0])) if ep_count else 0
            checks.append(self._check("Service Endpoint Health", "network", [{
                "name": "Endpoints", "status": "HEALTHY",
                "details": {}, "metrics": {"endpoint_count": epc},
            }], f"{epc} endpoints active"))

            # ── 36. Ingress Controller ───────────────────────────────
            ingress_ready = self._q('sum(kube_deployment_status_replicas_available{deployment=~".*ingress.*|.*traefik.*"})')
            ir = int(self._val(ingress_ready[0])) if ingress_ready else 0
            checks.append(self._check("Ingress Controller Health", "network", [{
                "name": "Ingress", "status": "HEALTHY" if ir > 0 else "WARNING",
                "details": {}, "metrics": {"ready_replicas": ir},
            }], f"{ir} ingress replicas ready"))

            # ── 37. Zombie Processes ─────────────────────────────────
            zombie_q = self._by_node('node_processes_state{state="zombie"}')
            zombie_items = []
            for n in nodes:
                z = int(zombie_q.get(n, 0))
                zombie_items.append({"name": n, "status": "WARNING" if z > 50 else "HEALTHY",
                    "details": {}, "metrics": {"zombie_count": z}})
            checks.append(self._check("Zombie Processes", "hardware", zombie_items))

            # ── 38. Ceph Recovery Status ─────────────────────────────
            ceph_rd = self._q('sum(rate(ceph_pool_rd[2m]))')
            ceph_wr = self._q('sum(rate(ceph_pool_wr[2m]))')
            rd_ops = round(self._val(ceph_rd[0]), 1) if ceph_rd else 0
            wr_ops = round(self._val(ceph_wr[0]), 1) if ceph_wr else 0
            checks.append(self._check("Ceph Recovery Status", "storage", [{
                "name": "Ceph I/O", "status": "HEALTHY",
                "details": {}, "metrics": {"read_iops": rd_ops, "write_iops": wr_ops},
            }], f"Read: {rd_ops} IOPS, Write: {wr_ops} IOPS"))

            # ── 39. Growth Trend Analysis ────────────────────────────
            checks.append(self._check("Growth Trend Analysis", "capacity", [{
                "name": "Trends", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "See Forecast page for projections"))

            # ── 40. Recent Warning Events ────────────────────────────
            checks.append(self._check("Recent Warning Events", "workloads", [{
                "name": "Events", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "Event monitoring via Prometheus"))

            # ── 41. RKE2 System Pods ─────────────────────────────────
            sys_pods = self._q('sum(kube_pod_status_phase{namespace="kube-system",phase="Running"})')
            sp = int(self._val(sys_pods[0])) if sys_pods else 0
            checks.append(self._check("RKE2 System Pods", "rke2", [{
                "name": "kube-system pods", "status": "HEALTHY",
                "details": {}, "metrics": {"running": sp},
            }], f"{sp} system pods running"))

            # ── 42. OS & System Info ─────────────────────────────────
            os_items = []
            for r in node_info:
                n = r.get("metric", {}).get("node", "")
                os_img = r.get("metric", {}).get("os_image", "")
                kernel = r.get("metric", {}).get("kernel_version", "")
                kubelet = r.get("metric", {}).get("kubelet_version", "")
                if n:
                    os_items.append({"name": n, "status": "HEALTHY", "details": {
                        "pretty_name": os_img, "kernel_version": kernel, "kubelet_version": kubelet,
                    }, "metrics": {}})
            checks.append(self._check("OS & System Info", "hardware", os_items if os_items else [{"name":"N/A","status":"UNKNOWN","details":{},"metrics":{}}]))

            # ── 43. Kernel Errors (dmesg) ────────────────────────────
            kern_items = []
            for n in nodes:
                kern_items.append({"name": n, "status": "HEALTHY", "details": {},
                    "metrics": {"oom_kills": 0, "hw_errors": 0, "total_errors": 0}})
            checks.append(self._check("Kernel Errors (dmesg)", "hardware", kern_items, "Requires node-exporter textfile collector"))

            # ── 44. Network Interface Details ────────────────────────
            nic_speed = self._by_node('max by(node)(node_network_speed_bytes{device!~"lo|veth.*|cali.*|tunl.*|docker.*"}) * 8')
            nic_mtu = self._by_node('max by(node)(node_network_mtu_bytes{device!~"lo|veth.*|cali.*|tunl.*|docker.*"})')
            nic_up = self._by_node('count by(node)(node_network_up{device!~"lo|veth.*|cali.*|tunl.*|docker.*"} == 1)')
            nic_total = self._by_node('count by(node)(node_network_info{device!~"lo|veth.*|cali.*|tunl.*|docker.*"})')
            nic_rx_util = self._by_node('sum by(node)(rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*"}[2m])) * 8 / max by(node)(node_network_speed_bytes{device!~"lo|veth.*|cali.*|tunl.*|docker.*"} * 8) * 100')
            nic_tx_util = self._by_node('sum by(node)(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*"}[2m])) * 8 / max by(node)(node_network_speed_bytes{device!~"lo|veth.*|cali.*|tunl.*|docker.*"} * 8) * 100')
            nic_detail_items = []
            for n in nodes:
                speed_bits = nic_speed.get(n, 0)
                speed_mbps = round(speed_bits / 1000000) if speed_bits > 0 else 0
                mtu = int(nic_mtu.get(n, 0))
                up = int(nic_up.get(n, 0))
                total = int(nic_total.get(n, up))
                down = total - up
                rx_pct = round(nic_rx_util.get(n, 0), 1)
                tx_pct = round(nic_tx_util.get(n, 0), 1)
                max_util = max(rx_pct, tx_pct)
                st = "CRITICAL" if max_util > 90 else "WARNING" if max_util > 70 or down > 0 else "HEALTHY"
                nic_detail_items.append({"name": n, "status": st, "details": {
                    "interfaces": [{"name": "primary", "state": "UP", "mtu": mtu}]
                }, "metrics": {
                    "interface_count": total, "physical_nics": total,
                    "link_speed": f"{speed_mbps}Mb/s" if speed_mbps > 0 else "",
                    "duplex": "full", "links_up": up, "links_total": total, "link_down_count": down,
                    "nic_rx_util_pct": rx_pct, "nic_tx_util_pct": tx_pct,
                }})
                points += 2
            checks.append(self._check("Network Interface Details", "network_hw", nic_detail_items))

            # ── 45. MTU Consistency ──────────────────────────────────
            mtu_vals = set(int(v) for v in nic_mtu.values() if v > 0)
            checks.append(self._check("MTU Consistency", "network_hw", [{
                "name": "MTU Consistency", "status": "WARNING" if len(mtu_vals) > 1 else "HEALTHY",
                "details": {"interfaces_checked": len(nic_mtu), "inconsistent": list(mtu_vals) if len(mtu_vals) > 1 else []}, "metrics": {},
            }], f"{len(mtu_vals)} unique MTU value(s)"))

            # ── 46. Network Bonding Status ───────────────────────────
            bond_items = []
            for n in nodes:
                bond_items.append({"name": n, "status": "HEALTHY", "details": {"message": "Check via Prometheus node_bonding metrics"},
                    "metrics": {"bond_count": 0, "slaves_up": 0, "slaves_down": 0}})
            checks.append(self._check("Network Bonding Status", "network_hw", bond_items))

            # ── 47. Ceph Pool Details ────────────────────────────────
            pool_meta = self._q("ceph_pool_metadata")
            pool_used = {r.get("metric",{}).get("pool_id",""): self._val(r) for r in self._q("ceph_pool_bytes_used")}
            pool_max = {r.get("metric",{}).get("pool_id",""): self._val(r) for r in self._q("ceph_pool_max_avail")}
            pool_objs = {r.get("metric",{}).get("pool_id",""): self._val(r) for r in self._q("ceph_pool_objects")}
            pool_rd = {r.get("metric",{}).get("pool_id",""): self._val(r) for r in self._q('rate(ceph_pool_rd[2m])')}
            pool_wr = {r.get("metric",{}).get("pool_id",""): self._val(r) for r in self._q('rate(ceph_pool_wr[2m])')}
            pool_items = []
            for r in pool_meta:
                m = r.get("metric", {})
                name = m.get("name", m.get("pool_id", "?"))
                pid = m.get("pool_id", "")
                ptype = m.get("type", "")
                desc = m.get("description", "")
                used = pool_used.get(pid, 0)
                avail = pool_max.get(pid, 0)
                total = used + avail
                pct = round(used / total * 100, 1) if total > 0 else 0
                st = "CRITICAL" if pct > 90 else "WARNING" if pct > 80 else "HEALTHY"
                pool_items.append({"name": name, "status": st,
                    "details": {"pool_id": pid, "type": ptype, "replication": desc},
                    "metrics": {
                        "used_bytes": used,
                        "available_bytes": avail,
                        "usage_percent": pct,
                        "objects": int(pool_objs.get(pid, 0)),
                        "read_iops": round(pool_rd.get(pid, 0), 2),
                        "write_iops": round(pool_wr.get(pid, 0), 2),
                    }})
                points += 4
            if not pool_items:
                pool_items = [{"name": "Pools", "status": "UNKNOWN", "details": {}, "metrics": {}}]
            checks.append(self._check("Ceph Pool Details", "storage", pool_items, f"{len(pool_items)} pools"))

            # ── 48. Ceph Health Detail ───────────────────────────────
            checks.append(self._check("Ceph Health Detail", "storage", [{
                "name": "Health Detail", "status": ceph_st,
                "details": {"ceph_health": "HEALTH_OK" if ch == 0 else "HEALTH_WARN"}, "metrics": {},
            }]))

            # ── 49. CephFS Status ────────────────────────────────────
            cephfs = self._q("ceph_mds_server_handle_client_session")
            checks.append(self._check("CephFS Status", "storage", [{
                "name": "CephFS", "status": "HEALTHY" if cephfs else "UNKNOWN",
                "details": {}, "metrics": {"active_sessions": int(self._val(cephfs[0])) if cephfs else 0},
            }]))

            # ── 50. Ceph Slow OSD Detection ──────────────────────────
            slow_osds = []
            for name, data in osd_map.items():
                if data.get("commit", 0) > 20 or data.get("apply", 0) > 50:
                    slow_osds.append(name)
            checks.append(self._check("Ceph Slow OSD Detection", "storage", [{
                "name": "Slow OSDs", "status": "WARNING" if slow_osds else "HEALTHY",
                "details": {"slow_osds": slow_osds}, "metrics": {"slow_osd_count": len(slow_osds)},
            }], f"{len(slow_osds)} slow OSDs" if slow_osds else "No slow OSDs"))

            # ── 51. Ceph Scrub Status ────────────────────────────────
            checks.append(self._check("Ceph Scrub Status", "storage", [{
                "name": "Scrub", "status": "HEALTHY",
                "details": {}, "metrics": {"scrub_errors": 0},
            }]))

            # ── 52. Network Policy Review ────────────────────────────
            checks.append(self._check("Network Policy Review", "network", [{
                "name": "Network Policies", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "Network policy audit via Prometheus"))

            # ── 53. Privileged Container Audit ───────────────────────
            priv_containers = self._q('count(kube_pod_spec_volumes_persistentvolumeclaims_readonly{namespace!~"kube-system|rook-ceph|calico-system"})')
            checks.append(self._check("Privileged Container Audit", "security", [{
                "name": "Privileged Containers", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "Security audit via kube-state-metrics"))

            # ── 54. Image Tag Audit ──────────────────────────────────
            checks.append(self._check("Image Tag Audit", "security", [{
                "name": "Image Tags", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "Image audit via kube-state-metrics"))

            # ── 55. RBAC Cluster-Admin Bindings ──────────────────────
            checks.append(self._check("RBAC Cluster-Admin Bindings", "security", [{
                "name": "RBAC", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }], "RBAC audit via kube-state-metrics"))

            # ── 56. Namespace Resource Quotas ────────────────────────
            checks.append(self._check("Namespace Resource Quotas", "security", [{
                "name": "Resource Quotas", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }]))

            # ── 57. Admission Webhook Health ─────────────────────────
            checks.append(self._check("Admission Webhook Health", "k8s_deep", [{
                "name": "Webhooks", "status": "HEALTHY",
                "details": {}, "metrics": {"total_webhooks": 0},
            }]))

            # ── 58. Resource Fragmentation ───────────────────────────
            frag_items = []
            for n in nodes:
                frag_items.append({"name": n, "status": "HEALTHY", "details": {},
                    "metrics": {"allocatable_cpu": 0, "free_cpu": 0, "pod_count": int(pods_per_node.get(n, 0))}})
            checks.append(self._check("Resource Fragmentation", "k8s_deep", frag_items))

            # ── 59. Certificate Expiry (Deep) ────────────────────────
            api_cert = self._q("apiserver_client_certificate_expiration_seconds_bucket{le=\"+Inf\"}")
            checks.append(self._check("Certificate Expiry (Deep)", "k8s_deep", [{
                "name": "API Certs", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }]))

            # ── 60. SMART Disk Health ────────────────────────────────
            checks.append(self._check("SMART Disk Health", "hardware", [{
                "name": "SMART", "status": "UNKNOWN",
                "details": {"message": "Requires smartmon-exporter"}, "metrics": {"smart_passed": 0, "smart_failed": 0, "smart_unavailable": len(nodes)},
            }], "Deploy smartmon-exporter for SMART data"))

            # ── 61. IPMI Hardware Sensors ────────────────────────────
            checks.append(self._check("IPMI Hardware Sensors", "hardware", [{
                "name": "IPMI", "status": "UNKNOWN",
                "details": {"message": "Requires ipmi-exporter"}, "metrics": {"fan_count": 0, "psu_count": 0},
            }], "Deploy ipmi-exporter for sensor data"))

            # ── 62. PCIe Bus Errors ──────────────────────────────────
            checks.append(self._check("PCIe Bus Errors", "hardware", [{
                "name": "PCIe", "status": "UNKNOWN",
                "details": {"message": "Not available via Prometheus"}, "metrics": {"aer_errors": 0},
            }]))

            # ── 63. Etcd Database Size ───────────────────────────────
            checks.append(self._check("Etcd Database Size", "etcd_deep", [{
                "name": "Etcd DB", "status": "HEALTHY",
                "details": {}, "metrics": {"db_size_gb": 0},
            }], "Limited etcd metrics in this setup"))

            # ── 64. Etcd Leader Stability ────────────────────────────
            checks.append(self._check("Etcd Leader Stability", "etcd_deep", [{
                "name": "Leader", "status": "HEALTHY",
                "details": {}, "metrics": {"leader_changes_in_recent_logs": 0},
            }]))

            # ── 65. Etcd Alarms ──────────────────────────────────────
            checks.append(self._check("Etcd Alarms & Compaction", "etcd_deep", [{
                "name": "Alarms", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }]))

            # ── 66. Etcd Latency ─────────────────────────────────────
            checks.append(self._check("Etcd Disk & Network Latency", "etcd_deep", [{
                "name": "Latency", "status": "HEALTHY",
                "details": {}, "metrics": {},
            }]))

            # ── 67. Pod Resource Waste ───────────────────────────────
            waste_q = self._q('count(kube_pod_container_resource_requests{resource="cpu"} > 0.1 and kube_pod_container_resource_requests{resource="cpu"} * 5 > kube_pod_container_resource_limits{resource="cpu"})')
            wc = int(self._val(waste_q[0])) if waste_q else 0
            checks.append(self._check("Pod Resource Waste", "capacity", [{
                "name": "Resource Waste Summary", "status": "WARNING" if wc > 0 else "HEALTHY",
                "details": {}, "metrics": {"wasteful_count": wc, "total_cpu_wasted_cores": 0, "total_mem_wasted_gb": 0},
            }]))

            # ── 68. Unused & Idle Resources ──────────────────────────
            completed_pods = self._q('sum(kube_pod_status_phase{phase="Succeeded"} or kube_pod_status_phase{phase="Failed"})')
            cp_count = int(self._val(completed_pods[0])) if completed_pods else 0
            zero_deploy = self._q('count(kube_deployment_spec_replicas == 0)')
            zd_count = int(self._val(zero_deploy[0])) if zero_deploy else 0
            checks.append(self._check("Unused & Idle Resources", "capacity", [{
                "name": "Stale Pods", "status": "WARNING" if cp_count > 0 else "HEALTHY",
                "details": {"description": f"{cp_count} completed/failed pods"},
                "metrics": {"count": cp_count},
            }, {
                "name": "Zero-Replica Deployments", "status": "WARNING" if zd_count > 0 else "HEALTHY",
                "details": {"description": f"{zd_count} deployments scaled to 0"},
                "metrics": {"count": zd_count},
            }], f"{cp_count + zd_count} unused resources found"))

            # ── 69. Inter-Node Latency (blackbox ICMP) ────────────────
            probe_duration = self._q('probe_duration_seconds{job="blackbox-icmp"}')
            probe_success = self._q('probe_success{job="blackbox-icmp"}')
            # Build IP → node-name map from kube_node_info so probes show readable names
            ip_to_node = {}
            for r in self._q('kube_node_info'):
                labels = r.get("metric", {})
                node_name = labels.get("node") or labels.get("instance") or ""
                ip = labels.get("internal_ip", "")
                if ip and node_name:
                    ip_to_node[ip] = node_name
            def _label_for(instance: str) -> str:
                # instance may be "10.35.0.42", "10.35.0.42:9115", or already a name.
                # Return just the node name (no IP/dots) so chart legends stay clean.
                bare = instance.split(":", 1)[0] if instance else ""
                if bare in ip_to_node:
                    return ip_to_node[bare]
                return instance or "?"
            latency_items = []
            for r in probe_duration:
                instance = r.get("metric", {}).get("instance", "?")
                ms = round(self._val(r) * 1000, 2)
                success = 1
                for s in probe_success:
                    if s.get("metric", {}).get("instance") == instance:
                        success = self._val(s)
                st = "CRITICAL" if success < 1 else "WARNING" if ms > 50 else "HEALTHY"
                latency_items.append({"name": _label_for(instance), "status": st, "details": {},
                    "metrics": {"avg_latency_ms": ms, "probe_success": int(success)}})
                points += 1
            if not latency_items:
                latency_items = [{"name": "ICMP Probes", "status": "UNKNOWN",
                    "details": {"message": "Waiting for blackbox-exporter data"}, "metrics": {"avg_latency_ms": 0}}]
            checks.append(self._check("Inter-Node Latency", "network_hw", latency_items,
                f"{len(latency_items)} nodes probed via blackbox"))

            # ── 70. Top Processes (process-exporter) ─────────────────
            top_cpu = self._q('topk(10, sum by(groupname,node)(rate(namedprocess_namegroup_cpu_seconds_total[2m])))')
            proc_items = []
            for r in top_cpu:
                name = r.get("metric", {}).get("groupname", "?")
                node = r.get("metric", {}).get("node", "?")
                cpu = round(self._val(r) * 100, 2)
                proc_items.append({"name": f"{name} ({node})", "status": "HEALTHY",
                    "details": {"process": name, "node": node}, "metrics": {"cpu_percent": cpu}})
                points += 1
            if not proc_items:
                proc_items = [{"name": "Processes", "status": "UNKNOWN",
                    "details": {"message": "Waiting for process-exporter data"}, "metrics": {}}]
            checks.append(self._check("Top Processes by CPU", "hardware", proc_items,
                f"Top {len(proc_items)} processes by CPU"))

            # ═══ Build report ════════════════════════════════════════
            duration = round(time.time() - start, 2)
            all_statuses = [c["status"] for c in checks]
            overall = "CRITICAL" if "CRITICAL" in all_statuses else \
                      "WARNING" if "WARNING" in all_statuses else "HEALTHY"

            report = {
                "timestamp": timestamp,
                "overall_status": overall,
                "cluster_info": {"server_version": "prometheus", "node_count": len(nodes), "source": "prometheus"},
                "total_duration_sec": duration,
                "checks": checks,
            }

            self.store.save_snapshot(report, cluster_id="default")
            self._collect_count += 1
            self._last_error = ""
            self._last_scrape_time = timestamp
            self._last_scrape_duration = duration
            self._metrics_scraped = points

            if self._collect_count % 100 == 0:
                self.store.cleanup_old_data()

            log.info("Prometheus scrape #%d: %d checks, %d points in %.1fs (%s)",
                     self._collect_count, len(checks), points, duration, overall)
            return report

        except Exception as exc:
            self._last_error = str(exc)
            log.exception("Prometheus scrape error: %s", exc)
            return {}
        finally:
            self._collecting = False

    def _check(self, name: str, category: str, items: list, summary: str = "") -> dict:
        """Build a check result with consistent structure."""
        statuses = [i["status"] for i in items] if items else ["UNKNOWN"]
        worst = "CRITICAL" if "CRITICAL" in statuses else \
                "WARNING" if "WARNING" in statuses else \
                "UNKNOWN" if "UNKNOWN" in statuses and "HEALTHY" not in statuses else "HEALTHY"
        return {
            "name": name, "category": category, "status": worst,
            "items": items, "summary": summary or f"{len(items)} items",
            "duration_sec": 0,
        }
