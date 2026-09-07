# PVC Backup Guide — RKE2 + Thick TopoLVM → MinIO → S3

*Backup, tiered retention, date-tagged snapshots, and restore runbook.*
**Mover:** VolSync (restic) · **copyMethod:** Direct · Incremental + deduplicated

---

## 1. Overview and constraints

This guide covers backing up Kubernetes PersistentVolumeClaims from an RKE2 cluster backed by **thick-provisioned TopoLVM** to a **MinIO** instance on a separate server, with older backups tiered to AWS S3.

**The defining constraint:** thick TopoLVM has **no CSI snapshot and no clone support** — both are thin-pool-only in TopoLVM. Anything that relies on `VolumeSnapshot` or PVC cloning will not work.

Consequences:

- Backups are taken at the **filesystem level** by reading the live PVC.
- `copyMethod: Direct` is mandatory for every VolSync source — no point-in-time copy is created.
- Volume-level backups are **crash-consistent only**. Databases need quiescing or logical dumps (§8).
- TopoLVM volumes are **RWO and node-local** (`WaitForFirstConsumer`). The mover runs on the same node as the app pod; a backup can't run while the volume is detached or its node is down.

**Tool choice:** VolSync with the restic mover. restic gives encrypted, deduplicated, incremental backups; MinIO is a drop-in S3 backend; AWS S3 is the long-term archive tier.

---

## 2. Architecture

One restic repository **per PVC** (a distinct path under the bucket) — scopes dedup, retention, and prune per volume and keeps restores unambiguous.

```
RKE2 cluster (thick TopoLVM)
  app-ns
   app pod  ── mounts ──  PVC (thick TopoLVM, RWO, node-local)
   VolSync mover (copyMethod: Direct) ── reads same PVC on same node
        │  restic: dedup + encrypt + incremental
        ▼
  MinIO  (separate server)   ── hot tier, last 3 days
   bucket: k8s-backups / <namespace> / <pvc> /
        │  restic copy (daily) OR MinIO ILM transition
        ▼
  AWS S3 (ap-south-1)        ── archive tier, long retention
```

---

## 3. Prerequisites

- VolSync operator installed and healthy (§5).
- Reachable MinIO endpoint with an account able to create buckets and policies.
- Network path open from each app namespace to `MINIO_HOST:9000` (or `:443` if TLS-fronted). With default-deny NetworkPolicies, allow mover egress (§12).
- `mc` (MinIO client) and `restic` CLI on a workstation for verification and out-of-band restores.
- An AWS S3 bucket (e.g. `ap-south-1`) for the archive tier, with its own credentials.

---

## 4. MinIO setup

### 4.1 Bucket and dedicated user

```bash
mc alias set backups https://MINIO_HOST:9000 MINIO_ROOT_USER MINIO_ROOT_PASS
mc mb backups/k8s-backups

cat > k8s-backups-policy.json <<'POLICY'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["s3:ListBucket","s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::k8s-backups"] },
    { "Effect": "Allow",
      "Action": ["s3:PutObject","s3:GetObject","s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::k8s-backups/*"] }
  ]
}
POLICY

mc admin policy create  backups k8s-backups-rw k8s-backups-policy.json
mc admin user add       backups volsync-backup 'STRONG_SECRET_KEY'
mc admin policy attach  backups k8s-backups-rw --user volsync-backup
```

### 4.2 Versioning and lifecycle (safety net)

```bash
mc version enable backups/k8s-backups
mc ilm rule add backups/k8s-backups --noncurrent-expire-days 30
mc ilm rule add backups/k8s-backups --expire-delete-marker
```

> Backup retention is enforced primarily by **restic** (§11), not by object expiry. Don't set an object-expiry that deletes restic pack files restic still references.

### 4.3 Object locking (optional, ransomware resistance)

```bash
mc mb --with-lock backups/k8s-backups
mc retention set --default GOVERNANCE 30d backups/k8s-backups
```

