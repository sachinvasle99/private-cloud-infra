# UsLab-Eng-01 — Backup Guide (how it works, versions, manual triggers)

End-to-end documentation of the `uslab-eng-01` namespace backup system: what we back
up, how, where it lands, how versions/restore points are managed, and how to trigger a
backup manually at any time.

- **Backup target:** MinIO (separate server) `https://10.35.0.36:9000`, bucket `uslab-eng-01-backup`
- **Schedule:** every service, daily at **23:00 IST** (`30 17 * * *` = 17:30 UTC)
- **Manifests/tools:** `UsLab-Eng-01/Backup/uslab/`

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  RKE2 cluster  —  namespace: uslab-eng-01      (storage: thick TopoLVM, RWO,    │
│                                                 node-local — NO CSI snapshots)  │
│                                                                                 │
│   DATABASES                                  FILE / BLOB VOLUMES                │
│   Postgres global / node1 / node2            mediadb · elasticsearch ·          │
│   (StatefulSets)                             krista-ai · node1/2-accesspoint    │
│        │                                            │                            │
│        ▼                                            ▼                            │
│  ┌──────────────────────────┐            ┌────────────────────────────────┐    │
│  │ CronJob  pgdump-<inst>    │            │ VolSync  ReplicationSource     │    │
│  │  initC: copy `mc` binary  │            │  restic mover (runs as root)   │    │
│  │  pg_dumpall | gzip | mc   │            │  copyMethod: Direct            │    │
│  │  → streamed to MinIO      │            │  incremental + dedup + encrypt │    │
│  └────────────┬─────────────┘            └───────────────┬────────────────┘    │
│   FULL dump, one dated file/run            INCREMENTAL, one dated snapshot/run  │
│               │   trigger: schedule 23:00 IST  (or manual)  │                   │
└───────────────┼─────────────────────────────────────────────┼──────────────────┘
                │              HTTPS  (self-signed CA "pane")    │
                ▼                                                ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │  MinIO  —  10.35.0.36:9000   (S3 API; console :9001)   region us-west-lab │
   │  bucket: uslab-eng-01-backup                                              │
   │   ├─ pgdump/<inst>/YYYY/MM/DD/<inst>-<timestamp>.sql.gz   ← DB dumps      │
   │   ├─ restic/uslab-eng-01/<pvc>/                           ← restic repos  │
   │   │     config · keys · index · data/ · snapshots/        (dedup packs)   │
   │   └─ reports/YYYY/MM/DD/backup-inventory.txt              ← daily catalog  │
   └───────────────────────────────────┬───────────────────────────────────────┘
                                        │  (planned) restic copy / ILM tiering
                                        ▼
                              AWS S3 — long-term archive  (not yet configured)
```

**Why this shape:** thick TopoLVM has **no CSI snapshot/clone**, so we can't snapshot
volumes. Instead we read data out at the file level. Databases get **logical dumps**
(consistent), everything else uses **VolSync + restic** (`copyMethod: Direct`, the only
option without snapshots) which is incremental, deduplicated, and encrypted.

---

## 2. What we back up

| Service | PVC / source | Size | Method | Notes |
|---|---|---|---|---|
| Postgres **global** | StatefulSet | 50 Gi | pg_dumpall → MinIO | full dump/run |
| Postgres **node1** | StatefulSet | 250 Gi | pg_dumpall → MinIO | full dump/run (largest) |
| Postgres **node2** | StatefulSet | 100 Gi | pg_dumpall → MinIO | full dump/run |
| **shared-mediadb** | `shared-mediadb-pvc` | 281 GB used | VolSync restic | dedup → ~63 GB stored |
| **elasticsearch** | `elasticsearch-pvc` | 162 GB | VolSync restic | crash-consistent (see §7) |
| **krista-ai** | `krista-ai-server` | 16 GB | VolSync restic | |
| **node1/2-accesspoint** | `nodeN-accesspoint3-pvc` | ~6 GB ea | VolSync restic | |

**Excluded:** redis, activemq (by request), infinispan (cache), prometheus/grafana
(regenerable), gc-logs.

---

## 3. How a backup is taken (the mechanics)

### 3a. Databases — `10-pgdump-cronjobs.yaml`
Each Postgres instance has a CronJob that **streams** a logical dump straight to MinIO
(no temp disk):
1. An **initContainer** (`minio/mc`) copies the `mc` binary into a shared dir.
2. The main container (`postgres:17`) runs:
   `pg_dumpall | gzip | mc pipe → s3://uslab-eng-01-backup/pgdump/<inst>/YYYY/MM/DD/<inst>-<ts>.sql.gz`
