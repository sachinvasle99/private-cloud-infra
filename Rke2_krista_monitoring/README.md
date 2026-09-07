# RKE2 Deep Monitor

Continuous health monitoring, alerting, and capacity planning for RKE2 Kubernetes clusters — powered by Prometheus.

![Version](https://img.shields.io/badge/version-5.0.0-blue)
![Checks](https://img.shields.io/badge/checks-70-green)
![Data Source](https://img.shields.io/badge/source-Prometheus-orange)

## What it does

RKE2 Deep Monitor runs **70 health checks every 30 seconds** using data from your existing Prometheus stack. No kubeconfig needed, no kubectl exec, no extra cluster permissions — just HTTP queries to Prometheus API.

- **Live Dashboard** — 17-page web UI with real-time charts, auto-refresh every 30s
- **Time-Series Metrics** — 7-day history with custom date/time range graphs
- **Custom Alerts** — configurable threshold rules with Teams / Slack / Discord / PagerDuty / Email (SMTP), per-rule channel routing, automatic resolved notifications
- **Configurable Cluster Name** — set from the Settings UI (no need to redeploy)
- **Remediation Suggestions** — actionable fix commands for every issue
- **Capacity Forecasting** — linear-trend projections, days until CPU / memory / disk / Ceph hit 100%
- **Cost Optimization** — detects unused pods, PVCs, deployments, idle resources
- **Certificate Monitoring** — expiry countdown with real expiry dates and cert-manager status notes
- **Ceph Storage Dashboard** — OSD-to-host mapping, device class, util bars, pool details (replication, IOPS, usage)
- **Hardware Monitoring** — temperature, NIC speed / actual Mb/s utilization, bandwidth (separate RX/TX/Total), link flaps
- **Authentication** — mandatory login with admin/read roles, password policy

## Architecture

```
┌────────────────────────────┐         ┌──────────────────────┐
│  rke2-deep-monitor pod     │  HTTP   │  Prometheus          │
│                            │◄────────│  (node_exporter,     │
│  Prometheus Scraper        │ PromQL  │   kube-state-metrics,│
│    → 70 checks / 30s       │         │   ceph_mgr)          │
│                            │         └──────────────────────┘
│  FastAPI + WebSocket       │
│  SQLite (7-day retention)  │
│  Alert Engine + Webhooks   │
│  Auth (admin/read roles)   │
│  Web Dashboard             │
└────────────────────────────┘
```

**No kubeconfig. No kubectl. No pod exec. Pure Prometheus.**

## Data Sources

| Source               | Metrics                                                          |
|----------------------|------------------------------------------------------------------|
| node_exporter        | CPU, memory, disk, temperature, NIC speed, bandwidth, FDs, load  |
| kube-state-metrics   | Pods, deployments, daemonsets, PVCs, PDBs, node status           |
| ceph_mgr             | Cluster health, OSDs, monitors, pools, PGs, IOPS                |
| apiserver metrics    | API latency, request duration                                    |
| cert-manager         | Certificate expiry timestamps                                    |

## Check Coverage (70 checks)

| Category           | Checks | What it monitors                                                 |
|--------------------|--------|------------------------------------------------------------------|
| Nodes              | 3      | Health, resource utilization, version consistency                |
| Hardware           | 8      | CPU/load, memory, disk, temperature, FDs, zombies, kernel, OS   |
| RKE2 Control Plane | 5      | API server, etcd, control plane pods, system pods, certificates  |
| Etcd Deep          | 4      | DB size, alarms, leader stability, latency                      |
| Storage (Ceph)     | 11     | Health, OSDs, monitors, pools, PGs, PVCs, recovery, scrub       |
| Network            | 5      | CNI, CoreDNS, ingress, endpoints, network policies              |
| Network Hardware   | 7      | NIC speed/utilization, errors, link flaps, bandwidth, MTU, bonds|
| Security           | 6      | Privileged containers, RBAC, resource limits, quotas, PDBs      |
| Workloads          | 8      | Pods, deployments, daemonsets, statefulsets, OOM, evictions      |
| K8s Deep           | 4      | Webhooks, fragmentation, scheduling, cert expiry                 |
| Hardware Deep      | 3      | SMART, IPMI, PCIe (requires exporters)                          |
| Capacity           | 5      | Headroom, pod capacity, waste, idle resources, top processes      |

## Dashboard Pages

| Page             | Description                                                              |
|------------------|--------------------------------------------------------------------------|
| Overview         | Cluster status, node charts, check distribution, active issues           |
| Nodes            | Per-server cards with IP, CPU, mem, disk, temp, NIC speed/utilization    |
| Server Metrics   | 10 pre-built time-series charts with node filter and custom date range   |
| Custom Metrics   | Query any metric with custom date/time picker                            |
| Unused Resources | Stale pods, zero-replica deployments, unused PVCs, idle pods             |
| Ceph Storage     | Ceph health, OSD table, monitors, pools, PGs, IOPS                      |
| Forecast         | Capacity projections — days until resources exhausted                    |
| Certificates     | TLS cert expiry countdown                                                |
| Checks           | All 70 check results, filterable by category and status                  |
| Remediation      | Fix suggestions with kubectl commands                                    |
| Alerts           | Custom alert rules (create/edit/delete from UI), active alerts           |
| History          | 7-day status timeline and snapshot log                                   |
| Settings         | Users, data sources, backup/restore, system info                         |
| Docs             | Built-in documentation with glossary and metrics reference               |

## Quick Start

### Prerequisites

Your cluster needs:
- **Prometheus** with node_exporter, kube-state-metrics
- **Ceph manager** metrics endpoint (if using Rook-Ceph)
- **node_exporter on ALL nodes** (including control plane — add toleration for NoExecute taints)
- **A fast PVC for the dashboard's SQLite DB** — the workload writes ~370 rows every 30 s into a multi-GB DB. On Ceph RBD or other network-attached storage this can pin the asyncio loop in disk-wait. Use a local-NVMe storage class (e.g. `postgres-fast-ssd`) and size the PVC for ~3 GB / week of retention.

### Deploy

```bash
# 1. Build and push image
docker build -t your-registry/rke2-deep-monitor:latest .
docker push your-registry/rke2-deep-monitor:latest

# 2. Deploy to cluster
kubectl create namespace rke2-monitor
kubectl apply -f deploy/

# 3. Access dashboard
kubectl -n rke2-monitor port-forward svc/rke2-deep-monitor 8080:8080

# 4. Login and configure from the UI
#    Username: admin / Password: (set via ADMIN_PASSWORD env)
#
#    Settings → Cluster → Cluster Name:        e.g. "prod-east", "lumens-lab"
#
#    Settings → Data Sources → Add:
#      Name: Prometheus
#      Type: prometheus
#      URL:  http://prometheus-server.your-namespace:80
#      Interval: 30
#
#    Settings → Notification Channels → Add:
#      Slack:  https://hooks.slack.com/services/...
#      Teams:  https://outlook.office.com/webhook/...
#      Email:  smtp://user:pass@smtp.gmail.com:587?from=alerts@x.com&to=ops@x.com
```

## Configuration

### Environment variables (set at deploy time)

| Environment Variable       | Default            | Description                                          |
|----------------------------|--------------------|------------------------------------------------------|
| `CLUSTER_NAME`             | (none)             | **Fallback only.** Preferred location is Settings → Cluster in the UI. |
| `RETENTION_DAYS`           | `7`                | Days to keep metric data                             |
| `ADMIN_PASSWORD`           | `admin123`         | Initial admin password (change immediately!)         |
| `DB_PATH`                  | `/app/data/metrics.db` | SQLite database path                             |
| `LOG_LEVEL`                | `INFO`             | DEBUG, INFO, WARNING, ERROR                          |
| `ALERT_COOLDOWN`           | `300`              | Seconds between repeat alerts for the same rule      |

> Notification channels (Teams / Slack / Discord / PagerDuty / Email / generic webhook) are now configured **entirely from the UI** under Settings → Notification Channels. The legacy `ALERT_WEBHOOK_URL` env var is no longer required and is ignored.

### Runtime settings (configured from the UI, stored in the database)

| Setting              | Where                                  | Notes                                                                  |
|----------------------|----------------------------------------|------------------------------------------------------------------------|
| Cluster name         | Settings → Cluster → Cluster Name      | Appears in alert titles and report headers. Overrides `CLUSTER_NAME`.  |
| Notification channels| Settings → Notification Channels       | Add/edit/test/delete. Per-channel category filter and enable toggle.   |
| Alert rules          | Alerts page                            | Threshold, operator, message template, target channels, on/off toggle. |
| Data sources         | Settings → Data Sources                | Prometheus URL and scrape interval.                                    |
| Users                | Settings → User Management (admin)     | Create/delete users, change passwords, set role.                       |

## Alerts & Notifications

### How alerts work

1. Define rules on the **Alerts** page (e.g. `memory_percent > 80 warning, > 90 critical`).
2. Every scrape (default 30s), the engine evaluates rules against collected metrics.
3. When a threshold trips, the alert is shown on the dashboard and dispatched to each channel mapped to that rule. The cluster name (Settings → Cluster) is included in every notification title.
4. When the metric goes back below threshold, a `RESOLVED` notification is sent through the same channels.

### Cluster name

Configure once at **Settings → Cluster → Cluster Name**. The value is persisted in the dashboard's database and overrides the legacy `CLUSTER_NAME` env var. It appears in alert titles and report headers.

### Step 1 — Add a notification channel

Go to **Settings → Notification Channels**. Each channel needs a name, a URL, and an optional category filter (blank = all). The channel transport is auto-detected from the URL.

| Type             | Detected by                     | URL format                                                                  |
|------------------|---------------------------------|-----------------------------------------------------------------------------|
| Microsoft Teams  | `office.com` / `microsoft.com`  | `https://outlook.office.com/webhook/...`                                    |
| Slack            | `hooks.slack.com`               | `https://hooks.slack.com/services/T.../B.../...`                            |
| Discord          | `discord` in URL                | `https://discord.com/api/webhooks/<id>/<token>`                             |
| PagerDuty        | `pagerduty.com`                 | `https://events.pagerduty.com/v2/enqueue` (routing key in URL)              |
| Email (SMTP)     | scheme `smtp://` or `smtps://`  | `smtp://user:pass@host:587?from=a@x.com&to=b@y.com[,c@y.com]`               |
| Generic          | any other `https://`            | Any URL accepting JSON POST — receives `{text, alert}`                      |

#### Microsoft Teams

1. In Teams, open the channel → **...** next to its name → **Workflows** → **Post to a channel when a webhook request is received**.
2. Sign in, pick the team and channel, click **Add workflow**, copy the URL.
3. Dashboard: **Settings → Notification Channels** → name `Infra Alerts`, paste the URL, leave categories blank (all) or set e.g. `storage,nodes`. **Add** → **Test**.

#### Slack

1. <https://api.slack.com/apps> → **Create New App** → *From scratch* → pick a workspace.
2. **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace** → choose channel → **Allow**.
3. Copy the webhook URL (`https://hooks.slack.com/services/...`) and paste it into a new dashboard channel.

#### Email (SMTP)

Pack the SMTP settings into a single URL — the dashboard parses host, port, credentials, sender, and recipients.

```
# Gmail (STARTTLS, 587 — uses an app password, not your account password):
smtp://alerts@example.com:<app-password>@smtp.gmail.com:587?from=alerts@example.com&to=ops@example.com,sec@example.com

# Office 365 (STARTTLS, 587):
smtp://alerts@yourtenant.onmicrosoft.com:<password>@smtp.office365.com:587?from=alerts@yourtenant.onmicrosoft.com&to=ops@example.com

# SES SMTP / SendGrid / generic SSL on port 465:
smtps://<smtp-user>:<smtp-pass>@email-smtp.us-east-1.amazonaws.com:465?from=alerts@yourdomain.com&to=ops@yourdomain.com

# Internal relay with no auth, no TLS:
smtp://relay.internal.local:25?from=alerts@x&to=ops@x&starttls=0
```

Query parameters: `from` (required) — sender address; `to` (required) — comma-separated recipients; `starttls=1|0` — force or skip STARTTLS on plain `smtp://` (default `1`). Use `smtps://` for implicit SSL on port 465. URL-encode special characters in the password (e.g. `@` → `%40`, space → `%20`).

### Step 2 — Route a rule to specific channels

When creating or editing a rule, the **Notify Channels** multi-select chooses which channels receive that rule's alerts. Hold **Ctrl/Cmd** to select multiple. If empty, alerts fall back to category-based routing using each channel's category filter.

### Notification levels

| Level           | Behavior                                                       |
|-----------------|----------------------------------------------------------------|
| Both            | Notify on warning and critical                                 |
| Critical Only   | Notify only when critical threshold breached                   |
| Dashboard Only  | Show on dashboard, skip all external notifications             |

### Message templates

Placeholders available in the **Message** field:

| Placeholder   | Replaced with                                  |
|---------------|------------------------------------------------|
| `{value}`     | Metric value that tripped the rule             |
| `{item}`      | Node or item name (e.g. `master-1`)            |
| `{metric}`    | Metric name (e.g. `cpu_percent`)               |
| `{threshold}` | Threshold value the metric exceeded            |

### Cooldown & resolved notifications

The same alert won't refire within `ALERT_COOLDOWN` seconds (default 300). When the metric goes back under threshold, a `RESOLVED` message is sent through the same channels the alert originally fired on, with a green banner.

### Testing a channel

Each row in **Settings → Notification Channels** has a **Test** button that dispatches a synthetic `info`-severity alert through the channel's transport — verifies connectivity, formatting, and credentials without waiting for a real incident.

## Authentication

Mandatory. Two roles:

| Role      | Access                                                           |
|-----------|------------------------------------------------------------------|
| **Admin** | Full access — manage users, alert rules, data sources, backup    |
| **Read**  | View-only — see all dashboards and data, cannot modify anything  |

### Password Policy

- Minimum 8 characters
- At least 1 uppercase, 1 lowercase, 1 number, 1 special character

## Node Exporter Setup

All nodes (including control plane) need node_exporter for full metrics. If your control plane node is missing:

```bash
# Check if node_exporter DaemonSet tolerates all taints
kubectl -n <prometheus-namespace> get ds <node-exporter-ds> -o jsonpath='{.spec.template.spec.tolerations}'

# Patch to tolerate ALL taints (including NoExecute on control plane)
kubectl -n <prometheus-namespace> patch daemonset <node-exporter-ds> \
  --type='json' -p='[{"op":"replace","path":"/spec/template/spec/tolerations","value":[{"operator":"Exists"}]}]'
```

## Security

- No kubeconfig required — reads only from Prometheus HTTP API
- Container runs as non-root (UID 1000)
- Authentication is mandatory (cannot be disabled)
- Session cookies are HTTP-only
- Password hashing with salt

## API Reference

| Method | Endpoint                    | Description                |
|--------|-----------------------------|----------------------------|
| GET    | `/api/status`               | Cluster status summary     |
| GET    | `/api/overview`             | Dashboard overview data    |
| GET    | `/api/checks`               | All check results          |
| GET    | `/api/metrics`              | Time-series query          |
| GET    | `/api/metrics/available`    | List all metric names      |
| GET    | `/api/alerts`               | Active alerts              |
| GET    | `/api/alert-rules`          | Custom alert rules         |
| POST   | `/api/alert-rules/create`   | Create rule                |
| PUT    | `/api/alert-rules/{id}`     | Update rule                |
| DELETE | `/api/alert-rules/{id}`     | Delete rule                |
| GET    | `/api/idle`                 | Unused resources           |
| GET    | `/api/forecast`             | Capacity projections       |
| GET    | `/api/certificates`         | Cert expiry data           |
| GET    | `/api/waste`                | Pod resource waste         |
| GET    | `/api/datasources`          | Data source config         |
| POST   | `/api/datasources/create`   | Add data source            |
| POST   | `/api/datasources/test`     | Test connectivity          |
| GET    | `/api/prometheus/status`    | Prometheus scraper status  |
| GET    | `/api/report/html`          | Download HTML report       |
| GET    | `/api/report/json`          | Download JSON report       |
| GET    | `/api/backup`               | Download DB backup         |
| POST   | `/api/restore`              | Restore DB from upload     |
| POST   | `/api/collect`              | Trigger scrape             |
| GET    | `/api/stats`                | Database statistics        |
| GET    | `/api/users`                | List users (admin)         |
| POST   | `/api/users/create`         | Create user (admin)        |
| POST   | `/api/login`                | Login                      |
| POST   | `/api/logout`               | Logout                     |
| WS     | `/ws`                       | Live WebSocket updates     |
