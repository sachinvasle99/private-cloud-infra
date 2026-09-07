# TopoLVM — UsLab-Eng-01 (thick, per-node, Lumens-style)

Node-local LVM storage. Every PVC is a **plain (thick) Logical Volume** carved
out of a **per-node Volume Group** named after the node. No thin pools, no CSI
snapshots — lowest, flattest NVMe latency, identical to the Lumens cluster.

> ⚠️ Nothing here is applied automatically. Run the steps in order.

## Files

| File | Purpose |
|------|---------|
| `topolvm-values.yaml` | Helm values. THICK device class `ssd`; one lvmd per node via `kubernetes.io/hostname`, each pointing at its own `vg-topolvm-<node>`. `storageClasses: []` (we manage them). |
| `storageclasses.yaml` | Per-node classes `local-ssd-<node>`, `Retain`, **no default class**, `allowedTopologies` pin to one node each. |
| `pvc.yaml` | Smoke test (provision + write + verify). |

## Storage layout

| Node | Drive | Device @ | VG to create | Device class | StorageClass |
|------|-------|----------|--------------|--------------|--------------|
| cheese | Lexar NM790 4TB | /dev/nvme0n1 | `vg-topolvm-cheese` | ssd | `local-ssd-cheese` |
| dough   | Lexar NM790 4TB | /dev/nvme0n1 | `vg-topolvm-dough`   | ssd | `local-ssd-dough` |
| nicks   | Lexar NQ780 4TB | /dev/nvme0n1 | `vg-topolvm-nicks`   | ssd | `local-ssd-nicks` |
| rosatini | Lexar NQ780 4TB | /dev/nvme0n1 | `vg-topolvm-rosatini` | ssd | `local-ssd-rosatini` |

`roso` (master) and `poco` (invoker) have no drive and are **not** storage nodes.

## 1. Prerequisites

- `helm` + `kubectl` against the cluster.
- **cert-manager installed** (TopoLVM's mutating webhook needs a cert).
- The NVMe on each storage node is empty / wiped (step 3).

## 2. Label the storage nodes

```bash
for n in cheese dough nicks rosatini; do
  kubectl label node "$n" topolvm.io/storage=true --overwrite
done
kubectl get nodes -L topolvm.io/storage
# roso, poco must NOT have the label.
```

## 3. Prepare disks — run ON each storage node, as root

THICK = no thin pool, just a PV + VG. The VG is named after the node.

```bash
DEV=/dev/nvme0n1
lsblk -dno NAME,SIZE,MODEL $DEV          # confirm the 3.7T Lexar, NOT the root SSD

wipefs -a $DEV
sgdisk --zap-all $DEV
pvcreate $DEV
vgcreate vg-topolvm-cheese $DEV         # use the matching name on each node:
                                         #   dough   -> vg-topolvm-dough
                                         #   nicks   -> vg-topolvm-nicks
                                         #   rosatini -> vg-topolvm-rosatini
vgs vg-topolvm-cheese                   # confirm size ~3.7 TiB
# Add another drive later:  vgextend vg-topolvm-cheese /dev/nvme1n1
```

## 4. Install TopoLVM

```bash
helm repo add topolvm https://topolvm.github.io/topolvm && helm repo update
kubectl create namespace topolvm-system
kubectl label ns topolvm-system topolvm.io/webhook=ignore --overwrite
kubectl label ns kube-system    topolvm.io/webhook=ignore --overwrite

helm upgrade --install topolvm topolvm/topolvm -n topolvm-system \
  -f UsLab-Eng-01/TopoLVM/topolvm-values.yaml
kubectl -n topolvm-system rollout status deploy/topolvm-controller

kubectl apply -f UsLab-Eng-01/TopoLVM/storageclasses.yaml
kubectl get sc                                 # 4 local-ssd-* classes, NO default
```

## 5. Verify

```bash
kubectl -n topolvm-system get pod -o wide      # lvmd + node pods on the 4 storage nodes only
kubectl get csidrivers | grep topolvm.io
kubectl get csistoragecapacities -A -o wide    # per-(node,deviceClass) free space
kubectl get nodes -L topology.topolvm.io/node

kubectl create ns demo
kubectl apply -f UsLab-Eng-01/TopoLVM/pvc.yaml
kubectl -n demo get pvc,pod -o wide            # Pending -> Bound/Running
# on cheese:  sudo lvs vg-topolvm-cheese     # shows the new LV
kubectl -n demo delete -f UsLab-Eng-01/TopoLVM/pvc.yaml
```

## Day-2 — capacity (thick is simple)

```bash
# Free space per node. A full VG just makes NEW PVCs Pending — it never blocks
# live writes (the thick advantage; no Data%/Meta% pool to watch).
ssh cheese 'sudo vgs vg-topolvm-cheese'
```

## Notes / gotchas

- Volumes are **RWO** and **node-pinned**. If a node dies, its PVCs are
  unreachable until it returns — no failover. Off-node copies come from VolSync
  (Direct mode, see `../VolSync/`) or app-level backups.
- Capacity is **per node**: a 100Gi PVC only schedules where one node's VG has
  100Gi free. `storageCapacityTracking` keeps the scheduler honest.
- Each node's lvmd is aimed by `kubernetes.io/hostname` and looks ONLY for its
  `vg-topolvm-<node>`. Enable a node only after its VG exists, or that lvmd
  reports the device-class not-ready until the VG is created.
- **No snapshots** (thick). If you later need zero-downtime snapshot replication,
  switch the device class to thin (`type: thin` + `thin-pool`) — see
  `../thin-vs-thick-implementation.html`. That's the only change required.
