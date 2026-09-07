"""
Remediation engine — maps check results to actionable fix suggestions.

Each rule inspects check items and produces remediation suggestions with:
- severity (critical / warning / info)
- description of the problem
- suggested fix (human-readable)
- command (optional shell/kubectl command to apply)
"""

import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)


class Remediation:
    """Single remediation suggestion."""

    def __init__(
        self,
        check_name: str,
        item_name: str,
        severity: str,
        title: str,
        description: str,
        fix: str,
        command: str = "",
        category: str = "",
    ):
        self.check_name = check_name
        self.item_name = item_name
        self.severity = severity
        self.title = title
        self.description = description
        self.fix = fix
        self.command = command
        self.category = category

    def to_dict(self) -> Dict:
        return {
            "check_name": self.check_name,
            "item_name": self.item_name,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "fix": self.fix,
            "command": self.command,
            "category": self.category,
        }


def generate_remediations(report: Dict[str, Any]) -> List[Dict]:
    """
    Analyze a QC report and return remediation suggestions for all
    WARNING and CRITICAL items.
    """
    remediations = []

    for check in report.get("checks", []):
        check_name = check.get("name", "")
        category = check.get("category", "")

        for item in check.get("items", []):
            status = item.get("status", "HEALTHY")
            if status == "HEALTHY":
                continue

            item_name = item.get("name", "")
            details = item.get("details", {})
            metrics = item.get("metrics", {})
            severity = "critical" if status == "CRITICAL" else "warning"

            # Run through all remediation rules
            for rule in _RULES:
                result = rule(check_name, item_name, severity, details, metrics, category)
                if result:
                    remediations.append(result.to_dict())

    # Deduplicate by title
    seen = set()
    unique = []
    for r in remediations:
        key = (r["check_name"], r["item_name"], r["title"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort: critical first, then warning
    priority = {"critical": 0, "warning": 1, "info": 2}
    unique.sort(key=lambda r: priority.get(r["severity"], 3))

    return unique


# ── Remediation Rules ────────────────────────────────────────────────────
# Each rule is a function that returns a Remediation or None.

def _rule_node_not_ready(check_name, item_name, severity, details, metrics, category):
    if check_name != "Node Health & Readiness":
        return None
    if details.get("ready") == "False" or details.get("ready_status") == "False":
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Node Not Ready",
            description=f"Node {item_name} is not in Ready state.",
            fix="Check kubelet status and node conditions. Look for disk pressure, memory pressure, or PID pressure.",
            command=f"kubectl describe node {item_name} | grep -A5 Conditions",
        )
    return None


def _rule_node_high_cpu(check_name, item_name, severity, details, metrics, category):
    if "Resource" not in check_name:
        return None
    cpu = metrics.get("cpu_percent", 0)
    if cpu >= 90:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Critical CPU Usage",
            description=f"Node {item_name} CPU usage is at {cpu:.1f}%.",
            fix="Identify top CPU consumers and consider scaling out, adding resource limits, or migrating workloads.",
            command=f"kubectl top pods --all-namespaces --sort-by=cpu | head -20",
        )
    elif cpu >= 70:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="High CPU Usage",
            description=f"Node {item_name} CPU usage is at {cpu:.1f}%.",
            fix="Monitor CPU trends. Consider horizontal pod autoscaling or node autoscaling.",
            command=f"kubectl top nodes {item_name}",
        )
    return None


def _rule_node_high_memory(check_name, item_name, severity, details, metrics, category):
    if "Resource" not in check_name:
        return None
    mem = metrics.get("memory_percent", 0)
    if mem >= 90:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Critical Memory Usage",
            description=f"Node {item_name} memory usage is at {mem:.1f}%.",
            fix="Identify memory-heavy pods. Check for memory leaks. Consider eviction or scaling.",
            command=f"kubectl top pods --all-namespaces --sort-by=memory | head -20",
        )
    elif mem >= 75:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="High Memory Usage",
            description=f"Node {item_name} memory usage is at {mem:.1f}%.",
            fix="Review pod memory requests/limits. Consider adding nodes or setting tighter limits.",
            command=f"kubectl describe node {item_name} | grep -A10 'Allocated resources'",
        )
    return None


