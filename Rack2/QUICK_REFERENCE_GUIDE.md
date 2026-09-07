# Quick Reference Guide - US Lab Kubernetes Cluster

**Cluster Name:** uslab-cluster  
**Last Updated:** January 2026

---

## Cluster Overview

- **Kubernetes Version:** v1.33.1+rke2r1
- **Distribution:** RKE2
- **Nodes:** 7 (1 control plane + 6 workers)
- **Total Resources:** ~360 CPU cores, ~1.5TB RAM
- **Virtual IP:** 10.0.1.100 (k8s-api.antbrains.com)

---

## Node Inventory

| Hostname | IP        | Role          | CPU | RAM (GB) | OS             |
|----------|-----------|---------------|-----|----------|----------------|
| fratelli | 10.0.1.30 | Control Plane | 40  | 128      | Ubuntu 24.04.2 |
| pane     | 10.0.1.25 | Worker        | 40  | 128      | Ubuntu 24.04.3 |
| cicis    | 10.0.1.35 | Worker        | 40  | 128      | Ubuntu 24.04.3 |
| cesar    | 10.0.1.40 | Worker        | 40  | 128      | Ubuntu 24.04.2 |
| papaj    | 10.0.1.45 | Worker        | 40  | 128      | Ubuntu 24.04.2 |
| grimaldi | 10.0.1.50 | Worker (High) | 80  | 640      | Ubuntu 24.04.2 |
| rosso    | 10.0.1.60 | Worker        | 40  | 128      | Ubuntu 22.04.5 |

---

## Quick Access URLs

- **Rancher UI:** https://rancher-uslab.antbrains.com
- **Ceph Dashboard:** https://ceph-uslab.antbrains.com
- **HAProxy Stats:** http://10.0.1.30:8404/stats (admin/admin123)
- **Kubernetes API:** https://10.0.1.100:6443

---

## Essential Commands

### Cluster Status
```bash
# Set kubeconfig
export KUBECONFIG=~/Documents/us-cluster.yaml

# Check nodes
kubectl get nodes -o wide

# Check all pods
kubectl get pods -A

# Check cluster info
kubectl cluster-info

# Resource usage
kubectl top nodes
kubectl top pods -A
```

### Ceph Storage
```bash
# Ceph status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status

# Ceph health
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph health detail

# OSD status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd status

# Storage usage
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph df
```

### Node Management
```bash
# SSH to nodes
ssh fratelli
ssh pane
ssh cicis
ssh cesar
ssh papaj
ssh grimaldi
ssh rosso

# Check RKE2 status
sudo systemctl status rke2-server  # On fratelli
sudo systemctl status rke2-agent   # On workers

# View logs
sudo journalctl -u rke2-server -f  # On fratelli
sudo journalctl -u rke2-agent -f   # On workers
```

### Common Operations
```bash
# Scale deployment
kubectl scale deployment <name> --replicas=3 -n <namespace>

# Restart deployment
kubectl rollout restart deployment/<name> -n <namespace>

# View logs
kubectl logs -f <pod> -n <namespace>

# Execute command in pod
kubectl exec -it <pod> -n <namespace> -- /bin/bash

# Port forward
kubectl port-forward svc/<service> 8080:80 -n <namespace>

# Drain node for maintenance
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data

# Uncordon node
kubectl uncordon <node>
```

---

## Storage Classes

- **ceph-block** (default) - RBD block storage
- **ceph-filesystem** - CephFS shared filesystem
- **ceph-bucket** - Object storage (S3-compatible)
- **postgres-fast-ssd** - Optimized for PostgreSQL data
- **postgres-wal-ssd** - Optimized for PostgreSQL WAL

---

## Network Configuration

- **Pod CIDR:** 10.48.0.0/16
- **Service CIDR:** 10.49.0.0/16
- **Node Network:** 10.0.1.0/24
- **Virtual IP:** 10.0.1.100
- **MetalLB Pool:** 10.0.1.101-10.0.1.200

---

## Troubleshooting Quick Checks

### Node Issues
```bash
# Check node status
kubectl describe node <node-name>

# Check kubelet logs
ssh <node> "sudo journalctl -u rke2-agent -n 100"

# Restart RKE2
ssh <node> "sudo systemctl restart rke2-agent"
```

### Pod Issues
```bash
# Describe pod
kubectl describe pod <pod> -n <namespace>

# Check events
kubectl get events -n <namespace> --sort-by='.lastTimestamp'

# Check logs
kubectl logs <pod> -n <namespace>
kubectl logs <pod> -n <namespace> --previous
```

### Storage Issues
```bash
# Check PVCs
kubectl get pvc -A

# Check PVs
kubectl get pv

# Describe PVC
kubectl describe pvc <pvc-name> -n <namespace>
```

---

## Important File Locations

### On fratelli (Control Plane)
- RKE2 config: `/etc/rancher/rke2/config.yaml`
- Kubeconfig: `/etc/rancher/rke2/rke2.yaml`
- Node token: `/var/lib/rancher/rke2/server/node-token`
- HAProxy config: `/etc/haproxy/haproxy.cfg`
- Keepalived config: `/etc/keepalived/keepalived.conf`

### On Workers
- RKE2 config: `/etc/rancher/rke2/config.yaml`

---

## Emergency Contacts

- **Infrastructure Team:** infrastructure@antbrains.com
- **Full Documentation:** `/krista_private_cloud/Rack2/COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md`

---

**For detailed instructions, refer to the Complete On-Prem Kubernetes Setup Guide.**

