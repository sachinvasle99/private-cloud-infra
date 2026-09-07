# RKE2 Cluster IP Migration — Execution Log

**Cluster:** uslab-cluster  
**Date:** April 2, 2026  
**Performed by:** Chris / DevOps  
**Scope:** Physical relocation of 7 servers + in-place IP subnet migration (10.0.1.x → 10.35.0.x)  
**Objective:** Migrate all servers to new lab without rebuilding the Kubernetes cluster — preserve etcd state, Rook-Ceph OSD data, all workloads, and persistent volumes.

---

## Server Inventory

| Hostname | Role | Old IP | New IP | Status |
|----------|------|--------|--------|--------|
| fratelli | Master (control-plane, etcd) | 10.0.1.30 | 10.35.0.26 | ✅ Ready |
| pane | Worker | 10.0.1.25 | 10.35.0.36 | ✅ Ready |
| cicis | Worker | 10.0.1.35 | 10.35.0.37 | ✅ Ready |
| cesar | Worker | 10.0.1.40 | 10.35.0.38 | ✅ Ready |
| papaj | Worker | 10.0.1.45 | 10.35.0.39 | ✅ Ready |
| grimaldi | Worker | 10.0.1.50 | 10.35.0.40 | ✅ Ready |
| rosso | Worker | 10.0.1.60 | 10.35.0.41 | ✅ Ready |
| VIP | API Load Balancer | 10.0.1.100 | 10.35.0.100 | ✅ Ready |

**Infrastructure:** RKE2 v1.33.1+rke2r1, Calico CNI (VXLAN mode), Rook-Ceph v1.17.4 (Ceph v19.2.2), 5 OSDs across 5 workers, 84 TiB total storage.

---

## Phase 1: Pre-Migration Backup & Graceful Shutdown

### 1.1 Verified Cluster Health (at old location)

Confirmed all nodes Ready, Ceph HEALTH_OK, etcd healthy before starting.

### 1.2 Created etcd Snapshot

```bash
rke2 etcd-snapshot save --name pre-migration-$(date +%Y%m%d)
```

Snapshot created: `pre-migration-20260401-fratelli-1775040626` (174 MB). Copied to external storage.

### 1.3 Backed Up Configurations

- RKE2 `config.yaml` from all nodes
- `/etc/hosts` from all nodes
- Rook-Ceph YAML state (CephCluster, CephBlockPool, ConfigMaps, Secrets)
- Recorded node/pod/PVC/service state

### 1.4 Set Ceph Maintenance Flags

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set noout
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set norebalance
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set nobackfill
```

### 1.5 Stopped All Services

Stopped in correct order to prevent Ceph rebalancing:

```bash
# On each worker (pane, cicis, cesar, papaj, grimaldi, rosso)
systemctl stop rke2-agent
systemctl disable rke2-agent

# On master (fratelli)
systemctl stop rke2-server
systemctl disable rke2-server
```

### 1.6 Physical Move

Powered down all servers, transported to new lab, racked, and connected to new network.

---

## Phase 2: Network Configuration at New Location

### 2.1 Assigned New Static IPs

Updated netplan on each server with the new 10.35.0.x addresses. Applied with `netplan apply`.

### 2.2 Updated /etc/hosts on All Nodes

Applied identical `/etc/hosts` on all 7 servers:

```
127.0.0.1   localhost
127.0.1.1   <own-hostname>

10.35.0.26  fratelli
10.35.0.36  pane
10.35.0.37  cicis
10.35.0.38  cesar
10.35.0.39  papaj
10.35.0.40  grimaldi
10.35.0.41  rosso
10.35.0.100 k8s-api.antbrains.com
```

### 2.3 Verified Connectivity

All nodes could ping all other nodes by hostname. Confirmed `ip addr show` showed correct IPs.

---

## Phase 3: Master Node (fratelli) Recovery

### 3.1 Updated RKE2 Server Configuration

Edited `/etc/rancher/rke2/config.yaml` — key changes:

```yaml
tls-san:
  - rancher-uslab.antbrains.com
  - 10.35.0.100        # new VIP
  - 10.35.0.26         # new master IP
  - k8s-api.antbrains.com