def _rule_disk_pressure(check_name, item_name, severity, details, metrics, category):
    if "Disk" not in check_name and "Hardware" not in check_name:
        return None
    usage = metrics.get("max_fs_usage_pct", metrics.get("usage_percent", 0))
    if usage >= 90:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Disk Nearly Full",
            description=f"Disk usage on {item_name} is at {usage:.1f}%.",
            fix="Clean up unused images, old logs, and temp files. Expand disk if possible.",
            command="crictl rmi --prune && journalctl --vacuum-size=500M",
        )
    elif usage >= 80:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="High Disk Usage",
            description=f"Disk usage on {item_name} is at {usage:.1f}%.",
            fix="Monitor disk growth. Schedule cleanup of unused container images and old logs.",
            command="df -h / && crictl images | wc -l",
        )
    return None


def _rule_pod_crashloop(check_name, item_name, severity, details, metrics, category):
    if check_name != "Pod Health Overview":
        return None
    phase = details.get("phase", "")
    reason = details.get("reason", "")
    restarts = metrics.get("restart_count", details.get("restart_count", 0))
    if "CrashLoopBackOff" in str(reason) or (isinstance(restarts, (int, float)) and restarts > 10):
        ns = details.get("namespace", "default")
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Pod CrashLoopBackOff",
            description=f"Pod {item_name} is crash-looping with {restarts} restarts.",
            fix="Check pod logs and events for the root cause. Common issues: misconfiguration, missing secrets, OOM.",
            command=f"kubectl logs {item_name} -n {ns} --tail=50 && kubectl describe pod {item_name} -n {ns}",
        )
    return None


def _rule_pod_pending(check_name, item_name, severity, details, metrics, category):
    if check_name != "Pod Health Overview":
        return None
    phase = details.get("phase", "")
    if phase == "Pending":
        ns = details.get("namespace", "default")
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Pod Stuck in Pending",
            description=f"Pod {item_name} is in Pending state.",
            fix="Check events for scheduling failures. Common causes: insufficient resources, node affinity, taints.",
            command=f"kubectl describe pod {item_name} -n {ns} | grep -A10 Events",
        )
    return None


def _rule_deployment_degraded(check_name, item_name, severity, details, metrics, category):
    if check_name != "Deployment Health":
        return None
    ready = details.get("ready_replicas", 0)
    desired = details.get("desired_replicas", 0)
    if isinstance(ready, (int, float)) and isinstance(desired, (int, float)) and ready < desired:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="Deployment Under-replicated",
            description=f"Deployment {item_name} has {ready}/{desired} replicas ready.",
            fix="Check pod events and node capacity. The deployment may need more resources or nodes.",
            command=f"kubectl rollout status deployment/{item_name} --timeout=30s",
        )
    return None


def _rule_ceph_health(check_name, item_name, severity, details, metrics, category):
    if check_name != "Ceph Cluster Health":
        return None
    health = details.get("ceph_health", "")
    usage = metrics.get("usage_percent", 0)
    if "HEALTH_ERR" in str(health):
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Ceph Cluster in ERROR State",
            description=f"Ceph cluster reports {health}. Storage usage: {usage:.1f}%.",
            fix="Check Ceph health detail for specific issues. Common: PGs degraded, OSDs down, near-full.",
            command="kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail",
        )
    elif "HEALTH_WARN" in str(health):
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Ceph Cluster Warning",
            description=f"Ceph cluster reports {health}. Storage usage: {usage:.1f}%.",
            fix="Review Ceph warnings. Common: clock skew, too few PGs, slow ops.",
            command="kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail",
        )
    return None


def _rule_ceph_capacity(check_name, item_name, severity, details, metrics, category):
    if "Ceph" not in check_name:
        return None
    usage = metrics.get("usage_percent", 0)
    if usage >= 90:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Ceph Storage Nearly Full",
            description=f"Ceph storage usage is at {usage:.1f}%.",
            fix="Immediately free space by deleting unused PVCs, snapshots, or expanding OSDs.",
            command="kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph df detail",
        )
    elif usage >= 80:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Ceph Storage Getting Full",
            description=f"Ceph storage usage is at {usage:.1f}%.",
            fix="Plan capacity expansion. Add new OSDs or clean up unused persistent volumes.",
            command="kubectl get pvc --all-namespaces --sort-by=.status.capacity.storage",
        )
    return None


