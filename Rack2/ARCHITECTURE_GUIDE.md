# US Lab On-Premises Kubernetes Cluster Architecture Guide

**Document Version:** 1.0
**Date:** January 2026
**Cluster Name:** uslab-cluster
**Location:** US Lab - Rack2

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Infrastructure Components](#infrastructure-components)
4. [RKE2 Architecture](#rke2-architecture)
5. [Ceph Storage Architecture](#ceph-storage-architecture)
6. [Network Architecture](#network-architecture)
7. [High Availability Design](#high-availability-design)
8. [Data Flow Diagrams](#data-flow-diagrams)
9. [Scaling and Performance](#scaling-and-performance)
10. [Security Architecture](#security-architecture)

---

## Executive Summary

### Cluster Overview

The US Lab Kubernetes cluster is a production-grade, on-premises Kubernetes deployment built on RKE2 (Rancher Kubernetes Engine 2) with Rook-Ceph distributed storage. The cluster provides a robust, scalable platform for running containerized applications with enterprise-level features.

**Key Specifications:**
- **Total Nodes:** 7 (1 control plane + 6 workers)
- **Total Compute:** ~360 CPU cores, ~1.5TB RAM
- **Kubernetes Version:** v1.33.1+rke2r1
- **Storage System:** Rook-Ceph v19.2.2 (Squid)
- **Network Plugin:** Calico CNI with VXLAN
- **Management Platform:** Rancher v2.11.2

**Design Principles:**
- **High Availability:** Redundant components and automatic failover
- **Scalability:** Horizontal scaling for compute and storage
- **Security:** RBAC, network policies, TLS encryption
- **Observability:** Comprehensive monitoring and logging
- **Automation:** Self-healing and automatic recovery

---

## Architecture Overview

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          External Network (10.0.1.0/24)                      │
│                                                                               │
│  ┌──────────────┐                                                            │
│  │   Clients    │                                                            │
│  │  (Users/Apps)│                                                            │
│  └──────┬───────┘                                                            │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │              Virtual IP: 10.0.1.100                               │       │
│  │         (k8s-api.antbrains.com, rancher-uslab.antbrains.com)     │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │                    HAProxy + Keepalived                           │       │
│  │                    (Load Balancer on fratelli)                    │       │
│  │  - API Server Load Balancing (6443 → 6444)                       │       │
│  │  - HTTP/HTTPS Load Balancing (80/443)                            │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│         │                                                                     │
└─────────┼─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster (RKE2)                             │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Control Plane (fratelli)                            │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │  │
│  │  │  API Server  │  │  Scheduler   │  │  Controller  │                │  │
│  │  │   (6443)     │  │              │  │   Manager    │                │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                │  │
│  │  ┌──────────────────────────────────────────────────┐                │  │
│  │  │              etcd (Cluster State)                 │                │  │
│  │  └──────────────────────────────────────────────────┘                │  │
│  │  IP: 10.0.1.30 | 40 CPU | 128GB RAM                                  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Worker Nodes                                   │  │
│  │                                                                         │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │    pane     │  │    cicis    │  │    cesar    │  │    papaj    │  │  │
│  │  │ 10.0.1.25   │  │ 10.0.1.35   │  │ 10.0.1.40   │  │ 10.0.1.45   │  │  │
│  │  │ 40C/128GB   │  │ 40C/128GB   │  │ 40C/128GB   │  │ 40C/128GB   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                         │  │
│  │  ┌─────────────┐  ┌─────────────┐                                     │  │
│  │  │  grimaldi   │  │    rosso    │                                     │  │
│  │  │ 10.0.1.50   │  │ 10.0.1.60   │                                     │  │
│  │  │ 80C/640GB   │  │ 40C/128GB   │                                     │  │
│  │  │ (High Cap)  │  │             │                                     │  │
│  │  └─────────────┘  └─────────────┘                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Distributed Storage (Ceph)                          │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │  │
│  │  │ OSD (pane)│  │OSD(cicis)│  │OSD(cesar)│  │OSD(papaj)│              │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │  │
│  │  ┌──────────┐  ┌──────────┐                                           │  │
│  │  │OSD(grima)│  │OSD(rosso)│                                           │  │
│  │  └──────────┘  └──────────┘                                           │  │
│  │                                                                         │  │
│  │  Storage Classes: ceph-block, ceph-filesystem, ceph-bucket            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Stack

| Layer | Components | Purpose |
|-------|------------|---------|
| **Management** | Rancher UI | Multi-cluster management, RBAC, monitoring |
| **Application** | Pods, Deployments, Services | Application workloads |
| **Orchestration** | RKE2 (Kubernetes) | Container orchestration and scheduling |
| **Networking** | Calico CNI, MetalLB, Nginx Ingress | Pod networking, load balancing, ingress |
| **Storage** | Rook-Ceph | Distributed persistent storage |
| **Compute** | containerd | Container runtime |
| **Infrastructure** | Ubuntu 22.04/24.04 | Operating system |
| **Hardware** | Physical servers | Compute, memory, storage |

---

## Infrastructure Components

### Node Inventory

#### Control Plane Node

| Hostname | IP Address | Role | CPU | Memory | Storage | OS |
|----------|------------|------|-----|--------|---------|-----|
| fratelli | 10.0.1.30 | Control Plane, etcd, Master | 40 cores | 128GB | Multiple disks | Ubuntu 24.04.2 LTS |

**Responsibilities:**
- Kubernetes API Server (port 6443)
- etcd cluster state database
- Scheduler (assigns pods to nodes)
- Controller Manager (manages cluster state)
- HAProxy load balancer
- Keepalived (VIP management)

#### Worker Nodes

| Hostname | IP Address | CPU | Memory | Storage | OS | Special Notes |
|----------|------------|-----|--------|---------|-----|---------------|
| pane | 10.0.1.25 | 40 cores | 128GB | Multiple disks | Ubuntu 24.04.3 LTS | Standard worker |
| cicis | 10.0.1.35 | 40 cores | 128GB | Multiple disks | Ubuntu 24.04.3 LTS | Standard worker |
| cesar | 10.0.1.40 | 40 cores | 128GB | Multiple disks | Ubuntu 24.04.2 LTS | Standard worker |
| papaj | 10.0.1.45 | 40 cores | 128GB | Multiple disks | Ubuntu 24.04.2 LTS | Standard worker |
| grimaldi | 10.0.1.50 | 80 cores | 640GB | Multiple disks | Ubuntu 24.04.2 LTS | **High capacity node** |
| rosso | 10.0.1.60 | 40 cores | 128GB | Multiple disks | Ubuntu 22.04.5 LTS | Newer addition |

**Total Cluster Resources:**
- **Total CPU:** ~360 cores
- **Total Memory:** ~1.5TB RAM
- **Total Nodes:** 7 (1 control plane + 6 workers)

**Worker Node Responsibilities:**
- Run application pods
- Provide Ceph OSD storage
- Run CNI networking components
- Run monitoring and logging agents

### Network Configuration

| Component | Value | Purpose |
|-----------|-------|---------|
| **Network Subnet** | 10.0.1.0/24 | Physical network for all nodes |
| **Virtual IP (VIP)** | 10.0.1.100 | Kubernetes API and Rancher access |
| **Pod CIDR** | 10.48.0.0/16 | IP range for pods (65,536 IPs) |
| **Service CIDR** | 10.49.0.0/16 | IP range for services (65,536 IPs) |
| **MetalLB Pool** | 10.0.1.101-200 | External IPs for LoadBalancer services |
| **DNS Domain** | antbrains.com | External DNS domain |

### Key Services and URLs

| Service | URL | Port | Purpose |
|---------|-----|------|---------|
| **Kubernetes API** | https://k8s-api.antbrains.com:6443 | 6443 | Cluster API access |
| **Rancher UI** | https://rancher-uslab.antbrains.com | 443 | Cluster management interface |
| **Ceph Dashboard** | https://ceph-uslab.antbrains.com:8443 | 8443 | Storage monitoring |

---

## RKE2 Architecture

### What is RKE2?

**RKE2 (Rancher Kubernetes Engine 2)** is Rancher's next-generation Kubernetes distribution, designed for security and compliance in the U.S. Federal Government sector. It combines the best of RKE1 and K3s.

### Why RKE2 for This Cluster?

**Key Benefits:**

1. **Security Hardened**
   - CIS Kubernetes Benchmark compliance out-of-the-box
   - FIPS 140-2 compliant cryptography
   - SELinux support
   - Pod Security Standards enabled by default

2. **Production Ready**
   - Stable, well-tested Kubernetes distribution
   - Enterprise support available from SUSE/Rancher
   - Regular security updates and patches

3. **Simple Installation**
   - Single binary installation
   - Minimal dependencies
   - Easy to upgrade and maintain

4. **Embedded Components**
   - containerd (container runtime)
   - Calico CNI (networking)
   - CoreDNS (DNS)
   - Nginx Ingress Controller
   - Metrics Server

### RKE2 Architecture Components

#### Control Plane Components (on fratelli)

```
┌─────────────────────────────────────────────────────────────────┐
│                    Control Plane (fratelli)                     │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   kube-apiserver                         │  │
│  │  - REST API for all cluster operations                  │  │
│  │  - Authentication and authorization                      │  │
│  │  - Admission control                                     │  │
│  │  - Validates and configures API objects                 │  │
│  │  - Port: 6443 (secured with TLS)                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            ▲                                    │
│                            │                                    │
│  ┌─────────────────────────┴────────────────────────────────┐  │
│  │                        etcd                              │  │
│  │  - Distributed key-value store                          │  │
│  │  - Stores all cluster state and configuration           │  │
│  │  - Consistent and highly-available                      │  │
│  │  - Raft consensus algorithm                             │  │
│  │  - Automatic snapshots for backup                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  kube-scheduler                          │  │
│  │  - Watches for newly created pods                       │  │
│  │  - Selects optimal node for pod placement               │  │
│  │  - Considers: resources, affinity, taints/tolerations   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              kube-controller-manager                     │  │
│  │  - Node Controller: monitors node health                │  │
│  │  - Replication Controller: maintains pod count          │  │
│  │  - Endpoints Controller: populates endpoints            │  │
│  │  - Service Account Controller: creates default accounts │  │
│  │  - And many more controllers...                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  cloud-controller-manager                │  │
│  │  - Manages cloud-specific control logic                 │  │
│  │  - In our case: minimal (on-prem cluster)               │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Worker Node Components (on all nodes including fratelli)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Worker Node                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                      kubelet                             │  │
│  │  - Primary node agent                                    │  │
│  │  - Registers node with API server                       │  │
│  │  - Manages pods and containers                          │  │
│  │  - Reports node and pod status                          │  │
│  │  - Executes liveness/readiness probes                   │  │
│  │  - Mounts volumes                                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            │                                    │
│                            ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    containerd                            │  │
│  │  - Container runtime (CRI implementation)                │  │
│  │  - Manages container lifecycle                          │  │
│  │  - Image pulling and storage                            │  │
│  │  - Container execution                                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    kube-proxy                            │  │
│  │  - Network proxy on each node                           │  │
│  │  - Implements Kubernetes Service concept                │  │
│  │  - Maintains network rules (iptables/IPVS)              │  │
│  │  - Enables service load balancing                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Calico CNI                             │  │
│  │  - Pod networking                                        │  │
│  │  - Network policy enforcement                           │  │
│  │  - VXLAN overlay network                                │  │
│  │  - IP address management (IPAM)                         │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### How RKE2 Works: Request Flow

**Example: User creates a deployment**

```
1. User → kubectl → API Server (fratelli:6443)
   └─ Authentication (TLS certificates)
   └─ Authorization (RBAC)
   └─ Admission Controllers (validation, mutation)

2. API Server → etcd
   └─ Stores deployment object in etcd

3. Deployment Controller (in controller-manager) → API Server
   └─ Watches for new deployments
   └─ Creates ReplicaSet object

4. ReplicaSet Controller → API Server
   └─ Watches for new ReplicaSets
   └─ Creates Pod objects

5. Scheduler → API Server
   └─ Watches for unscheduled pods
   └─ Selects best node based on:
      - Resource availability (CPU, memory)
      - Node affinity/anti-affinity
      - Taints and tolerations
      - Pod topology spread
   └─ Updates pod with node assignment

6. Kubelet (on selected worker node) → API Server
   └─ Watches for pods assigned to its node
   └─ Pulls container image
   └─ Creates container via containerd
   └─ Configures networking via Calico
   └─ Mounts volumes (from Ceph if needed)
   └─ Starts container
   └─ Reports status back to API server

7. kube-proxy (on all nodes)
   └─ Watches for new services
   └─ Updates iptables/IPVS rules
   └─ Enables service load balancing
```

### RKE2 vs Standard Kubernetes

| Feature | Standard Kubernetes | RKE2 |
|---------|-------------------|------|
| **Installation** | Complex (kubeadm, manual) | Simple (single script) |
| **Security** | Manual hardening required | CIS hardened by default |
| **Container Runtime** | Choose your own | containerd embedded |
| **CNI** | Choose your own | Calico embedded |
| **Ingress** | Manual installation | Nginx embedded |
| **Updates** | Manual process | Simplified upgrade path |
| **FIPS Compliance** | Manual configuration | Built-in support |
| **SELinux** | Manual configuration | Pre-configured policies |

---


## Ceph Storage Architecture

### What is Ceph?

**Ceph** is a unified, distributed storage system designed for excellent performance, reliability, and scalability. It provides three types of storage in one platform:

1. **Block Storage (RBD)** - Like virtual hard drives for VMs/containers
2. **File Storage (CephFS)** - Shared filesystem for multiple clients
3. **Object Storage (RGW)** - S3-compatible object storage

### Why Ceph for This Cluster?

**Key Benefits:**

1. **Distributed and Scalable**
   - Data distributed across all nodes
   - Add nodes to increase capacity and performance
   - No single point of failure

2. **Self-Healing**
   - Automatic data replication
   - Automatic recovery from failures
   - Continuous data scrubbing for integrity

3. **Flexible**
   - Multiple storage types from one system
   - Dynamic provisioning via Kubernetes
   - Thin provisioning support

4. **Production Ready**
   - Battle-tested in large deployments
   - Active community and enterprise support
   - Integrated with Kubernetes via Rook

### Ceph Architecture Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Ceph Cluster Architecture                          │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                        Client Layer                                   │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │ │
│  │  │ RBD Clients  │  │CephFS Clients│  │ RGW Clients  │                │ │
│  │  │ (Block)      │  │ (File)       │  │ (Object/S3)  │                │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                  │                                          │
│                                  ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                      RADOS (Reliable Autonomic                        │ │
│  │                   Distributed Object Store)                           │ │
│  │                                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                    MON (Monitors)                               │ │ │
│  │  │  - Maintain cluster map (OSD map, MON map, PG map, CRUSH map)  │ │ │
│  │  │  - Provide consensus for distributed decision-making           │ │ │
│  │  │  - Track cluster health and state                              │ │ │
│  │  │  - Quorum-based (need majority for decisions)                  │ │ │
│  │  │  - Deployed on: pane, cicis, cesar (3 monitors)                │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                    MGR (Managers)                               │ │ │
│  │  │  - Provide monitoring and metrics                              │ │ │
│  │  │  - Host Ceph dashboard (web UI)                                │ │ │
│  │  │  - Expose Prometheus metrics                                   │ │ │
│  │  │  - Manage plugins and modules                                  │ │ │
│  │  │  - Active/standby configuration                                │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                    MDS (Metadata Servers)                       │ │ │
│  │  │  - Required ONLY for CephFS                                    │ │ │
│  │  │  - Manage filesystem metadata (directories, permissions)       │ │ │
│  │  │  - Not needed for RBD or RGW                                   │ │ │
│  │  │  - Active/standby configuration                                │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                 OSD (Object Storage Daemons)                    │ │ │
│  │  │  - Store actual data on disks                                  │ │ │
│  │  │  - Handle data replication                                     │ │ │
│  │  │  - Handle data recovery and rebalancing                        │ │ │
│  │  │  - Perform data scrubbing and integrity checks                 │ │ │
│  │  │  - One OSD per disk/device                                     │ │ │
│  │  │                                                                 │ │ │
│  │  │  Deployed on all worker nodes:                                 │ │ │
│  │  │  ┌──────────┬──────────┬──────────┬──────────┬──────────┬─────┐│ │ │
│  │  │  │OSD (pane)│OSD(cicis)│OSD(cesar)│OSD(papaj)│OSD(grima)│OSD  ││ │ │
│  │  │  │          │          │          │          │  ldi)    │(ros)││ │ │
│  │  │  └──────────┴──────────┴──────────┴──────────┴──────────┴─────┘│ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Ceph Data Storage: How It Works

#### 1. Data Placement with CRUSH Algorithm

**CRUSH (Controlled Replication Under Scalable Hashing)** is Ceph's data placement algorithm.

**How CRUSH works:**

```
Object → Hash Function → Placement Group (PG) → CRUSH → OSD Set
```

**Step-by-step:**

1. **Object Creation**
   - Application writes data (e.g., Kubernetes PVC)
   - Data is split into objects (typically 4MB each)
   - Each object gets a unique name

2. **Hash to Placement Group**
   - Object name is hashed
   - Hash determines which Placement Group (PG) owns the object
   - PGs are logical groupings of objects (typically 128-256 PGs per OSD)

3. **CRUSH Maps PG to OSDs**
   - CRUSH algorithm calculates which OSDs should store the PG
   - Considers: replication factor, failure domains, weights
   - Deterministic: same input always gives same output
   - No central lookup table needed!

4. **Data Written to OSDs**
   - Primary OSD receives write
   - Primary OSD replicates to secondary OSDs
   - Write acknowledged when all replicas are written

**Example:**
```
User creates 100MB PVC
  ↓
Ceph splits into 25 objects (4MB each)
  ↓
Object "obj1" → Hash → PG 1.3a
  ↓
CRUSH: PG 1.3a → [OSD.0 (pane), OSD.2 (cesar), OSD.4 (grimaldi)]
  ↓
Data written to 3 OSDs (if replication = 3)
```

#### 2. Ceph Replication: How Data is Protected

**THIS IS THE MOST IMPORTANT SECTION FOR UNDERSTANDING CEPH RELIABILITY**

##### Replication Factor (Pool Size)

**Current Configuration:** `osd_pool_default_size = 1` (from cluster-values.yaml)

This means **NO REPLICATION** - each piece of data exists in only ONE location.

**⚠️ IMPORTANT: This is NOT recommended for production!**

##### How Replication Works (When Properly Configured)

**Recommended Production Configuration:** `size = 3, min_size = 2`

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Ceph Replication Mechanism                               │
│                                                                             │
│  Step 1: Client Writes Data                                                │
│  ┌──────────┐                                                               │
│  │  Client  │  "Write 100MB file"                                           │
│  │  (Pod)   │                                                               │
│  └─────┬────┘                                                               │
│        │                                                                     │
│        ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Ceph splits file into objects (25 objects × 4MB each)             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ▼                                                                     │
│  Step 2: CRUSH Determines Placement (for each object)                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Object "obj1" → CRUSH → [OSD.0, OSD.3, OSD.5]                     │   │
│  │                          Primary  Replica1  Replica2                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│        │                                                                     │
│        ▼                                                                     │
│  Step 3: Primary OSD Receives Write                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  OSD.0 (pane) - PRIMARY                                              │  │
│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │  │ 1. Receives write request from client                          │  │  │
│  │  │ 2. Writes data to local disk (journal first, then data)        │  │  │
│  │  │ 3. Simultaneously sends data to replica OSDs                   │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│        │                                                                     │
│        ├──────────────────────┬──────────────────────────────────────┐      │
│        ▼                      ▼                                      ▼      │
│  ┌──────────────┐       ┌──────────────┐                    ┌──────────────┐│
│  │ OSD.0 (pane) │       │ OSD.3 (papaj)│                    │OSD.5(grimaldi││
│  │   PRIMARY    │       │   REPLICA 1  │                    │   REPLICA 2  ││
│  │              │       │              │                    │              ││
│  │ [DATA COPY 1]│       │ [DATA COPY 2]│                    │ [DATA COPY 3]││
│  │              │       │              │                    │              ││
│  │ Disk: /dev/  │       │ Disk: /dev/  │                    │ Disk: /dev/  ││
│  │   sdb        │       │   sdb        │                    │   sdb        ││
│  └──────┬───────┘       └──────┬───────┘                    └──────┬───────┘│
│         │                      │                                   │        │
│         └──────────────────────┴───────────────────────────────────┘        │
│                                │                                            │
│                                ▼                                            │
│  Step 4: Acknowledgment                                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  All replicas confirm write → Primary OSD → Client                 │   │
│  │  "Write successful" (only after ALL replicas are written)          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

##### Replication Configuration Parameters

| Parameter | Current Value | Recommended | Meaning |
|-----------|---------------|-------------|---------|
| **size** | 1 | 3 | Total number of copies of data |
| **min_size** | 1 | 2 | Minimum replicas required for I/O |

**What these mean:**

- **size = 3**: Ceph maintains 3 copies of every piece of data
  - 1 primary copy
  - 2 replica copies
  - Distributed across different nodes (failure domains)

- **min_size = 2**: Cluster accepts writes if at least 2 copies can be written
  - Allows operation even if 1 OSD is down
  - Prevents data loss during single-node failure
  - If only 1 OSD available, cluster goes read-only (protects data)

##### Failure Scenarios with Different Replication Levels

**Scenario 1: Current Configuration (size=1, min_size=1)**

```
Normal Operation:
┌─────────────┐
│ OSD.0 (pane)│
│  [DATA]     │  ← Only copy
└─────────────┘

If pane node fails:
┌─────────────┐
│ OSD.0 (pane)│
│  [OFFLINE]  │  ← DATA LOST! ❌
└─────────────┘

Result: PERMANENT DATA LOSS
```

**Scenario 2: Production Configuration (size=3, min_size=2)**

```
Normal Operation:
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ OSD.0 (pane)│  │OSD.3 (papaj)│  │OSD.5(grimal)│
│  [DATA]     │  │  [DATA]     │  │  [DATA]     │
└─────────────┘  └─────────────┘  └─────────────┘
   Primary         Replica 1        Replica 2

If pane node fails:
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ OSD.0 (pane)│  │OSD.3 (papaj)│  │OSD.5(grimal)│
│  [OFFLINE]  │  │  [DATA] ✓   │  │  [DATA] ✓   │
└─────────────┘  └─────────────┘  └─────────────┘
                   New Primary      Replica

Result:
1. OSD.3 becomes new primary (automatic failover)
2. Data still accessible (2 copies remain)
3. Ceph starts recovery process
4. New replica created on another OSD
5. Back to 3 copies within minutes

If BOTH pane AND papaj fail:
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ OSD.0 (pane)│  │OSD.3 (papaj)│  │OSD.5(grimal)│
│  [OFFLINE]  │  │  [OFFLINE]  │  │  [DATA] ✓   │
└─────────────┘  └─────────────┘  └─────────────┘

Result:
1. Only 1 copy remains (< min_size=2)
2. Cluster goes READ-ONLY for this data
3. Prevents further data loss
4. When nodes return, cluster recovers
```

##### How Ceph Recovers from Failures

**Automatic Recovery Process:**

```
1. OSD Failure Detected
   ├─ Monitors detect OSD is down (heartbeat timeout)
   ├─ Wait grace period (default: 5 minutes)
   └─ Mark OSD as "out" if still down

2. Recovery Planning
   ├─ Identify all PGs with insufficient replicas
   ├─ Calculate new OSD placements using CRUSH
   └─ Prioritize recovery based on data risk

3. Data Recovery
   ├─ Copy data from surviving replicas
   ├─ Create new replicas on healthy OSDs
   ├─ Verify data integrity (checksums)
   └─ Update cluster map

4. Rebalancing
   ├─ Distribute data evenly across OSDs
   ├─ Respect CRUSH rules (failure domains)
   └─ Throttle to avoid performance impact

5. Completion
   ├─ All PGs back to desired replica count
   ├─ Cluster health returns to HEALTH_OK
   └─ Ready for next failure
```

**Recovery Timeline Example:**

```
Time    Event
------  --------------------------------------------------------
00:00   Node "pane" crashes (power failure)
00:01   Monitors detect OSD.0 is down
00:05   Grace period expires, OSD.0 marked "out"
00:06   Recovery begins (copying data from replicas)
00:15   50% of data recovered
00:25   100% of data recovered
00:30   Cluster health: HEALTH_OK

If pane returns:
01:00   Node "pane" comes back online
01:01   OSD.0 starts, marked "in"
01:02   Ceph compares data on OSD.0 with current data
01:05   Incremental recovery (only changed data)
01:10   Rebalancing complete
01:15   Cluster fully recovered
```



##### Changing Replication Factor

**To change from size=1 to size=3 (RECOMMENDED):**

```bash
# Connect to Ceph toolbox
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- bash

# Check current pool configuration
ceph osd pool ls detail

# Set replication for existing pools
ceph osd pool set ceph-blockpool size 3
ceph osd pool set ceph-blockpool min_size 2

# Set default for new pools
ceph config set global osd_pool_default_size 3
ceph config set global osd_pool_default_min_size 2

# Verify
ceph osd pool get ceph-blockpool size
ceph osd pool get ceph-blockpool min_size

# Monitor rebalancing (will take time to create replicas)
ceph status
ceph -w  # Watch in real-time
```

**Impact of changing to size=3:**
- **Storage Usage:** 3x increase (100GB becomes 300GB used)
- **Write Performance:** Slightly slower (must write to 3 locations)
- **Read Performance:** Faster (can read from any replica)
- **Reliability:** Can survive 2 simultaneous node failures
- **Recovery Time:** Faster (more replicas to recover from)

---

## Network Architecture

### Network Layers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Network Architecture                                │
│                                                                             │
│  Layer 1: External Network (10.0.1.0/24)                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  - Physical network connecting all nodes                              │ │
│  │  - 1Gbps or 10Gbps Ethernet                                           │ │
│  │  │  - Node-to-node communication                                       │ │
│  │  - Managed switch with VLAN support                                   │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                  │                                          │
│                                  ▼                                          │
│  Layer 2: Kubernetes Service Network (10.49.0.0/16)                       │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  - Virtual IPs for Kubernetes services                                │ │
│  │  - Managed by kube-proxy (IPVS mode)                                  │ │
│  │  - Load balancing across pod endpoints                                │ │
│  │  - ClusterIP, NodePort, LoadBalancer types                            │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                  │                                          │
│                                  ▼                                          │
│  Layer 3: Pod Network (10.48.0.0/16) - Calico CNI                         │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  - VXLAN overlay network                                              │ │
│  │  - Each pod gets unique IP from this range                            │ │
│  │  - Cross-node pod communication                                       │ │
│  │  - Network policy enforcement                                         │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                  │                                          │
│                                  ▼                                          │
│  Layer 4: Storage Network (Same as External: 10.0.1.0/24)                 │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  - Ceph OSD communication                                             │ │
│  │  - Data replication traffic                                           │ │
│  │  - Recovery and rebalancing traffic                                   │ │
│  │  - In production, often separate network for performance             │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Network Components

#### 1. Calico CNI (Container Network Interface)

**Purpose:** Provides networking and network policy for pods

**How it works:**

```
Pod A (node1) wants to talk to Pod B (node2)
  ↓
Pod A sends packet to 10.48.2.15 (Pod B's IP)
  ↓
Calico on node1 intercepts packet
  ↓
Encapsulates in VXLAN tunnel
  ↓
Sends to node2's physical IP (10.0.1.35)
  ↓
Calico on node2 decapsulates packet
  ↓
Delivers to Pod B
```

**Components:**
- **calico-node:** DaemonSet running on every node
- **calico-kube-controllers:** Manages Calico resources
- **calico-typha:** Scalability component for large clusters

**Configuration:**
- **Mode:** VXLAN (overlay network)
- **IPAM:** Calico's built-in IP address management
- **MTU:** 1450 (to account for VXLAN overhead)

#### 2. MetalLB (Load Balancer)

**Purpose:** Provides LoadBalancer service type for bare-metal

**Mode:** Layer 2 (ARP-based)

**How it works:**

```
Service created with type: LoadBalancer
  ↓
MetalLB controller assigns IP from pool (10.0.1.101-200)
  ↓
MetalLB speaker pods announce IP via ARP
  ↓
One speaker becomes leader for this IP (leader election)
  ↓
External clients send traffic to this IP
  ↓
Leader node receives traffic
  ↓
kube-proxy forwards to service endpoints (pods)
```

#### 3. Nginx Ingress Controller

**Purpose:** HTTP/HTTPS routing and SSL termination

**How it works:**

```
External HTTP request → https://app.antbrains.com
  ↓
DNS resolves to VIP (10.0.1.100) or MetalLB IP
  ↓
HAProxy forwards to Ingress Controller pod
  ↓
Nginx Ingress Controller:
  - Terminates TLS (if configured)
  - Matches host header to Ingress rules
  - Routes to appropriate service
  ↓
Service forwards to pod endpoints
```

---

## High Availability Design

### Control Plane HA

**Current Setup:** Single control plane node (fratelli)

**⚠️ Single Point of Failure:** If fratelli fails, cluster management is unavailable

**Recommended Production Setup:** 3 control plane nodes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              Recommended HA Control Plane (3 nodes)                         │
│                                                                             │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐             │
│  │  fratelli    │      │    pane      │      │    cicis     │             │
│  │  (Master 1)  │      │  (Master 2)  │      │  (Master 3)  │             │
│  │              │      │              │      │              │             │
│  │ API Server   │      │ API Server   │      │ API Server   │             │
│  │ Scheduler    │      │ Scheduler    │      │ Scheduler    │             │
│  │ Controller   │      │ Controller   │      │ Controller   │             │
│  │ etcd Member  │      │ etcd Member  │      │ etcd Member  │             │
│  └──────────────┘      └──────────────┘      └──────────────┘             │
│         │                     │                     │                      │
│         └─────────────────────┴─────────────────────┘                      │
│                               │                                            │
│                               ▼                                            │
│                    ┌─────────────────────┐                                 │
│                    │  etcd Cluster       │                                 │
│                    │  (Raft Consensus)   │                                 │
│                    │  - Quorum: 2 of 3   │                                 │
│                    │  - Can lose 1 node  │                                 │
│                    └─────────────────────┘                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Benefits of 3 control planes:**
- Can survive 1 control plane failure
- etcd maintains quorum with 2/3 nodes
- API server load balanced across 3 nodes
- Zero downtime upgrades possible

### Storage HA (Ceph)

**Current Setup:** 6 OSDs across 6 worker nodes, replication size=1

**HA Characteristics:**
- ✅ Distributed across multiple nodes
- ✅ No single point of failure for storage capacity
- ❌ No data replication (size=1)
- ❌ Node failure = data loss

**With size=3 replication:**
- ✅ Can survive 2 simultaneous node failures
- ✅ Automatic failover and recovery
- ✅ No data loss on node failure
- ✅ Continuous availability during failures

### Network HA

**Load Balancer HA:**
- HAProxy + Keepalived on fratelli
- Virtual IP (10.0.1.100) managed by Keepalived
- **Current limitation:** Single node (fratelli)
- **Recommended:** Deploy HAProxy + Keepalived on all 3 control planes

**Ingress HA:**
- Nginx Ingress Controller runs as DaemonSet or Deployment with replicas
- Multiple ingress pods across nodes
- Load balanced via MetalLB or NodePort

---


## Data Flow Diagrams

### Complete Request Flow: External User to Application Pod

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Complete Request Flow Example                            │
│                                                                             │
│  User accesses: https://myapp.antbrains.com                                │
│                                                                             │
│  Step 1: DNS Resolution                                                    │
│  ┌──────────┐                                                               │
│  │  User's  │  DNS query: myapp.antbrains.com                              │
│  │  Browser │  ──────────────────────────────────────────────────────────▶ │
│  └──────────┘                                                               │
│       │                                                                     │
│       │  DNS response: 10.0.1.100 (VIP)                                    │
│       ◀──────────────────────────────────────────────────────────────────  │
│       │                                                                     │
│  Step 2: HTTPS Request to VIP                                              │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Virtual IP: 10.0.1.100 (Keepalived on fratelli)                   │   │
│  │  - Managed by Keepalived                                            │   │
│  │  - Responds to ARP requests                                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 3: HAProxy Load Balancer                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  HAProxy (on fratelli)                                              │   │
│  │  - Receives HTTPS request on port 443                               │   │
│  │  - Forwards to Nginx Ingress Controller pods                        │   │
│  │  - Load balances across multiple ingress pods                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 4: Nginx Ingress Controller                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Nginx Ingress Controller Pod (e.g., on pane)                      │   │
│  │  - Terminates TLS (decrypts HTTPS)                                  │   │
│  │  - Reads Host header: myapp.antbrains.com                           │   │
│  │  - Matches to Ingress rule                                          │   │
│  │  - Routes to Service: myapp-service                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 5: Kubernetes Service                                                │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Service: myapp-service (ClusterIP: 10.49.15.20)                    │   │
│  │  - Virtual IP managed by kube-proxy                                 │   │
│  │  - Endpoints: [10.48.2.10, 10.48.3.15, 10.48.4.20]                 │   │
│  │  - kube-proxy load balances to one endpoint                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 6: Pod Network (Calico)                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  kube-proxy selects endpoint: 10.48.3.15 (pod on cicis)            │   │
│  │  - Packet sent to pod IP                                            │   │
│  │  - Calico CNI routes packet:                                        │   │
│  │    * Encapsulates in VXLAN                                          │   │
│  │    * Sends to cicis node (10.0.1.35)                                │   │
│  │    * Decapsulates and delivers to pod                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 7: Application Pod                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  myapp-pod-xyz (on cicis node)                                      │   │
│  │  - Container receives HTTP request                                  │   │
│  │  - Application processes request                                    │   │
│  │  - May access storage (Ceph PVC)                                    │   │
│  │  - Returns response                                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Step 8: Response Path (reverse of above)                                  │
│  Pod → Calico → Service → Ingress → HAProxy → VIP → User                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Storage Request Flow: Pod Writes to Ceph

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Storage Write Flow (with replication=3)                  │
│                                                                             │
│  Step 1: Application Writes Data                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Application Pod (e.g., database)                                    │  │
│  │  - Writes 100MB file to mounted PVC                                  │  │
│  │  - PVC backed by Ceph RBD                                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│  Step 2: Kernel and CSI Driver                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  - Kernel filesystem layer                                           │  │
│  │  - Ceph CSI driver (rbd.csi.ceph.com)                                │  │
│  │  - Translates filesystem operations to Ceph RBD operations           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│  Step 3: Ceph Client Library                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  - librbd (Ceph RBD client library)                                  │  │
│  │  - Splits 100MB into 25 objects (4MB each)                           │  │
│  │  - For each object:                                                  │  │
│  │    * Hash object name → Placement Group (PG)                         │  │
│  │    * CRUSH algorithm → OSD set [primary, replica1, replica2]        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│  Step 4: Write to Primary OSD                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Primary OSD (e.g., OSD.0 on pane)                                   │  │
│  │  - Receives write request                                            │  │
│  │  - Writes to journal (fast, sequential write)                        │  │
│  │  - Simultaneously sends to replica OSDs                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ├──────────────────────┬────────────────────────────────────────┐    │
│       ▼                      ▼                                        ▼    │
│  ┌──────────┐          ┌──────────┐                            ┌──────────┐│
│  │OSD.0     │          │OSD.3     │                            │OSD.5     ││
│  │(pane)    │          │(papaj)   │                            │(grimaldi)││
│  │PRIMARY   │          │REPLICA 1 │                            │REPLICA 2 ││
│  │          │          │          │                            │          ││
│  │Writes to │          │Writes to │                            │Writes to ││
│  │disk      │          │disk      │                            │disk      ││
│  └────┬─────┘          └────┬─────┘                            └────┬─────┘│
│       │                     │                                       │      │
│       │  ACK                │  ACK                                  │  ACK │
│       └─────────────────────┴───────────────────────────────────────┘      │
│                             │                                              │
│                             ▼                                              │
│  Step 5: Acknowledgment to Client                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Primary OSD → Ceph client → Application                            │  │
│  │  "Write successful" (only after all 3 replicas confirm)             │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Step 6: Background Operations                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  - Journal data flushed to main storage                              │  │
│  │  - Data scrubbing (periodic integrity checks)                        │  │
│  │  - Rebalancing (if cluster topology changes)                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Scaling and Performance

### Horizontal Scaling

#### Adding Worker Nodes

**Process:**
1. Prepare new server (same as Phase 1 in setup guide)
2. Install RKE2 agent
3. Join to cluster using node token
4. Ceph automatically detects new OSDs
5. Data rebalances across new OSDs

**Benefits:**
- More compute capacity for pods
- More storage capacity
- Better fault tolerance
- Improved performance (more parallel processing)

**Example: Adding node "poco"**

```bash
# On new node (poco)
export RKE2_TOKEN="<token-from-fratelli>"
export RKE2_URL="https://10.0.1.100:9345"

curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" sh -
systemctl enable rke2-agent
systemctl start rke2-agent

# Verify on fratelli
kubectl get nodes
# Should show poco in Ready state

# Ceph automatically discovers new OSDs
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd tree
# Should show new OSD from poco
```

#### Scaling Applications

**Horizontal Pod Autoscaler (HPA):**

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: myapp-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: myapp
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

**How it works:**
- Monitors CPU/memory usage
- Automatically adds/removes pods
- Distributes pods across nodes
- Integrates with cluster autoscaler

### Vertical Scaling

#### Increasing Node Resources

**Options:**
1. **Add RAM:** Increase memory capacity
2. **Add CPU:** Add more cores
3. **Add Disks:** Add more OSDs for storage

**For Ceph:**
- Adding disks to existing nodes increases capacity
- Each disk becomes a new OSD
- Automatic rebalancing distributes data

### Performance Optimization

#### Ceph Performance

**Factors affecting performance:**

1. **Disk Type:**
   - **HDD:** 100-200 IOPS, good for capacity
   - **SSD:** 10,000+ IOPS, good for performance
   - **NVMe:** 100,000+ IOPS, best performance

2. **Network:**
   - **1Gbps:** Sufficient for small clusters
   - **10Gbps:** Recommended for production
   - **Separate storage network:** Isolates storage traffic

3. **Replication:**
   - **size=1:** Fastest writes, no redundancy
   - **size=3:** Slower writes (3x network traffic), high redundancy

4. **Journal/WAL:**
   - **Separate SSD for journal:** Improves write performance
   - **Co-located journal:** Simpler, slower

**Optimization tips:**
```bash
# Enable fast read from replicas
ceph osd pool set <pool-name> fast_read true

# Adjust PG count for better distribution
ceph osd pool set <pool-name> pg_num 128
ceph osd pool set <pool-name> pgp_num 128

# Enable compression (saves space, uses CPU)
ceph osd pool set <pool-name> compression_mode aggressive
```

#### Kubernetes Performance

**Resource Requests and Limits:**

```yaml
resources:
  requests:
    cpu: "500m"      # Guaranteed CPU
    memory: "512Mi"  # Guaranteed memory
  limits:
    cpu: "2000m"     # Maximum CPU
    memory: "2Gi"    # Maximum memory
```

**Pod Priority:**

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: high-priority
value: 1000
globalDefault: false
description: "High priority for critical apps"
```

---

## Security Architecture

### Network Security

#### Network Policies

**Example: Restrict pod communication**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
  namespace: production
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  # No ingress rules = deny all
```

**Allow specific traffic:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: frontend
    ports:
    - protocol: TCP
      port: 8080
```

### Authentication and Authorization

#### RBAC (Role-Based Access Control)

**Cluster-wide admin:**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-user
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: User
  name: admin@antbrains.com
  apiGroup: rbac.authorization.k8s.io
```

**Namespace-specific access:**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: developer-binding
  namespace: development
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: developer
subjects:
- kind: User
  name: dev@antbrains.com
  apiGroup: rbac.authorization.k8s.io
```

### Data Security

#### Encryption at Rest

**Ceph encryption:**

```yaml
apiVersion: ceph.rook.io/v1
kind: CephBlockPool
metadata:
  name: encrypted-pool
  namespace: rook-ceph
spec:
  replicated:
    size: 3
  parameters:
    encryption: "true"
```

#### Secrets Management

**Kubernetes Secrets:**

```bash
# Create secret
kubectl create secret generic db-password \
  --from-literal=password='super-secret-password'

# Use in pod
env:
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: db-password
      key: password
```

### TLS/SSL

**Certificate Management with cert-manager:**

- Automatic certificate issuance from Let's Encrypt
- Automatic renewal before expiry
- TLS termination at ingress
- mTLS for service-to-service communication

---

## Conclusion

This architecture provides a robust, scalable, and secure platform for running containerized applications. Key takeaways:

**Strengths:**
- ✅ Distributed storage with Ceph
- ✅ Scalable compute with Kubernetes
- ✅ Automated management with Rancher
- ✅ Flexible networking with Calico
- ✅ Production-grade with RKE2

**Recommendations for Production:**
- ⚠️ **Increase Ceph replication to size=3** (currently size=1)
- ⚠️ **Add 2 more control plane nodes** (currently single master)
- ⚠️ **Implement regular backups** (Velero for Kubernetes, Ceph snapshots)
- ⚠️ **Set up monitoring** (Prometheus + Grafana)
- ⚠️ **Configure alerting** (AlertManager)
- ⚠️ **Document disaster recovery procedures**

**Next Steps:**
1. Review and implement production recommendations
2. Set up monitoring and alerting
3. Establish backup and disaster recovery procedures
4. Conduct regular security audits
5. Plan for capacity growth

---

**Document Information:**
- **Version:** 1.0
- **Last Updated:** January 2026
- **Maintained By:** Infrastructure Team
- **Contact:** admin@antbrains.com


