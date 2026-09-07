# Rack2 - US Lab On-Premises Kubernetes Cluster

This directory contains all documentation and configuration files for the US Lab on-premises Kubernetes cluster (uslab-cluster).

---

## 📚 Documentation

### Primary Documents

1. **[COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md](./COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md)**
   - **Purpose:** Comprehensive, production-ready guide for deploying and managing the entire Kubernetes cluster
   - **Audience:** Infrastructure engineers, DevOps teams, system administrators
   - **Content:** 
     - Complete installation guide (9 phases)
     - Architecture overview and diagrams
     - Operations and maintenance procedures
     - Troubleshooting guide
     - Backup and disaster recovery
     - Security best practices
     - Appendices with commands and references
   - **Status:** ✅ Complete and ready for publication
   - **Pages:** ~1850 lines

2. **[QUICK_REFERENCE_GUIDE.md](./QUICK_REFERENCE_GUIDE.md)**
   - **Purpose:** Quick reference for daily operations
   - **Audience:** Anyone managing the cluster
   - **Content:**
     - Cluster overview
     - Node inventory
     - Essential commands
     - Quick troubleshooting
     - Important URLs and file locations
   - **Status:** ✅ Complete

3. **[rke2-cluster/US-LabRKE2ClussterSetUp.md](./rke2-cluster/US-LabRKE2ClussterSetUp.md)**
   - **Purpose:** Original RKE2 setup documentation
   - **Status:** ✅ Updated with correct cluster architecture
   - **Note:** Superseded by COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md

---

## 🏗️ Cluster Architecture

### Overview
- **Cluster Name:** uslab-cluster
- **Kubernetes Version:** v1.33.1+rke2r1
- **Distribution:** RKE2 (Rancher Kubernetes Engine 2)
- **Container Runtime:** containerd 2.0.5-k3s1
- **Total Nodes:** 7 (1 control plane + 6 workers)
- **Total Capacity:** ~360 CPU cores, ~1.5TB RAM

### Nodes

| Hostname | IP Address | Role           | CPU | RAM (GB) | OS             | Notes                |
|----------|------------|----------------|-----|----------|----------------|----------------------|
| fratelli | 10.0.1.30  | Control Plane  | 40  | 128      | Ubuntu 24.04.2 | Master + HAProxy + VIP |
| pane     | 10.0.1.25  | Worker         | 40  | 128      | Ubuntu 24.04.3 | Standard worker      |
| cicis    | 10.0.1.35  | Worker         | 40  | 128      | Ubuntu 24.04.3 | Standard worker      |
| cesar    | 10.0.1.40  | Worker         | 40  | 128      | Ubuntu 24.04.2 | Standard worker      |
| papaj    | 10.0.1.45  | Worker         | 40  | 128      | Ubuntu 24.04.2 | Standard worker      |
| grimaldi | 10.0.1.50  | Worker         | 80  | 640      | Ubuntu 24.04.2 | High-capacity worker |
| rosso    | 10.0.1.60  | Worker         | 40  | 128      | Ubuntu 22.04.5 | Standard worker      |

### Network
- **Virtual IP:** 10.0.1.100 (k8s-api.antbrains.com)
- **Pod CIDR:** 10.48.0.0/16
- **Service CIDR:** 10.49.0.0/16
- **CNI:** Calico (VXLAN mode)

---

## 🔧 Installed Components

### Core Infrastructure
- **RKE2:** v1.33.1+rke2r1 (Kubernetes distribution)
- **containerd:** 2.0.5-k3s1 (Container runtime)
- **Calico:** CNI for pod networking
- **HAProxy + Keepalived:** Load balancing and high availability

### Management & Operations
- **Rancher:** v2.11.2 (Multi-cluster management platform)
  - URL: https://rancher-uslab.antbrains.com
- **Velero:** Backup and disaster recovery