def _rule_osd_down(check_name, item_name, severity, details, metrics, category):
    if check_name != "Ceph OSD Status":
        return None
    status_str = details.get("status", "")
    if "down" in str(status_str).lower():
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="OSD Down",
            description=f"OSD {item_name} is down.",
            fix="Check the OSD pod logs and node health. The disk may have failed or the node is unreachable.",
            command=f"kubectl -n rook-ceph logs -l ceph-osd-id={item_name.replace('osd.', '')} --tail=50",
        )
    return None


def _rule_osd_high_latency(check_name, item_name, severity, details, metrics, category):
    if check_name != "Ceph OSD Status":
        return None
    commit = metrics.get("commit_latency_ms", 0)
    apply = metrics.get("apply_latency_ms", 0)
    if commit > 50 or apply > 100:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="High OSD Latency",
            description=f"OSD {item_name}: commit={commit}ms, apply={apply}ms.",
            fix="Check disk health and I/O contention. SSD/NVMe should have <10ms commit. Consider rebalancing.",
            command="kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd perf",
        )
    return None


def _rule_etcd_db_size(check_name, item_name, severity, details, metrics, category):
    if "Etcd" not in check_name or "Database" not in check_name and "DB" not in check_name and "Size" not in check_name:
        return None
    db_gb = metrics.get("db_size_gb", metrics.get("db_size_bytes", 0) / (1024**3))
    if db_gb >= 4:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Etcd Database Too Large",
            description=f"Etcd DB size is {db_gb:.1f} GB (limit is typically 8 GB).",
            fix="Compact and defragment etcd. Check for high event churn or excessive secrets.",
            command="etcdctl compact $(etcdctl endpoint status -w json | jq '.[0].Status.header.revision') && etcdctl defrag",
        )
    elif db_gb >= 2:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Etcd Database Growing Large",
            description=f"Etcd DB size is {db_gb:.1f} GB.",
            fix="Schedule compaction and defragmentation. Review event-heavy workloads.",
            command="etcdctl endpoint status -w table",
        )
    return None


def _rule_etcd_leader_changes(check_name, item_name, severity, details, metrics, category):
    if "Leader" not in check_name:
        return None
    changes = metrics.get("leader_changes", details.get("leader_changes", 0))
    if isinstance(changes, (int, float)) and changes >= 5:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="Frequent Etcd Leader Elections",
            description=f"Detected {changes} leader changes.",
            fix="Check network stability between control plane nodes. Verify disk I/O performance (etcd needs fast fsync).",
            command="journalctl -u rke2-server --since '1 hour ago' | grep -i 'leader'",
        )
    return None


def _rule_cert_expiry(check_name, item_name, severity, details, metrics, category):
    if "Certificate" not in check_name and "Cert" not in check_name:
        return None
    days = metrics.get("days_until_expiry", details.get("days_until_expiry", 999))
    note = details.get("note", "")
    ready = details.get("ready", True)

    if note and "stale" in note.lower():
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Certificate Metric Stale",
            description=f"Certificate {item_name} has stale expiry data in Prometheus. Cert-manager shows ready={ready}.",
            fix="Restart cert-manager to refresh metrics, or check certificate status directly.",
            command=f"kubectl get certificate -A | grep {item_name.split('/')[-1]}\nkubectl -n cert-manager rollout restart deployment cert-manager",
        )

    if isinstance(days, (int, float)) and days <= 30:
        return Remediation(
            check_name=check_name, item_name=item_name,
            severity="critical" if days <= 7 else "warning",
            category=category,
            title="Certificate Expiring Soon",
            description=f"Certificate {item_name} expires in {days} days.",
            fix="Check cert-manager for renewal status. Force renewal if stuck.",
            command=f"kubectl get certificate {item_name.split('/')[-1]} -n {item_name.split('/')[0]} -o wide\nkubectl cert-manager renew {item_name.split('/')[-1]} -n {item_name.split('/')[0]}",
        )
    return None


def _rule_privileged_container(check_name, item_name, severity, details, metrics, category):
    if check_name != "Privileged Container Audit":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="warning",
        category=category,
        title="Privileged Container Detected",
        description=f"Container {item_name} is running in privileged mode.",
        fix="Review if privileged mode is necessary. Use SecurityContext with specific capabilities instead.",
        command=f"kubectl get pod {item_name} -o jsonpath='{{.spec.containers[*].securityContext}}'",
    )


