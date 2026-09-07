# UsLab-Eng-01 — Traefik ingress (Phase 2) — RKE2 bundled addon + kube-vip

Enables in-cluster ingress the **same way as Pune**: RKE2's **bundled
`rke2-traefik` addon**, tuned via a **`HelmChartConfig`**, exposed on a
**kube-vip ingress VIP (`10.35.0.61`)**, with **cert-manager / Let's Encrypt**
for TLS. **Replaces NPM** as the edge (downtime accepted — cluster is new).

```
client → kube-vip VIP 10.35.0.61 (TCP 80/443)      → rke2-traefik (DaemonSet, 4 app nodes) → TLS → Ingress → app svc
client → kube-vip VIP 10.35.0.62 (UDP 443 / HTTP-3) → rke2-traefik QUIC (07-traefik-quic-udp.yaml)
```

## How RKE2 Traefik works here (important)

- RKE2 **bundles** Traefik as the `rke2-traefik` addon (chart
  `rke2-traefik-34.2.00x` from `rke2-charts.rancher.io`, owned by a
  `k3s.cattle.io Addon`). The bundled DaemonSet/Service live in **`kube-system`**.
- **Enable** it by removing `rke2-traefik` from roso's `config.yaml` `disable:`.
- **Tune** it with a **`HelmChartConfig`** named `rke2-traefik` in `kube-system`
  (`06-...yaml`) — helm-controller merges it over the bundled chart. (This is
  Pune's mechanism. Do **not** use a standalone `HelmChart` of the same name.)

> ⚠️ **INCIDENT/LESSON (2026-06-24):** uncommenting `ingress-controller: traefik`
> in `config.yaml` crashed `rke2-server` (it's a K3s key, invalid in RKE2) and
> took roso's control plane down. Keep that line **commented**. The only valid
> change is removing `- rke2-traefik` from `disable:`. See `roso-config-snippet.yaml`.

## IP plan

| IP | Before | After |
|----|--------|-------|
| `10.35.0.60` | API VIP | API VIP (untouched) |
| `10.35.0.61` | NPM proxy | **kube-vip → rke2-traefik (TCP 80/443)** |
| `10.35.0.62` | NPM admin | **kube-vip → rke2-traefik HTTP/3 QUIC (UDP 443)** |
| MetalLB pool | `.61–.99` | **`.63–.99`** (`.61` + `.62` removed) |

## Progress (what is DONE on the live cluster)

- ✅ `config.yaml`: `rke2-traefik` removed from `disable:`; `rke2-server` restarted; API healthy.
- ✅ Bundled `rke2-traefik` deployed; IngressClass `traefik` is cluster default.
- ✅ `06-rke2-traefik-helmchartconfig.yaml` applied → Traefik on **4 app nodes only**
  (off roso), tuned (TLS 1.2+ ciphers, timeouts, upstream pool, dashboard API),
  service = **LoadBalancer** with kube-vip class + `.61` annotation
  → currently **EXTERNAL-IP `<pending>`** (waiting for kube-vip + `.61` free).
- ✅ NPM still serving `.61` — **edge intact, zero disruption so far.**

## Cutover — DONE (2026-06-24)

- ✅ App nodes labeled `kube-vip/iface=eno1`; MetalLB shrunk to `.63–.99`; NPM removed.
- ✅ kube-vip deployed → Traefik svc on **`.61`** (TCP), QUIC svc `rke2-traefik-quic` on **`.62`** (UDP).
- ✅ ClusterIssuers ready; ingresses applied (grafana, monitoring, rancher, **eng-01-proxy**).
- ✅ ~~**cert-manager `hostAliases` fix applied** (`09-...`) → HTTP-01 self-check passes despite no NAT hairpin.~~
  **SUPERSEDED 2026-06-25:** firewall **hairpin NAT enabled** (LAN→`209.245.232.1`→`.61`),
  so the in-cluster HTTP-01 self-check now reaches the public IP directly. The
  `hostAliases` patch was **removed** from the cert-manager deployment and a
  staging HTTP-01 cert re-issued in ~24s to confirm. `08`/`09` are no longer
  applied (kept in-repo only as reference for non-hairpin networks).
- ✅ Certs ISSUED & serving valid LE certs (all `True`):
  - `eng-01.krista.app` (+studio/extension) → `proxy:8080` — HTTP 200
  - `grafana.eng.antbrains.com` → `grafana:80` — HTTP 302 (login)
  - `monitor-eng.antbrains.com` → `monitoring-service:80` (PMM) — HTTP 302
  - `rancher-uslab.eng.antbrains.com` → `rancher:443` (HTTPS backend) — HTTP 200
- ✅ **Rancher fix:** HTTPS self-signed backend required `--serversTransport.insecureSkipVerify=true`
  (global, in `06-...`; matches Pune). Per-service serversTransport alone was not honored.
- 📝 Every public host needs a **public A record → 209.245.232.1** for LE to validate (proven: hosts
  pointing at private 10.x IPs fail with "no valid A records"). Once the A record is set, delete the
  failed Order (`kubectl -n <ns> delete order -l cert-manager.io/certificate-name=<cert>`) to retry now.
- ⏳ HTTP/3: served on `.62`; needs the edge **UDP/443 → .62** rule (per-host works once its cert is valid).

## Cutover commands (for reference / prod replication)

```bash
export KUBECONFIG=/Users/kiranmane/Documents/uslab-Eng-01.yaml
cd UsLab-Eng-01/Traefik

# 1. Label app nodes for kube-vip (NIC eno1)
kubectl label node cheese dough rosatini nicks kube-vip/iface=eno1

# 2. Shrink MetalLB so it won't hand out .61
kubectl apply -f 00-metallb-pool.yaml

# 3. Free .61/.62 — remove NPM (config PVC stays for rollback)
kubectl -n npm scale deploy/npm --replicas=0
kubectl -n npm delete svc npm-proxy npm-admin

# 4. kube-vip — claims .61 for the traefik LoadBalancer (class-scoped; MetalLB ignores)
kubectl apply -f 01-kube-vip.yaml
kubectl apply -f 02-kube-vip-cloud-provider.yaml
kubectl -n kube-system rollout status ds/kube-vip-ds --timeout=120s
kubectl -n kube-system get svc rke2-traefik -o wide   # EXTERNAL-IP should become 10.35.0.61

# 5. cert-manager issuers (email already set: devops_internal@kristasoft.com)
kubectl apply -f 03-cluster-issuer.yaml

# 6. HTTP/3 QUIC UDP VIP (.62)
kubectl apply -f 07-traefik-quic-udp.yaml
kubectl -n kube-system get svc rke2-traefik-quic -o wide    # EXTERNAL-IP 10.35.0.62

# 7. cert-manager issuers (email already set: devops_internal@kristasoft.com)
kubectl apply -f 03-cluster-issuer.yaml

# 8. (optional) dashboard  — fill <BASE64_HTPASSWD> first
kubectl apply -f 05-traefik-dashboard.yaml

# 9. App ingresses (test with letsencrypt-staging first, then prod)
kubectl apply -f 10-ingress-examples.yaml

# 10. Repoint DNS/edge for each host (grafana/rancher/…): TCP 443 → .61, UDP 443 → .62
```

## Observability (metrics + tracing)

`06-rke2-traefik-helmchartconfig.yaml` turns on:

- **Prometheus metrics** — `metrics.prometheus` on the bundled `metrics`
  entrypoint (`:9100/metrics`), with entrypoint/router/service labels. Scraped by
  the `traefik` job in `../Monitoring/prometheus-values.yaml`; visualized by the
  **"Traefik Ingress"** Grafana dashboard (`../Monitoring/grafana-dashboards/traefik.json`).
- **OTLP tracing → SigNoz** — sends traces to the SigNoz collector at
  `https://collect-eng.antbrains.com/v1/traces` (OTLP/HTTP over 443). If the
  collector ingress fronts the receiver under a path prefix, adjust the
  `tracing.otlp.http.endpoint` value.

See `../Monitoring/README.md` ("Traefik ingress monitoring") for the apply +
verify steps.

## HTTP/3 (QUIC)

The bundled chart (34.x) does **not** add a UDP/443 port to the main service
(verified: only `web 80/TCP` + `websecure 443/TCP`) — same limitation Pune hit.
So HTTP/3 is served by a **separate kube-vip UDP LoadBalancer on VIP `.62`**
(`07-traefik-quic-udp.yaml`), targeting the traefik pods' QUIC listener
(container UDP 8443). The HelmChartConfig sets `http3.advertisedPort=443`, so
browsers see `Alt-Svc: h3=":443"` and send QUIC to the same hostname:443 — point
the edge/DNS **UDP/443 → .62** (TCP/443 stays `.61`). This is Pune's exact pattern.

## Verify

```bash
kubectl -n kube-system get svc rke2-traefik -o wide            # EXTERNAL-IP 10.35.0.61
kubectl -n kube-system get pods -l app.kubernetes.io/name=rke2-traefik -o wide
curl -sI --resolve grafana.eng.antbrains.com:443:10.35.0.61 \
  https://grafana.eng.antbrains.com/ | grep -iE '^HTTP'
kubectl get certificate -A
```

## Rollback

| Step | Rollback |
|---|---|
| HelmChartConfig tuning bad | `kubectl delete -f 06-rke2-traefik-helmchartconfig.yaml` (reverts to bundled defaults) |
| Disable Traefik entirely | re-add `- rke2-traefik` to roso `disable:` + `systemctl restart rke2-server` |
| Need NPM back | re-expand pool to `.61-.99`; `kubectl -n npm scale deploy/npm --replicas=1` + recreate its LB svcs |
| kube-vip issue | `kubectl delete -f 02-... -f 01-...` |
| Anything | API VIP `.60:6443` is never touched → kubectl access never at risk |

## Files (prod-replication set)

| File | Purpose |
|------|---------|
| `roso-config-snippet.yaml` | The roso `config.yaml` edit (remove `rke2-traefik` from `disable:`) + the incident lesson |
| `06-rke2-traefik-helmchartconfig.yaml` | **Tunes the bundled Traefik** (kube-vip LB `.61`, app-nodes-only, TLS1.2+ ciphers, timeouts) — ✅ applied |
| `00-metallb-pool.yaml` | Shrink MetalLB pool to `.62-.99` |
| `01-kube-vip.yaml` | kube-vip DaemonSet + RBAC (app nodes, class-scoped) |
| `02-kube-vip-cloud-provider.yaml` | kube-vip IPAM (`.61` + `.62`) |
| `07-traefik-quic-udp.yaml` | kube-vip UDP LoadBalancer on `.62` for HTTP/3 QUIC |
| `03-cluster-issuer.yaml` | Let's Encrypt prod + staging (email `devops_internal@kristasoft.com`) |
| `05-traefik-dashboard.yaml` | Traefik dashboard at `traefik.eng.antbrains.com` (basic-auth + LE), `kube-system`. NOTE: the IngressRoute MUST carry `kubernetes.io/ingress.class: traefik` or the CRD provider (run with `ingressClass=traefik`) ignores it → 404. |
| `12-inframonitor-ingress.yaml` | `inframonitor.eng.antbrains.com` → `monitor/rke2-deep-monitor:8080` |
| `10-ingress-examples.yaml` | Grafana / monitoring / Rancher Ingresses (Rancher → HTTPS backend) |

## To replicate in PROD

1. Edit `/etc/rancher/rke2/config.yaml` on the prod master: remove `- rke2-traefik`
   from `disable:` (keep `rke2-ingress-nginx` disabled; **never** uncomment
   `ingress-controller:`). `systemctl restart rke2-server`; wait for API.
2. `kubectl apply -f 06-rke2-traefik-helmchartconfig.yaml` (adjust the `.61`
   annotation + nodeSelector label to prod's VIP/nodes).
3. Do the cutover steps above (label nodes, MetalLB pool, kube-vip, issuers,
   ingresses, DNS), with prod IPs/hostnames.

> Supersedes `../Traefik.phase2/` (the earlier HAProxy+hostPort draft).
