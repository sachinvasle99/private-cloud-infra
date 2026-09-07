# Monitoring — UsLab-Eng-01

The old-cluster monitoring stack, re-deployed for this cluster:

- **`devops-stack`** namespace → Prometheus (latest) + node-exporter +
  kube-state-metrics + **blackbox-exporter** + **process-exporter**.
- **`monitor`** namespace → the **krista RKE2 Deep Monitor** dashboard (custom
  FastAPI app; queries Prometheus over HTTP; SQLite on fast NVMe).

> **Changed for this cluster:** **Ceph monitoring removed, TopoLVM storage
> monitoring added** (this cluster has no Ceph). The dashboard's storage page is
> now *Local Storage (TopoLVM)* — per-node volume-group usage + CSI health. The
> code change lives in `../../Rke2_krista_monitoring/` and must be **rebuilt into
> the image** (below).

## Files

```
Monitoring/
├── 00-namespaces.yaml              devops-stack + monitor
├── prometheus-values.yaml          Helm values (server + node-exporter + KSM + scrape jobs)
├── exporters/
│   ├── blackbox-exporter.yaml      ICMP/TCP/HTTP probes (devops-stack)
│   └── process-exporter.yaml       per-node process metrics (devops-stack)
└── krista-monitor/
    └── krista-monitor.yaml         the dashboard (monitor ns, local-ssd-cheese PVC)
```

## Prerequisites

- TopoLVM up with `local-ssd-cheese` / `local-ssd-dough` (the PVCs pin pods there).
- MetalLB + NPM up (to expose the dashboard).
- `helm` + `kubectl` against the cluster.

## Deploy order

```bash
cd UsLab-Eng-01

# 1. Namespaces
kubectl apply -f Monitoring/00-namespaces.yaml

# 2. Prometheus + node-exporter + kube-state-metrics (latest charts)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install prometheus prometheus-community/prometheus \
  -n devops-stack -f Monitoring/prometheus-values.yaml
kubectl -n devops-stack rollout status deploy/prometheus-server

# 3. Exporters
kubectl apply -f Monitoring/exporters/blackbox-exporter.yaml
kubectl apply -f Monitoring/exporters/process-exporter.yaml

# 4. Build + push the krista monitor image to ECR (has the Ceph->TopoLVM change)
cd ../Rke2_krista_monitoring
aws ecr get-login-password --region us-west-2 --profile krista-prod \
  | docker login --username AWS --password-stdin 216499232680.dkr.ecr.us-west-2.amazonaws.com
docker buildx build --platform linux/amd64 \
  -t 216499232680.dkr.ecr.us-west-2.amazonaws.com/devops:rke2-monitor --push .
cd ../UsLab-Eng-01

# 5. ECR pull secret in the monitor namespace (nodes are on-prem, no IAM role)
kubectl -n monitor create secret docker-registry ecr-creds \
  --docker-server=216499232680.dkr.ecr.us-west-2.amazonaws.com \
  --docker-username=AWS \
  --docker-password="$(aws ecr get-login-password --region us-west-2 --profile krista-prod)"

# 6. Deploy the dashboard
kubectl apply -f Monitoring/krista-monitor/krista-monitor.yaml
kubectl -n monitor rollout status deploy/rke2-deep-monitor
```

> **ECR tokens expire every ~12 h.** The `ecr-creds` secret above goes stale, so a
> pod restart after 12 h hits `ImagePullBackOff`. Fix with `imagePullPolicy:
> IfNotPresent` (already set — a cached image still starts) **plus** a refresh
> CronJob that recreates the secret, e.g. every 8 h:
>
> ```yaml
> # a ServiceAccount with ECR auth runs: kubectl delete secret ecr-creds ... ;
> # kubectl create secret docker-registry ecr-creds --docker-password=$(aws ecr get-login-password ...)
> ```
> Or configure the **ECR credential helper** in each node's containerd. (Ask me
> and I'll generate the refresh CronJob.)

## Default config from the ConfigMap (Prometheus + Teams)

The data source and a Teams channel are now **seeded from the ConfigMap on first
boot** (`rke2-deep-monitor-config`), so the dashboard works out-of-the-box — no UI
clicks required:

| ConfigMap key | Seeds | Default |
|---------------|-------|---------|
| `PROMETHEUS_URL` | Data Sources → Prometheus | `http://prometheus-server.devops-stack.svc.cluster.local` |
| `PROMETHEUS_INTERVAL` | scrape interval | `30` |
| `TEAMS_WEBHOOK_URL` | Notification Channels → Teams | *(blank → no channel seeded)* |
| `TEAMS_CHANNEL_NAME` | channel name | `Teams` |
| `CLUSTER_NAME` | cluster name fallback | `uslab-eng-01` |

**How the override works:** seeding only happens when nothing is configured yet
(no data source / no channel by that name). After that the **UI is the source of
truth** — change the Prometheus URL or Teams webhook under
*Settings → Data Sources / Notification Channels* and it persists in the DB,
overriding the ConfigMap. To re-seed from config, clear it in the UI and restart
the pod. Set `TEAMS_WEBHOOK_URL` in the ConfigMap (or move it to a Secret) to get
Teams alerts automatically on deploy.