- Auth: `minio-backup-creds` secret. TLS: `mc --insecure` (self-signed CA).
- Result: **one full, date-stamped `.sql.gz` object per run.**

### 3b. File volumes — `20/30/40-*.yaml` (VolSync)
Each PVC has a **ReplicationSource**. On trigger, VolSync starts a **restic mover** pod
on the same node as the PVC that:
1. Mounts the live PVC read-side (`copyMethod: Direct`).
2. restic scans it, uploads **only new/changed blocks** (dedup), encrypts, and writes a
   new **snapshot** to `restic/uslab-eng-01/<pvc>/`.
- Auth + repo URL + encryption key: per-PVC `restic-*` secret.
- TLS: `customCA` configmap `minio-ca` (the MinIO self-signed cert).
- Mover runs **as root** (`moverSecurityContext`) so it can read every file.

### Supporting pieces
- `00-minio-ca.yaml` — MinIO's self-signed CA (so restic trusts HTTPS).
- `setup-secrets.sh` — creates the MinIO + per-repo restic secrets (kept out of git).
- `credentials.json` — the MinIO service-account creds (input).

---

## 4. How versions / restore points are managed

| | Postgres dumps | File backups (restic) |
|---|---|---|
| **A version =** | one dated `.sql.gz` file | one dated **snapshot** |
| **Storage** | full copy each run | incremental + dedup (one repo) |
| **Where the date is** | in the **path + filename** (`…/YYYY/MM/DD/…-<ts>.sql.gz`) — browsable in MinIO UI | inside the **snapshot** (timestamp + ID); the `snapshots/` objects' last-modified = backup time |
| **Retention** | none yet (every day kept — add MinIO lifecycle) | **7 daily + 4 weekly + 3 monthly** (restic auto-prunes) |
| **List versions** | browse `pgdump/` or `mc ls` | `restic snapshots` |

**See all versions in one place:** run **`./list-backups.sh`** — it lists every
restore point (restic snapshots with dates/IDs + the pg dump files). A daily
**inventory report** can also be written to `reports/YYYY/MM/DD/backup-inventory.txt`
for a browsable record in the MinIO console.

> One restore point accumulates per service per day. After N days you have N dated
> versions to choose from (within retention).

---

## 5. How to TRIGGER A BACKUP MANUALLY (any time)

> ⚠️ **Rule:** never manually trigger a backup that is **already running** — restic
> allows one writer per repo, and overlapping runs cause a stale-lock retry loop.

### 5a. Trigger a single file backup (incremental)
```bash
export KUBECONFIG=/Users/kiranmane/Documents/uslab-Eng-01.yaml

# replace <name> with: krista-ai-server-backup | shared-mediadb-backup |
#   elasticsearch-backup | node1-accesspoint3-backup | node2-accesspoint3-backup
kubectl -n uslab-eng-01 patch replicationsource <name> \
  --type merge -p "{\"spec\":{\"trigger\":{\"manual\":\"run-$(date +%s)\"}}}"

# watch it
kubectl -n uslab-eng-01 get replicationsource <name> -w
```
**IMPORTANT — restore the daily schedule afterward** (the manual trigger replaces it):
```bash
kubectl -n uslab-eng-01 patch replicationsource <name> \
  --type json -p '[{"op":"remove","path":"/spec/trigger/manual"}]'
```