Object lock changes how restic prune reclaims space — test prune before committing to COMPLIANCE mode.

---

## 5. Install / verify VolSync

```bash
# verify
kubectl get deploy -n volsync-system
kubectl get pods   -n volsync-system
kubectl api-resources | grep volsync

# install (Helm) if absent
helm repo add backube https://backube.github.io/helm-charts/
helm repo update
helm install volsync backube/volsync -n volsync-system --create-namespace
```

Confirm the restic mover image is pullable on every node that hosts PVCs (colo/air-gapped nodes may need it mirrored to your registry).

---

## 6. Restic repository secret

One Secret per PVC, since the per-PVC repo path lives in `RESTIC_REPOSITORY`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: restic-minio
  namespace: <app-ns>
type: Opaque
stringData:
  RESTIC_REPOSITORY: s3:https://MINIO_HOST:9000/k8s-backups/<app-ns>/<pvc-name>
  RESTIC_PASSWORD: <repo-encryption-passphrase>
  AWS_ACCESS_KEY_ID: volsync-backup
  AWS_SECRET_ACCESS_KEY: STRONG_SECRET_KEY
```

- `s3:https://...` when MinIO has TLS; `s3:http://...` otherwise.
- For a private CA, supply it via `restic.customCA` or TLS verification fails.
- **Store `RESTIC_PASSWORD` in your secrets manager.** Losing it makes every backup in that repo unrecoverable.

---

## 7. Backup — non-database PVCs

For file/config/log/upload volumes, `copyMethod: Direct` is sufficient:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: <pvc-name>-backup
  namespace: <app-ns>
spec:
  sourcePVC: <pvc-name>
  trigger:
    schedule: "0 */6 * * *"        # every 6 hours
  restic:
    repository: restic-minio
    copyMethod: Direct             # mandatory for thick TopoLVM
    pruneIntervalDays: 14
    cacheCapacity: 2Gi
    cacheStorageClassName: <topolvm-sc>
    retain:
      hourly: 6
      daily: 7
      weekly: 4
      monthly: 3
```

`cacheStorageClassName` should point at your thick TopoLVM StorageClass — the mover needs a small node-local PVC for restic metadata cache.

---

## 8. Backup — databases (consistency matters)

`Direct` reads files while the app writes, so a raw volume backup of a running DB is only crash-consistent and may restore into a state needing recovery, or fail. Two supported approaches.

### Option A — Quiesce with copy-triggers (volume backup, app-coordinated)

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <db-pvc>
  namespace: <app-ns>
  annotations:
    volsync.backube/use-copy-trigger: ""
```

Your automation watches for `latest-copy-status: WaitingForTrigger`, flushes/locks the DB, then sets a unique value on `volsync.backube/copy-trigger` to release it. Respond within ~10 minutes or the cycle errors (it keeps reconciling).

### Option B — Logical dump, then back up the dump (recommended for DBs)

Avoids the consistency problem. Dump to MinIO directly (note the date-tagged object name, expanded in §9):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: pg-dump-to-minio
  namespace: <app-ns>
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pgdump
              image: postgres:16
              command: ["/bin/sh","-c"]
              args:
                - >
                  set -euo pipefail;
                  TS=$(date +%Y%m%d-%H%M%S);
                  pg_dump -Fc -d "$PGDATABASE" |
                  mc pipe "backups/k8s-backups/<app-ns>/pgdump/${PGDATABASE}-${TS}.dump";
                  echo "dump ${TS} complete"
              env:
                - name: PGHOST
                  value: <db-service>
                - name: PGUSER
                  valueFrom: { secretKeyRef: { name: <db-secret>, key: username } }
                - name: PGPASSWORD
                  valueFrom: { secretKeyRef: { name: <db-secret>, key: password } }
                - name: PGDATABASE
                  value: <dbname>
                - name: MC_HOST_backups
                  value: https://volsync-backup:STRONG_SECRET_KEY@MINIO_HOST:9000