```bash
# Reach the UI (or expose via NPM — see below)
kubectl -n monitor port-forward svc/rke2-deep-monitor 8080:8080
# http://localhost:8080   login: admin / ADMIN_PASSWORD (set in the manifest)
# Settings → Cluster → Cluster Name → uslab-eng-01 (or rely on the seeded default)
```

## Expose via NPM (recommended)

Add an NPM Proxy Host:

| Field | Value |
|-------|-------|
| Domain | `monitor-uslab.eng.antbrains.com` |
| Scheme | `http` |
| Forward Hostname | `rke2-deep-monitor.monitor.svc.cluster.local` |
| Forward Port | `8080` |
| Websockets Support | **ON** (the dashboard uses `/ws`) |
| SSL | request cert, Force SSL |

🔒 Put an **NPM Access List** (IP allowlist / basic-auth) on it — the dashboard
has admin login but should not be open to the internet. See `../ACCESS-STRATEGY.md`.

## TopoLVM storage metrics (replaces Ceph)

The dashboard's storage checks now use TopoLVM's Prometheus metrics:
`topolvm_volumegroup_size_bytes` / `topolvm_volumegroup_available_bytes`
(per `node` + `device_class=ssd`). The `topolvm` scrape job in
`prometheus-values.yaml` collects them from `topolvm-system` pods (port 8080).

Verify they're present in Prometheus:
```bash
kubectl -n devops-stack port-forward svc/prometheus-server 9090:80
# http://localhost:9090 → query: topolvm_volumegroup_available_bytes
```
If empty, confirm the topolvm pods expose `/metrics` on 8080 (chart default). If a
newer chart uses a different port/needs enabling, set it in
`../TopoLVM/topolvm-values.yaml` and adjust the `topolvm` job's port regex.

## Traefik ingress monitoring (Prometheus → Grafana, traces → SigNoz)

Traefik is the cluster edge (`../Traefik/`). Its observability is split:

- **Metrics → Prometheus → Grafana.** `../Traefik/06-rke2-traefik-helmchartconfig.yaml`
  enables `metrics.prometheus` on the bundled `metrics` entrypoint
  (`:9100/metrics`, with entrypoint/router/service labels). The **`traefik`**
  scrape job in `prometheus-values.yaml` collects it from the rke2-traefik pods
  in `kube-system`. The **"Traefik Ingress"** Grafana dashboard
  (`grafana-dashboards/traefik.json`) renders RPS, status classes, latency
  quantiles, throughput, backend health, and TLS-cert expiry.
- **Traces → SigNoz.** The same HelmChartConfig sends OTLP/HTTP traces to the
  SigNoz collector at `https://collect-eng.antbrains.com/v1/traces`.

Apply / roll the Traefik change, then confirm metrics land in Prometheus:

```bash
kubectl apply -f ../Traefik/06-rke2-traefik-helmchartconfig.yaml
kubectl -n kube-system rollout status ds/rke2-traefik --timeout=300s

# re-render Prometheus with the new `traefik` scrape job
helm upgrade --install prometheus prometheus-community/prometheus \
  -n devops-stack -f Monitoring/prometheus-values.yaml

kubectl -n devops-stack port-forward svc/prometheus-server 9090:80 &
# http://localhost:9090 → Status→Targets: job "traefik" UP
#                       → query: sum(rate(traefik_entrypoint_requests_total[5m]))
```

> If `traefik` targets are missing, the bundled chart's metrics entrypoint port
> may differ from `9100` — check `kubectl -n kube-system get pod -l
> app.kubernetes.io/name=rke2-traefik -o jsonpath='{..containerPort}'` and adjust
> the `9100` regex in the `traefik` scrape job.

## Custom JSON dashboards (Grafana sidecar)

The hand-built dashboards in `grafana-dashboards/` (traefik, nodes-overview,
network, …) are loaded by Grafana's **dashboard sidecar** (enabled in
`grafana-values.yaml`), which watches `devops-stack` ConfigMaps labeled
`grafana_dashboard=1`. Create/refresh them from the JSON files:

```bash
for f in Monitoring/grafana-dashboards/*.json; do
  name="grafana-dash-$(basename "$f" .json)"
  kubectl -n devops-stack create configmap "$name" \
    --from-file="$(basename "$f")=$f" --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml \
  | kubectl apply -f -
done
# sidecar picks them up within ~60s — no Grafana restart needed.
```

> Run the loop again after editing any dashboard JSON; the sidecar reloads the
> changed ConfigMap automatically.

## node_exporter on the control plane

`roso` runs node_exporter via the DaemonSet's `tolerations: [{operator: Exists}]`
(set in `prometheus-values.yaml`), so control-plane metrics are collected too.

## Notes

- **Prometheus is single-replica** (RWO node-local TSDB on `dough`). Same SPOF
  caveat as NPM — fine for a lab; for HA use replicated storage or Thanos later.
- **No Ceph anywhere** — the old `ceph_mgr` data source and all Ceph checks are
  gone; storage health is per-node TopoLVM volume groups.
