# UsLab-Eng-01 — Master Plan

New RKE2 cluster with **node-local TopoLVM** (thick, per-node — Lumens-style),
**MetalLB + Nginx Proxy Manager** at the edge, and **VolSync** (optional) for
async PVC→PVC copies. **No ingress controller and no Ceph** in the initial stage.

> Ingress (Traefik v3 + cert-manager) is deferred to **Phase 2** and parked under
> `Traefik.phase2/`. The old Rook-Ceph design has been removed from this cluster
> (it still lives elsewhere in the repo under `CephCluster/`).

## What's here

```
UsLab-Eng-01/
├── README.md                        ← this file
├── new-k8s-cluster-plan.md          ← THE PLAN. Read this first.
├── UsLab-Eng-01-MasterPlan.pdf      ← rendered plan (regenerated from the md)
│
├── TopoLVM/                         ← node-local CSI storage (thick, per node)
│   ├── README.md
│   ├── topolvm-values.yaml          ← Helm values; per-node VG vg-topolvm-<node>, device class ssd
│   ├── storageclasses.yaml          ← per-node SCs local-ssd-<node>, Retain, NO default
│   └── pvc.yaml                     ← smoke test
│
├── MetalLB/                         ← LoadBalancer IPs on the LAN (L2)
│   ├── README.md
│   └── metallb-pools.yaml           ← IPAddressPool (<METALLB_POOL>) + L2Advertisement
│
├── NginxProxyManager/               ← initial-stage edge: reverse proxy + Let's Encrypt
│   ├── README.md
│   └── npm.yaml                     ← ns, PVCs, Deployment (pinned to cheese), LB + admin svc
│
├── VolSync/                         ← scheduled PVC→PVC replication (Direct mode)
│   ├── README.md
│   ├── replicationdestination.example.yaml
│   └── replicationsource.example.yaml
│
├── Traefik.phase2/                  ← ⏸ DEFERRED ingress design (not installed now)
│
├── thin-vs-thick.html               ← storage concept comparison
├── thin-vs-thick-implementation.html← exact implementation diffs
├── storage-performance.html         ← Ceph vs thick vs thin latency picture
└── ACCESS-STRATEGY.md               ← separate: how to give devs/users secure access
```

## Cluster summary

| Aspect | Value |
|--------|-------|
| Cluster name | `uslab-eng-01` |
| Distribution | RKE2 `v1.33.1+rke2r1` (Calico CNI) |
| Master + invoker worker | `roso` `10.35.0.30` — control plane AND invoker pods. **Untainted** (a taint deadlocks the CNI bootstrap); invoker-only enforced via nodeSelector + CPU/RAM reservations — see plan §6.1 |
| App + storage workers | `cheese` `10.35.0.51` (NM790), `dough` `10.35.0.52` (NM790), `rosatini` `10.35.0.53` (NQ780), `nicks` `10.35.0.54` (NQ780) |
| Invoker worker | `poco` `<IP_poco>` (no drive; preferred home for heavy invoker load) |
| CIDRs | pod `10.50.0.0/16` · svc `10.51.0.0/16` · node subnet `10.35.0.0/24` |
| API VIP | `10.35.0.60` (kube-API endpoint / TLS-SAN) |
| MetalLB pool | `10.35.0.61-10.35.0.99` (LoadBalancer IPs for apps; NPM → `10.35.0.61`) |
| **Storage** | **TopoLVM (thick)** — one VG `vg-topolvm-<node>` + one SC `local-ssd-<node>` per node, device class `ssd`, `Retain`, **no default class** |
| **Edge** | **MetalLB (L2) + Nginx Proxy Manager** — no ingress controller |
| **Async replication** | **VolSync** (optional) — `copyMethod: Direct`, scheduled PVC→PVC |
| TLS | Let's Encrypt, managed in the NPM UI |
| Management | Rancher (optional), proxied via NPM |

## Trade-off vs. the old Ceph design

| | Rook-Ceph (old) | TopoLVM thick (new) |
|---|---|---|
| Latency | network hop + OSD/RADOS per I/O | raw on-node NVMe (~10–40× lower) |
| Throughput | network-bound | full NVMe |
| Redundancy | synchronous (was `size:1` = none anyway) | none — VolSync/app backups (async) |
| Ops surface | mons/mgrs/OSDs/CRUSH | LVM VG + a CSI driver |
| RWX (multi-node) | CephFS | not supported (RWO only) |
| Snapshots | yes (RBD) | no (thick) — thin would add them |
| PVC online expand | yes | yes |

## Build order (full detail in plan §13)

1. Fill placeholders in `new-k8s-cluster-plan.md` §0 (VIP, MetalLB pool, IPs, passwords).
2. OS prep on all 6 nodes (§4). Disk + per-node VG on the 4 app nodes (§4.1, thick).
3. HAProxy + keepalived (API VIP) on `roso` (§5).
4. RKE2 server on `roso` (§6) → join 4 app workers (§7.1) → join `poco` (§7.2) → cert-manager (§7.4).
5. Label storage nodes, `helm install topolvm` with `TopoLVM/topolvm-values.yaml`, apply `TopoLVM/storageclasses.yaml`, smoke test (§8.1–8.4).
6. VolSync (optional, §8.5).
7. MetalLB (§9.1) → Nginx Proxy Manager (§9.2) → verify (§9.3).
8. Rancher (§11) + observability (§12), both optional.

**Phase 2 (later):** Traefik v3 + cert-manager (`Traefik.phase2/`); optionally thin pools for snapshot replication.