```

Database-specific notes:

- **ClickHouse (SigNoz):** use `clickhouse-backup` to MinIO or `BACKUP TABLE ... TO Disk('s3',...)`; raw volume copies are not reliably restorable while running.
- **ActiveMQ Artemis:** stop the broker or quiesce via copy-trigger; journal files are not safely copyable live. For HA pairs, back up the active node's journal in a maintenance window.
- **Elasticsearch / OpenSearch:** use the native snapshot API to an S3 repository pointed at MinIO, not volume backups.

**Rule of thumb:** Direct volume backup for stateless/file data; native dump or snapshot tooling for any database.

---

## 9. Storing daily backups with a date tag

Two layers to "store everyday data with a date tag": restic already timestamps every snapshot, and you can additionally name or tag backups explicitly by date. Use whichever matches how you want to find and restore them.

### 9.1 restic snapshots are already date stamped (recommended)

Every backup VolSync runs becomes a restic snapshot carrying its own timestamp. A daily schedule produces one dated restore point per day automatically — you don't have to name anything.

```yaml
# one snapshot per day
spec:
  trigger:
    schedule: "0 2 * * *"      # daily at 02:00
  restic:
    repository: restic-minio
    copyMethod: Direct
    retain:
      daily: 30                # keep 30 dated daily restore points
      weekly: 8
      monthly: 12
```

List and restore by date from a workstation:

```bash
export RESTIC_REPOSITORY=s3:https://MINIO_HOST:9000/k8s-backups/<ns>/<pvc>
export RESTIC_PASSWORD=...   AWS_ACCESS_KEY_ID=volsync-backup   AWS_SECRET_ACCESS_KEY=...

restic snapshots                         # shows ID + date/time for each day
restic restore <snapshotID> --target ./restore-2026-06-29
restic restore --time "2026-06-29 02:00:00" latest --target ./restore
```

From VolSync, restore a specific day with `restoreAsOf` on the ReplicationDestination (§14):

```yaml
  restic:
    repository: restic-minio
    destinationPVC: <pvc>
    copyMethod: Direct
    restoreAsOf: "2026-06-29T23:59:59Z"   # newest snapshot at/under this time
    # previous: 1                          # N snapshots before that point
```

### 9.2 Explicit date tags on restic snapshots

If you want to query snapshots by an explicit tag rather than by time, add a date tag. VolSync's restic mover doesn't expose arbitrary `--tag` values per run, so for tagged snapshots run restic itself on a schedule (CronJob) instead of relying on the mover:

```bash
# daily CronJob step (restic CLI) — tags each snapshot with the calendar date
restic backup /data \
  --tag daily \
  --tag "$(date +%F)"          # e.g. --tag 2026-06-30

# later: find / restore by tag
restic snapshots --tag "2026-06-29"
restic restore --tag "2026-06-29" latest --target ./restore
```

Use this only if you specifically need tag-based selection; for most cases the built-in timestamps in 9.1 are simpler.

### 9.3 Date-named objects (for dump-based backups)

Logical dumps are plain objects, so the clearest date tag is the object key itself. Lay them out by date so a day's data is obvious and lifecycle rules are easy:

```bash
# date-partitioned key layout in the bucket
k8s-backups/<ns>/pgdump/2026/06/30/<db>-20260630-020000.dump
k8s-backups/<ns>/pgdump/2026/06/29/<db>-20260629-020000.dump

# the CronJob line that produces it
TS=$(date +%Y%m%d-%H%M%S); DPATH=$(date +%Y/%m/%d);
pg_dump -Fc -d "$PGDATABASE" |
  mc pipe "backups/k8s-backups/<ns>/pgdump/${DPATH}/${PGDATABASE}-${TS}.dump"
