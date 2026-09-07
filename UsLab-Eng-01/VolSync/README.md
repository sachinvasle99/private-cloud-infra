# VolSync — UsLab-Eng-01  (Direct mode — thick TopoLVM)

VolSync turns TopoLVM's **node-local RWO** PVCs into scheduled, cross-node copies
by rsync-ing PVC→PVC. It is **not** synchronous replication — RPO = the schedule
interval. Use it for disaster recovery (restore onto a different node when a drive
or node fails), moving PVCs between nodes, or off-cluster backup.

> **THICK storage = Direct mode only.** This cluster uses thick LVM (matches
> Lumens), which has no CSI snapshots, so VolSync runs `copyMethod: Direct`: it
> rsyncs the **live** source PVC. The mover co-mounts the source PVC and lands on
> the same node (RWO). The copy is **not point-in-time** — for a consistent copy
> of a running database you must quiesce the writer (below) or back up at the app
> layer. (Snapshot mode, with no writer downtime, would need thin pools — see
> `../thin-vs-thick-implementation.html`.)

| File | Purpose |
|------|---------|
| `replicationdestination.example.yaml` | Destination side — pre-creates the dest PVC (on a *different* node's class) + rsync-tls endpoint. `copyMethod: Direct`. |
| `replicationsource.example.yaml` | Source side — reads dest address/keySecret and pushes deltas on a schedule. `copyMethod: Direct`. |

## Install

```bash
helm repo add backube https://backube.github.io/helm-charts && helm repo update
helm install volsync backube/volsync -n volsync-system --create-namespace
kubectl -n volsync-system rollout status deploy/volsync
```

## Wire up one replication (cheese → dough example)

```bash
# 1. Destination (on a different node's class than the source)
kubectl create ns target-ns
kubectl apply -f replicationdestination.example.yaml

# 2. Grab address + keySecret name
ADDR=$(kubectl -n target-ns get replicationdestination data-dest \
  -o jsonpath='{.status.rsyncTLS.address}')
KEY=$(kubectl -n target-ns get replicationdestination data-dest \
  -o jsonpath='{.status.rsyncTLS.keySecret}')
echo "address=$ADDR  keySecret=$KEY"

# 3. Patch the source example and apply
sed -e "s#<dest-address>#$ADDR#" -e "s#<key-secret-name>#$KEY#" \
    replicationsource.example.yaml | kubectl apply -f -

# 4. Watch
kubectl -n source-ns get replicationsource data-src \
  -o jsonpath='{.status.lastSyncTime}{"\n"}'
```

## Consistent copies in Direct mode

**File / append-only data:** usually fine to sync live.

**Databases — pick one:**

1. **Quiesce around the sync.** Scale the writer to 0 just before the schedule,
   let VolSync sync, scale back. Simple but incurs downtime each cycle.
2. **App-level dump → replicate the dump (recommended).** Run `pg_dump` /
   `mysqldump` (or the DB's snapshot tool) into a small PVC on a CronJob, and
   point VolSync at *that* PVC. The dump is crash-consistent and the live DB
   never stops. This is the cleanest pattern on thick storage.

## Restore on the target side

```bash
# When the source node/disk dies, point a new StatefulSet/Deployment at the
# destination PVC (already populated as of status.lastSyncTime).
kubectl -n target-ns get pvc
```

## What VolSync does NOT give you

- **Synchronous replication** — you can lose up to one schedule interval of writes.
- **Point-in-time consistency in Direct mode** — quiesce or use app dumps (above).
- **Multi-attach** — PVCs are still RWO; no writers on both sides at once.
- **Online failover** — repoint the app at the destination PVC manually.