def _rule_no_resource_limits(check_name, item_name, severity, details, metrics, category):
    if check_name != "Resource Limits & Requests":
        return None
    missing = details.get("missing_limits", details.get("missing", ""))
    if missing:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Missing Resource Limits",
            description=f"Pod {item_name} has no CPU/memory limits set.",
            fix="Set resource requests and limits to prevent resource contention and OOM kills.",
            command="# Add to pod spec:\n# resources:\n#   requests:\n#     cpu: 100m\n#     memory: 128Mi\n#   limits:\n#     cpu: 500m\n#     memory: 512Mi",
        )
    return None


def _rule_latest_image_tag(check_name, item_name, severity, details, metrics, category):
    if check_name != "Image Tag Audit":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="warning",
        category=category,
        title="Using 'latest' or Untagged Image",
        description=f"Container {item_name} uses an unspecific image tag.",
        fix="Pin images to specific versions/digests for reproducibility and security.",
        command="# Change: image: myapp:latest\n# To:     image: myapp:v1.2.3@sha256:abc...",
    )


def _rule_oom_killed(check_name, item_name, severity, details, metrics, category):
    if check_name != "OOM Killed Detection":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="critical",
        category=category,
        title="Pod OOM Killed",
        description=f"Pod {item_name} was terminated due to Out of Memory.",
        fix="Increase memory limits or optimize application memory usage. Check for memory leaks.",
        command=f"kubectl describe pod {item_name} | grep -A5 'Last State'",
    )


def _rule_probe_failure(check_name, item_name, severity, details, metrics, category):
    if check_name != "Probe Failure Monitoring":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="warning",
        category=category,
        title="Health Probe Failing",
        description=f"Pod {item_name} has failing liveness/readiness probes.",
        fix="Check the probe endpoint, timeout settings, and application startup time.",
        command=f"kubectl get pod {item_name} -o jsonpath='{{.spec.containers[*].livenessProbe}}'",
    )


def _rule_scheduling_failure(check_name, item_name, severity, details, metrics, category):
    if check_name != "Scheduling Failures":
        return None
    reason = details.get("reason", "")
    return Remediation(
        check_name=check_name, item_name=item_name, severity=severity,
        category=category,
        title="Pod Cannot Be Scheduled",
        description=f"Pod {item_name} is unschedulable: {reason}.",
        fix="Check node resources, taints/tolerations, and node affinity rules.",
        command=f"kubectl describe pod {item_name} | grep -A10 Events",
    )


def _rule_dns_latency(check_name, item_name, severity, details, metrics, category):
    if check_name != "DNS Resolution":
        return None
    latency = metrics.get("latency_ms", 0)
    if latency > 200:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Very High DNS Latency",
            description=f"DNS resolution latency is {latency:.0f}ms.",
            fix="Check CoreDNS pod health and resource limits. Consider scaling CoreDNS replicas.",
            command="kubectl -n kube-system get pods -l k8s-app=kube-dns && kubectl -n kube-system top pods -l k8s-app=kube-dns",
        )
    elif latency > 50:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Elevated DNS Latency",
            description=f"DNS resolution latency is {latency:.0f}ms.",
            fix="Monitor CoreDNS metrics. Consider increasing cache size or adding replicas.",
            command="kubectl -n kube-system logs -l k8s-app=kube-dns --tail=20",
        )
    return None


def _rule_zombie_processes(check_name, item_name, severity, details, metrics, category):
    if "Zombie" not in check_name:
        return None
    count = metrics.get("zombie_count", 0)
    if isinstance(count, (int, float)) and count > 50:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="Excessive Zombie Processes",
            description=f"Node {item_name} has {count} zombie processes.",
            fix="Identify parent processes not reaping children. May indicate application bugs.",
            command=f"ps aux | awk '$8 ~ /Z/ {{print $0}}'  # Run on node {item_name}",
        )
    return None


def _rule_high_fd_usage(check_name, item_name, severity, details, metrics, category):
    if "File Descriptor" not in check_name:
        return None
    usage = metrics.get("fd_usage_percent", metrics.get("usage_percent", 0))
    if isinstance(usage, (int, float)) and usage >= 70:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="High File Descriptor Usage",
            description=f"Node {item_name} FD usage is at {usage:.1f}%.",
            fix="Identify processes with high FD counts. May need to increase system limits.",
            command="sysctl fs.file-max && cat /proc/sys/fs/file-nr",
        )
    return None