```

Then a MinIO lifecycle rule can expire/transition the dated prefix on its own schedule, e.g.
`mc ilm rule add backups/k8s-backups --prefix <ns>/pgdump/ --expire-days 90`.

> **Note:** do not date-partition the restic repo objects this way — restic controls its own internal layout. Date-named objects apply to dumps and other plain-file backups, not to the restic pack store.

---

## 10. Tiered retention — 3 days on MinIO, archive on S3

**Why you cannot just move old objects:** a restic repository is one coherent set of pack/index/snapshot files, and dedup means an old pack often still holds blocks today's snapshot needs. Relocating "objects older than 3 days" to another backend splits one repo across two stores and breaks restores from both. "Keep last 3 days" is a **snapshot-retention** policy in restic, not an object-age rule.

### Pattern B — two repos: MinIO (3-day hot) + S3 (archive) — *recommended*

VolSync backs up to MinIO with 3-day retention; a job copies snapshots to a separate S3 repo with long retention. Cross-repo dedup is preserved by seeding the S3 repo with MinIO's chunker params.

**1. MinIO source keeps ~3 days:**

```yaml
spec:
  sourcePVC: <pvc>
  trigger: { schedule: "0 2 * * *" }
  restic:
    repository: restic-minio
    copyMethod: Direct
    pruneIntervalDays: 7
    retain:
      daily: 3                 # ~last 3 days on MinIO
```

**2. Initialise the S3 archive repo sharing MinIO's chunker params:**

```bash
restic -r s3:https://s3.ap-south-1.amazonaws.com/my-archive/<ns>/<pvc> \
  init --copy-chunker-params \
  --repo2 s3:https://MINIO_HOST:9000/k8s-backups/<ns>/<pvc>
```

**3. Daily CronJob: copy MinIO → S3, then enforce archive retention.** Run it near MinIO (or on the MinIO host) so colo→AWS transfer happens once, not duplicated from the cluster:

```bash
export S3_REPO=s3:https://s3.ap-south-1.amazonaws.com/my-archive/<ns>/<pvc>
export MINIO_REPO=s3:https://MINIO_HOST:9000/k8s-backups/<ns>/<pvc>
export RESTIC_PASSWORD=...

restic -r $S3_REPO copy --from-repo $MINIO_REPO     # pull new daily snapshots
restic -r $S3_REPO forget --keep-within 90d --prune  # archive retention
```

Result: MinIO holds 3 days, S3 holds 90 (or whatever you set), each independently restorable.

### Pattern A — MinIO transparent tiering to S3 (one repo, cheaper cold bytes)

MinIO transitions old object data to an S3 remote tier while keeping the namespace/metadata local; restic still sees one whole repo and MinIO fetches tiered objects on read.

```bash
mc ilm tier add s3 backups S3COLD \
  --endpoint https://s3.ap-south-1.amazonaws.com \
  --bucket my-archive --prefix k8s-backups/ \
  --access-key AKIA... --secret-key ... \
  --region ap-south-1 --storage-class STANDARD_IA

mc ilm rule add backups/k8s-backups \
  --transition-tier S3COLD --transition-days 3 \
  --noncurrent-transition-tier S3COLD --noncurrent-transition-days 3