# Critical additions to force correct IP binding
node-ip: 10.35.0.26
advertise-address: 10.35.0.26
```

All other settings (cluster-cidr, service-cidr, cluster-name, cni, etc.) remained unchanged.

### 3.2 Deleted Stale TLS Certificates

Inspected existing certs — confirmed old IPs (10.0.1.30, 10.0.1.100) were embedded in SANs of both API server and etcd certs.

Deleted serving/client certs (kept CA certs intact for re-signing):

```bash
# API server & kubelet serving certs
rm -f /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt
rm -f /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.key
rm -f /var/lib/rancher/rke2/server/tls/serving-kubelet.key
rm -f /var/lib/rancher/rke2/server/tls/dynamic-cert.json

# etcd serving, peer, and client certs
rm -f /var/lib/rancher/rke2/server/tls/etcd/server-client.crt
rm -f /var/lib/rancher/rke2/server/tls/etcd/server-client.key
rm -f /var/lib/rancher/rke2/server/tls/etcd/peer-server-client.crt
rm -f /var/lib/rancher/rke2/server/tls/etcd/peer-server-client.key
rm -f /var/lib/rancher/rke2/server/tls/etcd/client.crt
rm -f /var/lib/rancher/rke2/server/tls/etcd/client.key
```

**Preserved (not deleted):** `etcd/server-ca.*`, `etcd/peer-ca.*`, `server-ca.*`, `client-ca.*`, `request-header-ca.*`

### 3.3 First Start Attempt — etcd Peer URL Mismatch

Started RKE2 server normally. etcd connected but detected a mismatch:

```
Failed to test etcd connection: this server is a not a member of the etcd cluster.
Found [fratelli-12a6cd30=https://10.0.1.30:2380],
expect: fratelli-12a6cd30=https://10.35.0.26:2380
```

etcd was running but refused to proceed because the stored peer URL still referenced the old IP. This was expected behavior.

### 3.4 etcd Cluster Reset with Snapshot Restore

Stopped the server and performed a cluster-reset using the pre-migration snapshot:

```bash
systemctl stop rke2-server

rke2 server --cluster-reset \
  --cluster-reset-restore-path=/var/lib/rancher/rke2/server/db/snapshots/pre-migration-20260401-fratelli-1775040626
```

Key output confirming success:

```
"added-peer-peer-urls":["https://10.35.0.26:2380"]
"restored snapshot" ... "data-dir":"/var/lib/rancher/rke2/server/db/etcd"
"Managed etcd cluster membership has been reset, restart without --cluster-reset flag now."
```

### 3.5 Started Master Successfully

```bash
rm -f /var/lib/rancher/rke2/server/db/reset-flag
systemctl enable rke2-server
systemctl start rke2-server
```

Master came up cleanly:
- etcd connected and healthy
- API server running with `--advertise-address=10.35.0.26`
- New TLS certificates regenerated with correct SANs (10.35.0.26, 10.35.0.100, k8s-api.antbrains.com)
- `kubectl get nodes` showed fratelli as Ready with INTERNAL-IP 10.35.0.26

### 3.6 Token Verification

Confirmed the join token was unchanged after the restore:

```bash
cat /var/lib/rancher/rke2/server/node-token
# Token matched existing worker configs — no update needed
```

---

## Phase 4: Worker Nodes Rejoin

### 4.1 Updated Worker Configurations

On each worker, edited `/etc/rancher/rke2/config.yaml`:

```yaml
server: https://10.35.0.26:9345       # new master IP
token: <unchanged>
node-name: <hostname>                  # kept same
node-ip: <new-worker-ip>              # added new IP
```

Worker IP assignments:

| Worker | node-ip |
|--------|---------|
| pane | 10.35.0.36 |
| cicis | 10.35.0.37 |
| cesar | 10.35.0.38 |
| papaj | 10.35.0.39 |
| grimaldi | 10.35.0.40 |
| rosso | 10.35.0.41 |

### 4.2 Cleaned Stale State & Started Workers

On each worker:

```bash
rm -f /var/lib/rancher/rke2/agent/kubelet.kubeconfig
rm -f /var/lib/rancher/rke2/agent/client-kubelet.crt
rm -f /var/lib/rancher/rke2/agent/client-kubelet.key

systemctl enable rke2-agent
systemctl start rke2-agent
```

### 4.3 Result

All 7 nodes joined and showed Ready with correct new IPs:

```
NAME       STATUS   ROLES                       INTERNAL-IP
fratelli   Ready    control-plane,etcd,master   10.35.0.26
pane       Ready    worker                      10.35.0.36
cicis      Ready    worker                      10.35.0.37
cesar      Ready    worker                      10.35.0.38
papaj      Ready    worker                      10.35.0.39
grimaldi   Ready    worker                      10.35.0.40
rosso      Ready    worker                      10.35.0.41
```

---

## Phase 5: Calico CNI Recovery

### 5.1 Problem Identified

Calico-node pods (in `calico-system` namespace, not `kube-system`) were all `0/1 Running` — not Ready. Felix readiness probe returning 503.

### 5.2 Root Cause

Felix logs revealed the issue — Typha endpoint discovery was returning old IPs:

```
Found ready Typha addresses: 10.0.1.35:5473, 10.0.1.25:5473, 10.0.1.45:5473
Failed to connect to typha endpoint 10.0.1.35:5473 — no route to host
```

The Kubernetes Endpoints object for `calico-typha` service still had old IPs cached.

### 5.3 Fix Applied

```bash
# Deleted Typha pods — deployment recreated them, endpoints refreshed
kubectl -n calico-system delete pods -l k8s-app=calico-typha

# Verified new endpoints
kubectl -n calico-system get endpoints calico-typha
# Now showing new 10.35.0.x IPs

# Restarted calico-node pods
kubectl -n calico-system delete pods -l k8s-app=calico-node
```

### 5.4 Result

All 7 calico-node pods came up `1/1 Ready` within 40 seconds. Pod networking fully restored. This unblocked all other components that were failing due to lack of CNI (rook-ceph-operator, coredns, etc.).

---

## Phase 6: Rook-Ceph Storage Recovery

### 6.1 Updated MON Endpoint Mapping

The `rook-ceph-mon-endpoints` configmap had old node IPs in the mapping field. MON addresses themselves used ClusterIPs (10.49.x.x) which were fine, but the node mapping needed updating:

```bash
kubectl -n rook-ceph get configmap rook-ceph-mon-endpoints -o json | \
  sed 's/10.0.1.45/10.35.0.39/g' | \
  sed 's/10.0.1.50/10.35.0.40/g' | \
  sed 's/10.0.1.25/10.35.0.36/g' | \
  kubectl apply -f -
```

### 6.2 Restarted Rook-Ceph Operator

Operator had been CrashLooping (`dial tcp 10.49.0.1:443: i/o timeout`) because pod networking was down. After Calico was fixed:

```bash
kubectl -n rook-ceph delete pod -l app=rook-ceph-operator
```

Operator came up Running within 23 seconds.

### 6.3 OSD Recovery

All 5 OSD pods were in Unknown state from the old deployment. Force-deleted them:

```bash
kubectl -n rook-ceph delete pod rook-ceph-osd-{0,1,2,4,5}-* --force --grace-period=0
```

The operator triggered OSD prepare jobs for each node, followed by new OSD pod creation. OSDs came up progressively over ~10 minutes:

- OSD.0 (cicis) — up first
- OSD.2 (grimaldi) — up second
- OSD.1 (cesar), OSD.4 (pane), OSD.5 (papaj) — initialized and came up

### 6.4 Final Ceph Status

```
cluster:
    health: HEALTH_WARN (only due to maintenance flags)
  services:
    mon: 3 daemons, quorum a,b,d
    mgr: b(active), standbys: a
    mds: 1/1 daemons up, 1 hot standby
    osd: 5 osds: 5 up, 5 in
    rgw: 1 daemon active
  data:
    volumes: 1/1 healthy
    pools:   16 pools, 361 pgs
    objects: 2.15M objects, 2.3 TiB
    usage:   6.8 TiB used, 77 TiB / 84 TiB avail
    pgs:     361 active+clean (356 clean + 3 scrubbing+deep + 2 scrubbing)
```

### 6.5 Removed Maintenance Flags

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd unset noout
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd unset norebalance
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd unset nobackfill
```

---

## Phase 7: VIP & Load Balancer (Pending)

- Update HAProxy backend to point to 10.35.0.26:6443 and 10.35.0.26:9345
- Update Keepalived virtual_ipaddress to 10.35.0.100/24
- Update DNS records: `rancher-uslab.antbrains.com` → 10.35.0.100, `k8s-api.antbrains.com` → 10.35.0.100
- Verify API access via VIP: `curl -k https://10.35.0.100:6443/healthz`

---

## Phase 8: Post-Migration Validation (Pending)

- [ ] All 7 nodes Ready with correct IPs
- [ ] All system pods Running in kube-system and calico-system
- [ ] Ceph HEALTH_OK with no flags
- [ ] All PVCs Bound
- [ ] PVC read/write test passes
- [ ] CoreDNS resolution works
- [ ] Cross-node pod-to-pod networking works
- [ ] VIP responds on port 6443
- [ ] DNS resolution for rancher-uslab.antbrains.com works
- [ ] Application workloads running normally
- [ ] Rolling restart of deployments completed

---

## Issues Encountered & Resolutions

| # | Issue | Root Cause | Resolution |
|---|-------|-----------|------------|
| 1 | `etcdctl` binary not found at `/var/lib/rancher/rke2/bin/` | RKE2 v1.33 doesn't ship standalone etcdctl in bin/ — it's inside a containerd snapshot layer | Found at containerd snapshot path; can also use `kubectl exec` into etcd pod |
| 2 | etcd refused to start — peer URL mismatch | etcd member list stored old IP `https://10.0.1.30:2380` | Performed `rke2 server --cluster-reset` with snapshot restore — etcd re-initialized with new IP |
| 3 | Calico-node pods 0/1 Ready, felix reporting 503 | Typha endpoints object cached old IPs (10.0.1.x) — felix couldn't connect to Typha | Deleted Typha pods → endpoints refreshed → deleted calico-node pods → all came up Ready |
| 4 | Rook-ceph-operator CrashLoopBackOff | `dial tcp 10.49.0.1:443: i/o timeout` — no pod networking (Calico was down) | Fixed after Calico recovery; restarted operator pod |
| 5 | OSD pods stuck in Unknown state | Old pods from pre-migration were stale | Force-deleted old pods; operator created new ones via prepare jobs |
| 6 | etcd cert SANs had old IPs | Serving and peer certs baked in 10.0.1.30 and 10.0.1.100 | Deleted serving/peer/client certs (kept CAs); RKE2 regenerated with new IPs on startup |
| 7 | `serving-kubelet.crt` listed in guide but didn't exist | Only the `.key` file was present, no `.crt` | Non-issue — deleted the key file that existed |

---

## Key Decisions

1. **In-place migration over rebuild** — Chose to preserve etcd state and Ceph OSD data rather than rebuilding the cluster from scratch, saving significant time and avoiding data migration.

2. **etcd cluster-reset over manual member update** — Direct `etcdctl member update` would have been cleaner but etcd couldn't start to accept the command due to the peer URL mismatch. Cluster-reset with snapshot restore was the reliable fallback.

3. **Delete certs, not edit them** — Rather than trying to patch SANs into existing certificates, deleted them and let RKE2 regenerate fresh certs using the updated `config.yaml`. Simpler and less error-prone.

4. **Calico Typha pod restart before calico-node** — The root cause was stale Typha endpoints, not calico-node configuration. Fixing Typha first was essential — restarting calico-node alone would not have resolved the issue.

---