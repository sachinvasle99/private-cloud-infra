# UsLab-Eng-01 Backups — How it works (plain English)

This folder contains the **actual, environment-specific** backup setup for the
`uslab-eng-01` namespace. (The parent `Backup/backup-things.md` is the generic
design/theory; this is the real thing wired to our MinIO.)

---

## The mental model — 2 kinds of data, 2 ways to back up

Everything in the cluster lives on **PVCs** (disks). Our storage is **thick
TopoLVM**, which **cannot take snapshots**. So we can't "snapshot the disk." Instead
we back up by **reading the data out** and shipping it to **MinIO** (an S3 server at
`10.35.0.36`). Two methods, because databases and files need different handling:

| Data | Why it's special | How we back it up | Tool |
|------|------------------|-------------------|------|
| **Databases** (Postgres) | A raw copy of a live DB's files is corrupt/unreliable | Take a **logical dump** (`pg_dumpall`) — a consistent export — and upload it | CronJob → MinIO |
| **File volumes** (media, app data) | Just files; safe to copy | **VolSync + restic** reads the PVC and stores **encrypted, deduplicated, incremental** backups | VolSync → MinIO |

> Golden rule: **databases get dumps, files get VolSync.** Never rely on a raw
> volume copy of a running database.

---

## Where backups go (MinIO bucket `uslab-eng-01-backup`)

```
10.35.0.36:9000  (MinIO, S3 API, self-signed TLS)
└── uslab-eng-01-backup/
    ├── pgdump/<instance>/YYYY/MM/DD/<instance>-<timestamp>.sql.gz   ← Postgres dumps (date-stamped)
    └── restic/uslab-eng-01/<pvc>/                                   ← VolSync restic repos (one per PVC)
```

- **pgdump/** — plain gzipped SQL dumps, one object per run, organised by date. Easy
  to find and restore a specific day.
- **restic/** — restic's own encrypted repo format (don't read these by hand; use
  restic/VolSync to restore). One repo **per PVC** so dedup/retention/restore are clean.

---

## What's set up for uslab-eng-01

**Schedule: ALL services run daily at `30 17 * * *` = 23:00 IST (17:30 UTC).**
(K8s/VolSync cron is evaluated in UTC; 17:30 UTC = 23:00 IST.)

| Target | Type | Schedule | File |
|--------|------|----------|------|
| Postgres `node1` (250Gi) | pg_dumpall → MinIO | 23:00 IST | `10-pgdump-cronjobs.yaml` |
| Postgres `node2` (100Gi) | pg_dumpall → MinIO | 23:00 IST | `10-pgdump-cronjobs.yaml` |
| Postgres `global` (50Gi)  | pg_dumpall → MinIO | 23:00 IST | `10-pgdump-cronjobs.yaml` |
| `krista-ai-server` (50Gi) | VolSync restic | 23:00 IST | `20-volsync.yaml` |
| `shared-mediadb-pvc` (400Gi) | VolSync restic | 23:00 IST | `20-volsync.yaml` |
| `elasticsearch-pvc` (400Gi) | VolSync restic | 23:00 IST | `40-volsync-elasticsearch.yaml` |
| node1/node2 `accesspoint` | VolSync restic | 23:00 IST | `30-volsync-rest.yaml` |

**Retention** (restic, file PVCs): 7 daily + 4 weekly + 3 monthly restore points.
**Not backed up** (regenerable / cache): prometheus, grafana, infinispan cache,
gc-logs, redis, activemq (excluded by request).

---

## How it's wired (the pieces)

1. **`00-minio-ca.yaml`** — MinIO uses a self-signed cert; this ConfigMap holds its CA
   so restic trusts it (`customCA`). The pg_dump jobs use `mc --insecure` instead.
2. **`setup-secrets.sh`** — creates 3 Secrets in the namespace from
   `../credentials.json` (kept **out of git**):
   - `minio-backup-creds` — MinIO access/secret keys (for pg_dump jobs).
   - `restic-krista-ai`, `restic-mediadb` — per-PVC restic repo URL + **RESTIC_PASSWORD** + keys.
   > ⚠️ The two RESTIC_PASSWORDs are in `setup-secrets.sh`. **Save them in your secrets
   > manager** — lose one and that repo is unrecoverable.
3. **`10-pgdump-cronjobs.yaml`** — 3 CronJobs. Each streams `pg_dumpall | gzip | mc pipe`
   straight to MinIO (no temp disk). An init container copies the `mc` binary in.
4. **`20-volsync.yaml`** — 2 VolSync ReplicationSources (`copyMethod: Direct`, mandatory
   for thick TopoLVM), backing up the two file PVCs.

---

## Apply it (order)

```bash
export KUBECONFIG=/Users/kiranmane/Documents/uslab-Eng-01.yaml
cd UsLab-Eng-01/Backup/uslab

kubectl apply -f 00-minio-ca.yaml
./setup-secrets.sh
kubectl apply -f 10-pgdump-cronjobs.yaml
kubectl apply -f 20-volsync.yaml
```

## Run a backup now (don't wait for the schedule)

```bash
# Postgres dump (creates a one-off Job from the CronJob)
kubectl -n uslab-eng-01 create job --from=cronjob/pgdump-node1 pgdump-node1-now

# VolSync file backup (trigger an immediate sync)
kubectl -n uslab-eng-01 patch replicationsource krista-ai-server-backup \
  --type merge -p '{"spec":{"trigger":{"manual":"run-'"$(date +%s)"'"}}}'
```

## Check backup health

```bash
kubectl -n uslab-eng-01 get replicationsource          # LAST SYNC column
kubectl -n uslab-eng-01 get cronjob                    # LAST SCHEDULE
# list what's actually in MinIO:
mc alias set bk https://10.35.0.36:9000 <AK> <SK> --insecure
mc ls --recursive --insecure bk/uslab-eng-01-backup/
```

---

## How to RESTORE

### Postgres (from a dated dump)
```bash
# 1. download the dump
mc cp --insecure bk/uslab-eng-01-backup/pgdump/node1/2026/06/30/node1-<ts>.sql.gz ./d.sql.gz
gunzip d.sql.gz
# 2. load it (into the target instance; pg_dumpall is plain SQL incl. roles)
psql -h node1-postgres -U postgres -f d.sql        # run from a pod with psql + the password
```

### File PVC (VolSync restic) — see parent guide §14
```bash
# 1. scale the app to 0 so it isn't holding the RWO volume
# 2. create a ReplicationDestination (copyMethod Direct) pointing at the same restic
#    repo secret + destinationPVC; optionally restoreAsOf a date. VolSync writes the
#    data back into the PVC. 3. scale the app back up.
```
(Restore manifests can be generated on request — kept short here.)

---

## Next (not done yet — separate steps)
- **Elasticsearch (400Gi)** & **Redis/ActiveMQ**: native snapshot/dump (ES needs its
  S3 snapshot repo + the MinIO CA in its truststore).
- **Tier to AWS S3** for long-term archive (parent guide §10, Pattern B).
- **Prometheus alerts** on stale/missed backups (parent guide §13).
- **Test restores** on a schedule — a backup you've never restored is only a hope.