```

- This is a **cost** move, not a retention boundary — restic retention still governs how many snapshots exist.
- Restoring tiered (cold) data incurs S3 retrieval latency/cost.
- **Do not put your own lifecycle or Glacier rules on that S3 bucket** — MinIO manages those objects and external mutation/expiry corrupts transparent retrieval.

### Pattern C — bucket replication / mirror (simplest, least control)

One-way replicate the MinIO repo bucket to S3 (`mc mirror` without `--remove`, or MinIO bucket replication) and let MinIO prune to 3 days locally; S3 accumulates everything. Workable as a crude offsite mirror, but you get one retention knob and the S3 copy drifts from a clean restic retention model.

### Choosing

| Goal | Pattern |
|------|---------|
| MinIO stays small + clean long-term archive on S3, each restorable | **B (recommended)** |
| One simple store, just cheaper storage for old bytes | A |
| Minimal moving parts, crude offsite copy | C |

---

## 11. Scheduling and retention strategy

Map restic retention to tiers (aligns with 3-2-1-1-0: copies on cluster + MinIO, off-box on S3, versioning/lock as the immutable copy, verified restores as the "0 errors"):

| Data class | Schedule | retain h/d/w/m | pruneIntervalDays |
|------------|----------|----------------|-------------------|
| Critical app/config | every 6h | 6 / 14 / 8 / 6 | 7 |
| Standard file volumes | daily | 0 / 7 / 4 / 3 | 14 |
| Logs / low value | daily | 0 / 3 / 1 / 0 | 14 |
| DB logical dumps | daily | via MinIO ILM on dated prefix | n/a |

- restic dedup makes frequent backups of slowly-changing volumes cheap.
- `pruneIntervalDays` controls repack frequency (prune is I/O heavy — don't run it every cycle).
- Stagger schedules across PVCs so many movers don't hit MinIO at once.

---

## 12. NetworkPolicy (if default-deny)

The mover runs in the app namespace and needs egress to MinIO and DNS:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-volsync-egress
  namespace: <app-ns>
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/created-by: volsync
  policyTypes: [Egress]
  egress:
    - to: [{ ipBlock: { cidr: MINIO_HOST_IP/32 } }]
      ports: [{ protocol: TCP, port: 9000 }]
    - to: [{ namespaceSelector: {} }]
      ports:
        - { protocol: UDP, port: 53 }
        - { protocol: TCP, port: 53 }
```

Confirm the mover pod label in your version with `kubectl get pod -n <app-ns> --show-labels` on a running mover.

---

## 13. Monitoring backup health

```bash
kubectl get replicationsource -A
kubectl -n <app-ns> get replicationsource <name> \
  -o jsonpath='{.status.lastSyncTime}{"\n"}'
kubectl -n <app-ns> describe replicationsource <name>
```

Key fields: `.status.lastSyncTime`, `.status.lastSyncDuration`, `.status.restic.lastPruned`, `.status.latestMoverStatus.result`.

Prometheus alerts (wire into existing Prometheus / SigNoz):

```yaml
- alert: VolSyncBackupStale
  expr: volsync_volume_out_of_sync == 1
  for: 1h
  labels: { severity: warning }

- alert: VolSyncMissedSchedule
  expr: time() - volsync_last_sync_timestamp_seconds > 86400
  for: 30m
  labels: { severity: critical }
```

---

## 14. Restore — single PVC

Thick TopoLVM can't restore via snapshot, so restore writes directly into a pre-created PVC.

```bash
# 1. scale the app down so nothing holds the target volume
kubectl -n <app-ns> scale deploy/<app> --replicas=0
```

```yaml
# 2. pre-create the destination PVC on thick TopoLVM
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <pvc-name>
  namespace: <app-ns>
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <topolvm-sc>
  resources: { requests: { storage: <size> } }
```

```yaml
# 3. restore (latest, or a specific day via restoreAsOf)
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: <pvc-name>-restore
  namespace: <app-ns>
spec:
  trigger:
    manual: restore-once
  restic:
    repository: restic-minio
    destinationPVC: <pvc-name>
    copyMethod: Direct
    # restoreAsOf: "2026-06-29T23:59:59Z"
    # previous: 2
```

```bash
# 4. watch, then bring the app back and delete the RD
kubectl -n <app-ns> get replicationdestination <pvc-name>-restore -w
kubectl -n <app-ns> scale deploy/<app> --replicas=1
kubectl -n <app-ns> delete replicationdestination <pvc-name>-restore
```

---

## 15. Restore — database dumps

```bash
# pick the dated dump and restore it
mc cp backups/k8s-backups/<ns>/pgdump/2026/06/29/<db>-20260629-020000.dump ./r.dump
pg_restore --clean --if-exists -d "postgres://USER:PASS@HOST:5432/DBNAME" ./r.dump
```

For ClickHouse / Elasticsearch, restore via the same native tool that produced the backup.