### 5b. Trigger a single Postgres dump (full, dated) — no cleanup needed
```bash
# <inst> = global | node1 | node2
kubectl -n uslab-eng-01 create job --from=cronjob/pgdump-<inst> pgdump-<inst>-now-$(date +%s)
kubectl -n uslab-eng-01 get jobs | grep pgdump   # watch for Complete
```

### 5c. Trigger ALL services now
```bash
TS=$(date +%s)
for rs in krista-ai-server-backup shared-mediadb-backup elasticsearch-backup \
          node1-accesspoint3-backup node2-accesspoint3-backup; do
  kubectl -n uslab-eng-01 patch replicationsource "$rs" \
    --type merge -p "{\"spec\":{\"trigger\":{\"manual\":\"all-$TS\"}}}"
done
for inst in global node1 node2; do
  kubectl -n uslab-eng-01 create job --from=cronjob/pgdump-$inst pgdump-$inst-now-$TS
done
# afterwards: remove the manual triggers from the 5 sources (5a cleanup) to restore schedule
```
(Heavy on the node + the ~2 MB/s link if fired all at once — fine off-hours.)

---

## 6. How to RESTORE a chosen version

1. **Pick a version** — `./list-backups.sh` (note the date/ID or `.sql.gz` file).
2. **Postgres:**
   ```bash
   mc cp --insecure bk/uslab-eng-01-backup/pgdump/<inst>/YYYY/MM/DD/<file>.sql.gz ./d.sql.gz
   gunzip d.sql.gz && psql -h <inst>-postgres -U postgres -f d.sql
   ```
3. **File PVC** (scale the app to 0 first — RWO volume):
   ```yaml
   apiVersion: volsync.backube/v1alpha1
   kind: ReplicationDestination
   metadata: { name: <pvc>-restore, namespace: uslab-eng-01 }
   spec:
     trigger: { manual: restore-1 }
     restic:
       repository: restic-<repo>
       destinationPVC: <pvc>
       copyMethod: Direct
       customCA: { configMapName: minio-ca, key: ca.crt }
       restoreAsOf: "2026-07-01T23:59:59Z"   # newest snapshot at/under this date
   ```
   Then scale the app back up and delete the ReplicationDestination.

---

## 7. Operational notes & known limits

- **Throughput ~2 MB/s** to MinIO — first full backups are slow (ES ~hours, node1 dump
  ~2–3 h); subsequent file backups are incremental (small/fast). DB dumps are full each run.
- **Restic locks:** interrupting a mover (e.g. editing the spec mid-run) leaves a stale
  lock → `forget` fails → retry loop. Fix: `restic unlock` on the repo. Edit VolSync
  specs only **between** runs.
- **Elasticsearch is crash-consistent** (Direct file copy): ES 7.6.2 lacks the
  `repository-s3` plugin and uses a read-only config, so native S3 snapshots need the
  plugin + CA baked into the next ES image rebuild. Until then, a `_flush` runs cleaner.
- **Secrets:** `restic-*` repo passwords live in `setup-secrets.sh` — **store them in a
  secrets manager**; losing one makes that repo unrecoverable. `credentials.json` holds
  live MinIO keys — do not commit.

---

## 8. Files

| File | Purpose |
|------|---------|
| `00-minio-ca.yaml` | MinIO self-signed CA configmap (restic `customCA`) |
| `setup-secrets.sh` | creates MinIO + per-repo restic Secrets |
| `10-pgdump-cronjobs.yaml` | Postgres dump CronJobs (global/node1/node2) |
| `20-volsync.yaml` | VolSync for krista-ai + mediadb |
| `30-volsync-rest.yaml` | VolSync for node1/node2 accesspoint |
| `40-volsync-elasticsearch.yaml` | VolSync for elasticsearch |
| `list-backups.sh` | list all restore points (versions) |
| `README-uslab.md` | quick reference |
| `BACKUP-GUIDE.md` | this document |

## 9. Pending hardening
Offsite tier to **AWS S3**; **backup-failure alerts** (Prometheus/SigNoz); a **tested
restore** (the real proof); **MinIO lifecycle** on `pgdump/` so DB dumps auto-expire;
**ES native snapshots** at the next image rebuild.