### Storage
- **Rook-Ceph:** v1.15.8 (Distributed storage orchestrator)
  - Dashboard: https://ceph-uslab.antbrains.com
  - Storage Classes:
    - `ceph-block` (default) - RBD block storage
    - `ceph-filesystem` - CephFS shared filesystem
    - `ceph-bucket` - Object storage (S3-compatible)
    - `postgres-fast-ssd` - PostgreSQL data optimized
    - `postgres-wal-ssd` - PostgreSQL WAL optimized

### Networking & Security
- **Nginx Ingress Controller:** HTTP/HTTPS routing
- **cert-manager:** v1.16.2 (Automatic TLS certificate management)
- **MetalLB:** Bare metal load balancer (IP pool: 10.0.1.101-200)

---

## 📁 Directory Structure

```
Rack2/
├── README.md                                    # This file
├── COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md  # Main comprehensive guide
├── QUICK_REFERENCE_GUIDE.md                    # Quick reference
├── rke2-cluster/
│   ├── US-LabRKE2ClussterSetUp.md              # Original RKE2 setup doc
│   └── hosts                                    # /etc/hosts entries
└── ceph-cluster/
    ├── cluster-values.yaml                      # Ceph cluster Helm values
    ├── values.yaml                              # Rook operator values
    └── ingres.yaml                              # Ceph dashboard ingress
```

---

## 🚀 Quick Start

### Access the Cluster

```bash
# Set kubeconfig
export KUBECONFIG=~/Documents/us-cluster.yaml

# Verify access
kubectl get nodes
kubectl get pods -A
```

### Access Management UIs

- **Rancher:** https://rancher-uslab.antbrains.com
- **Ceph Dashboard:** https://ceph-uslab.antbrains.com
- **HAProxy Stats:** http://10.0.1.30:8404/stats

### Common Operations

```bash
# Check cluster health
kubectl get nodes
kubectl top nodes

# Check Ceph health
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status

# View logs
kubectl logs -f <pod> -n <namespace>

# Scale deployment
kubectl scale deployment <name> --replicas=3 -n <namespace>
```

---

## 📖 How to Use This Documentation

### For New Deployments
1. Read **COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md** from start to finish
2. Follow the 9 phases in order
3. Keep **QUICK_REFERENCE_GUIDE.md** handy for commands

### For Daily Operations
1. Use **QUICK_REFERENCE_GUIDE.md** for common tasks
2. Refer to **COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md** "Operations and Maintenance" section

### For Troubleshooting
1. Check **QUICK_REFERENCE_GUIDE.md** "Troubleshooting Quick Checks"
2. Refer to **COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md** "Troubleshooting" section

### For Disaster Recovery
1. Follow **COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md** "Backup and Disaster Recovery" section

---

## 🔐 Security Notes

- All ingress resources use TLS certificates from Let's Encrypt
- RBAC is enabled for access control
- Network policies can be applied for pod-to-pod communication restrictions
- Secrets are stored in Kubernetes secrets (consider external secret management for production)

---

## 📞 Support

- **Infrastructure Team:** infrastructure@antbrains.com
- **Documentation Issues:** Create an issue or contact the infrastructure team

---

## 📝 Document History

| Date       | Version | Changes                                      |
|------------|---------|----------------------------------------------|
| 2025-06-10 | 1.0     | Initial cluster deployment                   |
| 2026-01-22 | 2.0     | Complete documentation overhaul and update   |

---

## ✅ Checklist for New Team Members

- [ ] Read COMPLETE_ON_PREM_KUBERNETES_SETUP_GUIDE.md
- [ ] Set up kubectl access with provided kubeconfig
- [ ] Access Rancher UI and familiarize yourself with the interface
- [ ] Review QUICK_REFERENCE_GUIDE.md
- [ ] SSH access to all nodes configured
- [ ] Understand backup and disaster recovery procedures
- [ ] Know how to check cluster health
- [ ] Understand troubleshooting procedures

---

**Last Updated:** January 22, 2026  
**Maintained By:** Krista Infrastructure Team