---

## 16. Disaster recovery — full namespace

1. Recreate the namespace and app manifests (GitOps repo or `kubectl apply`).
2. Recreate Secrets (`restic-minio`, DB creds). Restore needs the **same `RESTIC_PASSWORD`** used at backup time.
3. Per PVC: pre-create on thick TopoLVM → ReplicationDestination (copyMethod Direct) → wait → verify.
4. Restore databases from dated dumps / native snapshots.
5. Scale workloads up in dependency order (DB → app → ingress).
6. Validate application health before declaring recovery complete.

Keep a short DR sheet per namespace: PVC names + sizes + StorageClass, repo paths, which DBs use dumps vs volume backup, and startup order.

---

## 17. Backup verification (do not skip)

A backup you've never restored is a hypothesis. Drill regularly:

- **Monthly:** restore one PVC into a throwaway namespace, mount it, spot-check / checksum files.
- **Per repo, periodically:** run an integrity check.

```bash
export RESTIC_REPOSITORY=s3:https://MINIO_HOST:9000/k8s-backups/<ns>/<pvc>
export RESTIC_PASSWORD=...  AWS_ACCESS_KEY_ID=volsync-backup  AWS_SECRET_ACCESS_KEY=...

restic snapshots
restic check                       # structural integrity
restic check --read-data-subset=5% # read & verify a sample of packs
```

`restic check` passing + a successful test restore = the "0" in 3-2-1-1-0.

---

## 18. Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Mover pod `Pending` | Can't schedule to the node holding the RWO PVC, or no node-local capacity for cache PVC | Check node TopoLVM capacity; ensure `cacheStorageClassName` is the node-local SC |
| `Fatal: unable to open repository` | Wrong endpoint/creds, MinIO TLS/CA, or first-use init | Verify URL scheme, creds, CA mount; VolSync auto-inits |
| `WaitingForTrigger` forever | copy-trigger annotation set but no `copy-trigger` value supplied | Set a unique `copy-trigger` value, or remove `use-copy-trigger` |
| Connection timeout to MinIO | NetworkPolicy blocking mover egress | Apply §12 policy; confirm mover pod labels |
| Prune never reclaims space | Object lock/versioning retaining old packs | Align MinIO ILM + lock with restic retention |
| Restore pod can't bind PVC | App still running, holding RWO volume | Scale app to 0 before restore |
| `restic copy` slow / no dedup to S3 | S3 repo not seeded with `--copy-chunker-params` | Re-init S3 repo with `--copy-chunker-params --repo2 <minio>` |

---

## 19. Quick reference

```bash
# list all backup sources + last sync
kubectl get replicationsource -A -o custom-columns=\
NS:.metadata.namespace,NAME:.metadata.name,PVC:.spec.sourcePVC,LAST:.status.lastSyncTime

# force an off-schedule backup
kubectl -n <ns> patch replicationsource <name> --type merge \
  -p '{"spec":{"trigger":{"manual":"adhoc-'"$(date +%s)"'"}}}'

# inspect a repo and list dated snapshots
restic snapshots ; restic stats ; restic check

# tier: copy MinIO -> S3 archive, then prune archive
restic -r $S3_REPO copy --from-repo $MINIO_REPO
restic -r $S3_REPO forget --keep-within 90d --prune
```

### Golden rules

1. `copyMethod: Direct` everywhere — thick TopoLVM cannot snapshot or clone.
2. Databases get dumps / native snapshots, not raw volume copies.
3. Daily snapshots are date-stamped by restic; restore by date with `restoreAsOf`. Add `--tag` for explicit date tags; date-name objects only for dumps.
4. Guard `RESTIC_PASSWORD` like a root key — losing it = total data loss.
5. Scale the app to 0 before restoring into its RWO PVC.
6. You can't lifecycle-move part of a restic repo — use two repos (Pattern B) or MinIO tiering (Pattern A).
7. Test restores on a schedule. Untested backups don't count.