def _rule_network_policy_missing(check_name, item_name, severity, details, metrics, category):
    if check_name != "Network Policy Check":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="info",
        category=category,
        title="No Network Policies Defined",
        description=f"Namespace {item_name} has no network policies.",
        fix="Define NetworkPolicy resources to restrict pod-to-pod traffic for defense in depth.",
        command=f"kubectl get networkpolicy -n {item_name}",
    )


def _rule_pdb_missing(check_name, item_name, severity, details, metrics, category):
    if check_name != "Pod Disruption Budgets":
        return None
    return Remediation(
        check_name=check_name, item_name=item_name, severity="warning",
        category=category,
        title="Missing Pod Disruption Budget",
        description=f"Deployment {item_name} has no PDB configured.",
        fix="Create a PodDisruptionBudget to ensure availability during node maintenance.",
        command=f"# Example PDB:\n# apiVersion: policy/v1\n# kind: PodDisruptionBudget\n# metadata:\n#   name: {item_name}-pdb\n# spec:\n#   minAvailable: 1\n#   selector:\n#     matchLabels:\n#       app: {item_name}",
    )


def _rule_temperature_high(check_name, item_name, severity, details, metrics, category):
    if "Temperature" not in check_name:
        return None
    temp = metrics.get("max_temp_celsius", 0)
    if isinstance(temp, (int, float)) and temp >= 75:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="High Hardware Temperature",
            description=f"Node {item_name} temperature is {temp}°C.",
            fix="Check cooling system, airflow, and ambient temperature. May need to reduce workload.",
            command=f"sensors  # Run on node {item_name}",
        )
    return None


def _rule_dmesg_errors(check_name, item_name, severity, details, metrics, category):
    if "Dmesg" not in check_name:
        return None
    error_count = metrics.get("error_count", 0)
    oom_count = metrics.get("oom_count", 0)
    if isinstance(oom_count, (int, float)) and oom_count > 0:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="critical",
            category=category,
            title="Kernel OOM Events Detected",
            description=f"Node {item_name} has {oom_count} OOM events in kernel log.",
            fix="Review memory limits across all pods. The node is running out of memory.",
            command=f"dmesg | grep -i 'oom\\|out of memory'  # Run on node {item_name}",
        )
    if isinstance(error_count, (int, float)) and error_count >= 10:
        return Remediation(
            check_name=check_name, item_name=item_name, severity="warning",
            category=category,
            title="Kernel Log Errors",
            description=f"Node {item_name} has {error_count} error/critical messages in dmesg.",
            fix="Review dmesg for hardware errors, driver issues, or filesystem problems.",
            command=f"dmesg --level=err,crit,alert,emerg | tail -20  # Run on node {item_name}",
        )
    return None


def _rule_pod_capacity(check_name, item_name, severity, details, metrics, category):
    if "Pod" not in check_name or "Capacity" not in check_name:
        return None
    pct = metrics.get("pod_usage_percent", 0)
    if isinstance(pct, (int, float)) and pct >= 80:
        return Remediation(
            check_name=check_name, item_name=item_name, severity=severity,
            category=category,
            title="Pod Capacity Running Low",
            description=f"Node {item_name} is at {pct:.0f}% pod capacity.",
            fix="Increase max-pods kubelet setting or add more nodes to the cluster.",
            command=f"kubectl describe node {item_name} | grep -E 'Capacity|Allocatable' -A5",
        )
    return None


# ── Rule registry ────────────────────────────────────────────────────────

_RULES = [
    _rule_node_not_ready,
    _rule_node_high_cpu,
    _rule_node_high_memory,
    _rule_disk_pressure,
    _rule_pod_crashloop,
    _rule_pod_pending,
    _rule_deployment_degraded,
    _rule_ceph_health,
    _rule_ceph_capacity,
    _rule_osd_down,
    _rule_osd_high_latency,
    _rule_etcd_db_size,
    _rule_etcd_leader_changes,
    _rule_cert_expiry,
    _rule_privileged_container,
    _rule_no_resource_limits,
    _rule_latest_image_tag,
    _rule_oom_killed,
    _rule_probe_failure,
    _rule_scheduling_failure,
    _rule_dns_latency,
    _rule_zombie_processes,
    _rule_high_fd_usage,
    _rule_network_policy_missing,
    _rule_pdb_missing,
    _rule_temperature_high,
    _rule_dmesg_errors,
    _rule_pod_capacity,
]
