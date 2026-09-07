# Complete On-Premises Kubernetes Cluster Setup Guide

**Document Version:** 2.0  
**Last Updated:** January 2026  
**Cluster Name:** uslab-cluster  
**Kubernetes Distribution:** RKE2 v1.33.1+rke2r1  
**Author:** Krista Infrastructure Team  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Infrastructure Specifications](#infrastructure-specifications)
4. [Prerequisites](#prerequisites)
5. [Network Planning](#network-planning)
6. [Installation Guide](#installation-guide)
   - [Phase 1: Server Preparation](#phase-1-server-preparation)
   - [Phase 2: Load Balancer Setup](#phase-2-load-balancer-setup)
   - [Phase 3: RKE2 Control Plane Installation](#phase-3-rke2-control-plane-installation)
   - [Phase 4: Worker Node Addition](#phase-4-worker-node-addition)
   - [Phase 5: Storage Configuration (Rook-Ceph)](#phase-5-storage-configuration-rook-ceph)
   - [Phase 6: Rancher Management Platform](#phase-6-rancher-management-platform)
   - [Phase 7: Ingress Controller](#phase-7-ingress-controller)
   - [Phase 8: Certificate Management](#phase-8-certificate-management)
   - [Phase 9: Load Balancer (MetalLB)](#phase-9-load-balancer-metallb)
7. [Operations and Maintenance](#operations-and-maintenance)
8. [Troubleshooting](#troubleshooting)
9. [Backup and Disaster Recovery](#backup-and-disaster-recovery)
10. [Security Best Practices](#security-best-practices)
11. [Appendices](#appendices)

---

## Executive Summary

This document provides a comprehensive guide for deploying and managing a production-grade, on-premises Kubernetes cluster using RKE2 (Rancher Kubernetes Engine 2). The cluster is designed for high availability, scalability, and enterprise workload management.

### Key Features

- **Kubernetes Distribution:** RKE2 v1.33.1+rke2r1 (Production-grade, FIPS-compliant)
- **Container Runtime:** containerd 2.0.5-k3s1
- **Network Plugin:** Calico (VXLAN mode)
- **Storage Solution:** Rook-Ceph (Distributed storage with block, file, and object support)
- **Management Platform:** Rancher v2.11.2
- **Ingress Controller:** Nginx Ingress Controller
- **Certificate Management:** cert-manager with Let's Encrypt
- **Load Balancer:** MetalLB (Bare metal load balancer)
- **Backup Solution:** Velero

### Cluster Topology

- **1 Control Plane Node** (fratelli)
- **6 Worker Nodes** (pane, cicis, cesar, papaj, grimaldi, rosso)
- **Total Capacity:** ~360 CPU cores, ~1.5TB RAM
- **High Availability:** Virtual IP (10.0.1.100) managed by HAProxy + Keepalived

---

## Architecture Overview

### Cluster Architecture Diagram

```
                                    ┌─────────────────────────────────┐
                                    │   External Access (Internet)    │
                                    └────────────┬────────────────────┘
                                                 │
                                    ┌────────────▼────────────────────┐
                                    │  DNS: *.antbrains.com           │
                                    │  - rancher-uslab.antbrains.com  │
                                    │  - ceph-uslab.antbrains.com     │
                                    └────────────┬────────────────────┘
                                                 │
                                    ┌────────────▼────────────────────┐
                                    │  Virtual IP: 10.0.1.100         │
                                    │  (HAProxy + Keepalived)         │
                                    └────────────┬────────────────────┘
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    │                            │                            │
       ┌────────────▼────────────┐  ┌───────────▼──────────┐  ┌──────────────▼─────────────┐
       │  Control Plane (fratelli)│  │  Worker Nodes (6)    │  │  Storage (Rook-Ceph)       │
       │  - API Server :6443      │  │  - pane              │  │  - Distributed across      │
       │  - etcd                  │  │  - cicis             │  │    all worker nodes        │
       │  - Controller Manager    │  │  - cesar             │  │  - Block, File, Object     │
       │  - Scheduler             │  │  - papaj             │  │  - Replication: 1          │
       │  - HAProxy :6444         │  │  - grimaldi (High)   │  │                            │
       │  - Keepalived (VIP)      │  │  - rosso             │  │                            │
       └──────────────────────────┘  └──────────────────────┘  └────────────────────────────┘
```

### Network Architecture

- **Pod Network (CIDR):** 10.48.0.0/16 (Calico VXLAN)
- **Service Network (CIDR):** 10.49.0.0/16
- **Node Network:** 10.0.1.0/24
- **Virtual IP:** 10.0.1.100 (k8s-api.antbrains.com)

---

## Infrastructure Specifications

### Server Inventory

| Hostname | IP Address | Role           | CPU Cores | RAM (GB) | OS                  | Age (Days) | Notes                    |
|----------|------------|----------------|-----------|----------|---------------------|------------|--------------------------|
| fratelli | 10.0.1.30  | Control Plane  | 40        | 128      | Ubuntu 24.04.2 LTS  | 229        | Master + HAProxy + VIP   |
| pane     | 10.0.1.25  | Worker         | 40        | 128      | Ubuntu 24.04.3 LTS  | 99         | Standard worker          |
| cicis    | 10.0.1.35  | Worker         | 40        | 128      | Ubuntu 24.04.3 LTS  | 229        | Standard worker          |
| cesar    | 10.0.1.40  | Worker         | 40        | 128      | Ubuntu 24.04.2 LTS  | 229        | Standard worker          |
| papaj    | 10.0.1.45  | Worker         | 40        | 128      | Ubuntu 24.04.2 LTS  | 229        | Standard worker          |
| grimaldi | 10.0.1.50  | Worker         | 80        | 640      | Ubuntu 24.04.2 LTS  | 229        | High-capacity worker     |
| rosso    | 10.0.1.60  | Worker         | 40        | 128      | Ubuntu 22.04.5 LTS  | 48         | Standard worker          |

**Total Cluster Resources:**
- **CPU:** ~360 cores
- **RAM:** ~1.5 TB
- **Storage:** Ceph distributed storage across all worker nodes

### Network Configuration

| Component          | Value                          | Description                           |
|--------------------|--------------------------------|---------------------------------------|
| Virtual IP         | 10.0.1.100                     | Managed by Keepalived on fratelli     |
| API Server Port    | 6443                           | Kubernetes API (internal)             |
| HAProxy Port       | 6444                           | Load balanced API access              |
| Pod CIDR           | 10.48.0.0/16                   | Calico pod network                    |
| Service CIDR       | 10.49.0.0/16                   | Kubernetes service network            |
| DNS Domain         | cluster.local                  | Internal cluster DNS                  |
| External Domain    | antbrains.com                  | Public domain for ingress             |

---

## Prerequisites

### Required Software and Tools

Before starting the installation, ensure you have:

**On All Servers:**
- Ubuntu 22.04 LTS or 24.04 LTS (minimal installation)
- Root or sudo access
- Internet connectivity (for package downloads)
- SSH access configured

**On Your Local Machine:**
- kubectl (Kubernetes CLI)
- helm (Kubernetes package manager)
- SSH client
- Text editor

### Hardware Requirements

**Minimum Requirements per Node:**
- **Control Plane:** 4 CPU cores, 8GB RAM, 100GB disk
- **Worker Node:** 4 CPU cores, 8GB RAM, 100GB disk
- **Storage Node:** Additional disks for Ceph OSDs

**Recommended (Production):**
- **Control Plane:** 8+ CPU cores, 16GB+ RAM, 200GB+ SSD
- **Worker Node:** 16+ CPU cores, 32GB+ RAM, 200GB+ SSD
- **Storage:** Dedicated SSDs/NVMe for Ceph OSDs

### Network Requirements

- **Static IP addresses** for all nodes
- **DNS resolution** (or /etc/hosts configuration)
- **NTP synchronization** across all nodes
- **Firewall rules** (see Appendix A for port requirements)
- **Virtual IP** available for HAProxy/Keepalived

### Access Requirements

- **Domain name** with DNS control (for Let's Encrypt certificates)
- **Wildcard DNS** or individual A records for ingress
- **SSH key-based authentication** (recommended)

---

## Network Planning

### IP Address Allocation

```
Network: 10.0.1.0/24

Reserved IPs:
- 10.0.1.1-10.0.1.20    : Network infrastructure (router, switches, etc.)
- 10.0.1.25-10.0.1.99   : Kubernetes nodes
- 10.0.1.100            : Virtual IP (HAProxy/Keepalived)
- 10.0.1.101-10.0.1.200 : MetalLB IP pool (for LoadBalancer services)
- 10.0.1.201-10.0.1.254 : Reserved for future use
```

### DNS Records

Configure the following DNS records (or /etc/hosts entries):

```
# A Records
10.0.1.100  k8s-api.antbrains.com
10.0.1.100  rancher-uslab.antbrains.com
10.0.1.100  ceph-uslab.antbrains.com

# Wildcard (optional, for ingress)
*.antbrains.com  → 10.0.1.100
```

### Required Ports

**Control Plane Node (fratelli):**
- 6443/TCP: Kubernetes API Server
- 6444/TCP: HAProxy (load balanced API)
- 9345/TCP: RKE2 supervisor API
- 10250/TCP: Kubelet API
- 2379-2380/TCP: etcd client and peer
- 8404/TCP: HAProxy stats page
- 80/TCP, 443/TCP: Ingress traffic

**Worker Nodes:**
- 10250/TCP: Kubelet API
- 30000-32767/TCP: NodePort services
- 80/TCP, 443/TCP: Ingress traffic

**All Nodes:**
- 8472/UDP: Calico VXLAN
- 179/TCP: Calico BGP (if using BGP mode)

---

## Installation Guide

## Phase 1: Server Preparation

**Purpose:** Prepare all servers with the necessary system configurations, kernel parameters, and prerequisites required for running a production Kubernetes cluster. This phase ensures consistency across all nodes and prevents common issues related to system configuration.

**Time Required:** ~30-45 minutes per server (can be parallelized)

---

### Step 1.1: Update System and Install Prerequisites

**Why this step is important:**
- **System Updates:** Ensures all servers have the latest security patches and bug fixes, reducing vulnerabilities
- **Essential Tools:** Provides utilities needed for cluster management, debugging, and monitoring
- **Time Synchronization:** Critical for distributed systems like Kubernetes and etcd, which rely on accurate timestamps for leader election, certificate validation, and log correlation

**What we're installing:**
- `curl/wget`: Download installation scripts and files
- `vim/git`: Configuration file editing and version control
- `htop/net-tools`: System monitoring and network diagnostics
- `chrony/ntp`: Time synchronization (prevents clock drift issues)
- `ca-certificates`: SSL/TLS certificate validation
- `gnupg/lsb-release`: Package verification and OS detection

Execute on **ALL 7 servers** (fratelli, pane, cicis, cesar, papaj, grimaldi, rosso):

```bash
# Update system packages
# This ensures we have the latest security patches and package metadata
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y \
    curl \
    wget \
    vim \
    git \
    htop \
    net-tools \
    ntp \
    chrony \
    ca-certificates \
    gnupg \
    lsb-release

# Verify NTP synchronization
# Time sync is CRITICAL - etcd requires accurate time across all nodes
# Clock skew can cause certificate validation failures and cluster instability
sudo systemctl enable chronyd
sudo systemctl start chronyd
timedatectl status

# Expected output should show "System clock synchronized: yes"
```

**Verification:**
```bash
# Check time synchronization status
timedatectl status | grep "synchronized"
# Should show: System clock synchronized: yes

# Check time difference between nodes (run on each node)
date
# All nodes should show times within 1-2 seconds of each other
```

### Step 1.2: Configure Hostname and DNS Resolution

**Why this step is important:**
- **Unique Hostnames:** Kubernetes uses hostnames to identify nodes; duplicate or generic hostnames cause node registration failures
- **DNS Resolution:** Enables nodes to communicate using friendly names instead of IP addresses
- **/etc/hosts:** Provides local DNS resolution as a fallback, ensuring cluster communication even if external DNS fails
- **Virtual IP Entry:** Pre-configures access to the cluster API through the load-balanced VIP

**What we're configuring:**
- Setting unique, meaningful hostnames for each server
- Creating local DNS entries for all cluster nodes
- Adding the Virtual IP (10.0.1.100) for high-availability API access

Execute on **ALL 7 servers**:

```bash
# Set hostname (change for each server)
# Hostnames must be unique and match the node names in Kubernetes
# Use lowercase, no special characters except hyphens

# On fratelli:
sudo hostnamectl set-hostname fratelli

# On pane:
sudo hostnamectl set-hostname pane

# On cicis:
sudo hostnamectl set-hostname cicis

# On cesar:
sudo hostnamectl set-hostname cesar

# On papaj:
sudo hostnamectl set-hostname papaj

# On grimaldi:
sudo hostnamectl set-hostname grimaldi

# On rosso:
sudo hostnamectl set-hostname rosso

# Verify hostname is set correctly
hostname
hostnamectl

# Add all cluster nodes to /etc/hosts
# This provides DNS resolution even if external DNS is unavailable
# Critical for cluster bootstrapping and recovery scenarios
cat << 'EOF' | sudo tee -a /etc/hosts
# RKE2 Cluster Nodes
10.0.1.30  fratelli
10.0.1.25  pane
10.0.1.35  cicis
10.0.1.40  cesar
10.0.1.45  papaj
10.0.1.50  grimaldi
10.0.1.60  rosso

# Virtual IP for Load Balancer
# This VIP will be managed by Keepalived on fratelli
10.0.1.100 k8s-api.antbrains.com rancher-uslab.antbrains.com
EOF

# Verify hostname resolution
ping -c 2 fratelli
ping -c 2 k8s-api.antbrains.com

# Test resolution of all nodes
for node in fratelli pane cicis cesar papaj grimaldi rosso; do
    echo "Testing $node..."
    ping -c 1 $node > /dev/null && echo "✓ $node reachable" || echo "✗ $node FAILED"
done
```

**Verification:**
```bash
# Verify hostname is set
hostname
# Should output the correct hostname for this server

# Verify /etc/hosts entries
cat /etc/hosts | grep -E "fratelli|pane|cicis|cesar|papaj|grimaldi|rosso|k8s-api"

# Verify DNS resolution works
nslookup fratelli
nslookup k8s-api.antbrains.com
```

### Step 1.3: Disable Swap and Configure Kernel Modules

**Why this step is important:**
- **Disable Swap:** Kubernetes requires swap to be disabled because:
  - Swap can cause unpredictable performance degradation
  - Makes memory limits unreliable (pods could use swap instead of being evicted)
  - Kubelet will refuse to start if swap is enabled
- **Kernel Modules:** Required for container networking and load balancing:
  - `overlay`: Enables OverlayFS for efficient container image layers
  - `br_netfilter`: Allows iptables to see bridged traffic (required for pod networking)
  - `ip_vs*`: IPVS modules for efficient service load balancing (better than iptables for large clusters)
  - `nf_conntrack`: Connection tracking for network address translation

**What we're configuring:**
- Permanently disabling swap memory
- Loading kernel modules required by Kubernetes and container runtime
- Ensuring modules load automatically on boot

Execute on **ALL 7 servers**:

```bash
# Disable swap permanently
# Kubernetes scheduler assumes memory limits are enforced; swap breaks this assumption
sudo swapoff -a

# Comment out swap entries in /etc/fstab to prevent swap from re-enabling on reboot
sudo sed -i '/ swap / s/^/#/' /etc/fstab

# Verify swap is disabled (Swap line should show 0)
free -h

# Load required kernel modules
# These modules are essential for container networking and Kubernetes services
cat << 'EOF' | sudo tee /etc/modules-load.d/k8s.conf
# Container filesystem support
overlay

# Bridge netfilter - allows iptables to process bridged traffic
# Required for pod-to-pod networking and network policies
br_netfilter

# IPVS modules for efficient service load balancing
# IPVS is more performant than iptables for large numbers of services
ip_vs
ip_vs_rr    # Round-robin scheduling
ip_vs_wrr   # Weighted round-robin
ip_vs_sh    # Source hashing

# Connection tracking for NAT
nf_conntrack
EOF

# Load modules immediately (without waiting for reboot)
sudo modprobe overlay
sudo modprobe br_netfilter
sudo modprobe ip_vs
sudo modprobe ip_vs_rr
sudo modprobe ip_vs_wrr
sudo modprobe ip_vs_sh
sudo modprobe nf_conntrack

# Verify modules are loaded
lsmod | grep -E 'overlay|br_netfilter|ip_vs'

# Verify modules will load on boot
cat /etc/modules-load.d/k8s.conf
```

**Verification:**
```bash
# Verify swap is disabled (should show 0 for Swap)
free -h

# Verify all required modules are loaded
for module in overlay br_netfilter ip_vs ip_vs_rr ip_vs_wrr ip_vs_sh nf_conntrack; do
    if lsmod | grep -q "^$module"; then
        echo "✓ $module loaded"
    else
        echo "✗ $module NOT loaded"
    fi
done
```

### Step 1.4: Configure Kernel Parameters

**Why this step is important:**
- **IP Forwarding:** Enables the kernel to route packets between containers and external networks
- **Bridge Netfilter:** Allows iptables rules to apply to bridged network traffic (essential for Kubernetes networking)
- **Connection Tracking:** Prevents connection table exhaustion in high-traffic environments
- **File Limits:** Kubernetes and containers can open many files simultaneously; default limits are too low
- **Network Tuning:** Optimizes network stack for high-throughput, low-latency container networking

**What each parameter does:**
- `net.ipv4.ip_forward`: Allows packets to be forwarded between network interfaces (required for pod networking)
- `net.bridge.bridge-nf-call-iptables`: Enables iptables processing of bridged traffic (required for NetworkPolicies)
- `net.netfilter.nf_conntrack_max`: Maximum number of tracked connections (prevents "table full" errors)
- `fs.inotify.max_user_watches`: Number of files that can be watched (kubelet watches many files)
- `fs.file-max`: System-wide file descriptor limit
- `vm.max_map_count`: Maximum memory map areas (required by Elasticsearch and other apps)
- `net.core.somaxconn`: Maximum queued connections (improves service responsiveness under load)

Execute on **ALL 7 servers**:

```bash
# Configure sysctl parameters for Kubernetes
cat << 'EOF' | sudo tee /etc/sysctl.d/99-kubernetes.conf
# Enable IP forwarding - REQUIRED for pod networking
# Without this, pods cannot communicate across nodes
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1

# Bridge netfilter - REQUIRED for Kubernetes networking
# Allows iptables to see and process traffic on bridge interfaces
# This is essential for Services, NetworkPolicies, and pod-to-pod communication
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1

# Disable IPv6 (optional, if not using IPv6)
# Uncomment if you want to disable IPv6 entirely
# net.ipv6.conf.all.disable_ipv6 = 1
# net.ipv6.conf.default.disable_ipv6 = 1

# Increase connection tracking limits
# Default is too low for Kubernetes clusters with many services
# Prevents "nf_conntrack: table full, dropping packet" errors
net.netfilter.nf_conntrack_max = 1000000
net.netfilter.nf_conntrack_tcp_timeout_established = 86400

# Kernel tuning for container workloads
# inotify: kubelet watches many files (pods, configs, secrets)
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches = 524288

# file-max: Total number of file handles the kernel can allocate
# Containers can open many files simultaneously
fs.file-max = 2097152

# max_map_count: Required by Elasticsearch, Ceph, and other memory-intensive apps
vm.max_map_count = 262144

# Network performance tuning
# somaxconn: Maximum number of queued connections
# Improves performance under high connection load
net.core.somaxconn = 32768

# tcp_max_syn_backlog: Maximum number of SYN packets to queue
# Prevents SYN flood attacks and improves connection handling
net.ipv4.tcp_max_syn_backlog = 8096

# netdev_max_backlog: Maximum packets in receive queue
# Prevents packet drops under high network load
net.core.netdev_max_backlog = 16384
EOF

# Apply sysctl parameters immediately
sudo sysctl --system

# Verify critical parameters are set correctly
echo "Verifying critical kernel parameters..."
sysctl net.ipv4.ip_forward                    # Should be 1
sysctl net.bridge.bridge-nf-call-iptables     # Should be 1
sysctl net.netfilter.nf_conntrack_max         # Should be 1000000
sysctl fs.file-max                            # Should be 2097152
```

**Verification:**
```bash
# Verify all parameters are applied
sudo sysctl -a | grep -E "ip_forward|bridge-nf-call|nf_conntrack_max|inotify|file-max|max_map_count"

# Check if parameters will persist after reboot
cat /etc/sysctl.d/99-kubernetes.conf
```

### Step 1.5: Configure System Limits

Execute on **ALL 7 servers**:

```bash
# Set ulimits for Kubernetes
cat << 'EOF' | sudo tee /etc/security/limits.d/kubernetes.conf
* soft nofile 1048576
* hard nofile 1048576
* soft nproc 1048576
* hard nproc 1048576
* soft memlock unlimited
* hard memlock unlimited
EOF

# Configure systemd limits for containerd
sudo mkdir -p /etc/systemd/system/containerd.service.d
cat << 'EOF' | sudo tee /etc/systemd/system/containerd.service.d/limits.conf
[Service]
LimitNOFILE=1048576
LimitNPROC=1048576
LimitMEMLOCK=infinity
EOF

sudo systemctl daemon-reload
```

### Step 1.6: Configure Firewall (Optional)

If using UFW firewall, configure it on **ALL 7 servers**:

```bash
# Install UFW if not already installed
sudo apt install -y ufw

# Allow SSH (IMPORTANT: Do this first!)
sudo ufw allow 22/tcp

# On fratelli (control plane):
sudo ufw allow 6443/tcp   # Kubernetes API
sudo ufw allow 6444/tcp   # HAProxy
sudo ufw allow 9345/tcp   # RKE2 supervisor
sudo ufw allow 10250/tcp  # Kubelet
sudo ufw allow 2379:2380/tcp  # etcd
sudo ufw allow 8404/tcp   # HAProxy stats
sudo ufw allow 80/tcp     # HTTP
sudo ufw allow 443/tcp    # HTTPS
sudo ufw allow 8472/udp   # Calico VXLAN

# On all worker nodes:
sudo ufw allow 10250/tcp  # Kubelet
sudo ufw allow 30000:32767/tcp  # NodePort range
sudo ufw allow 80/tcp     # HTTP
sudo ufw allow 443/tcp    # HTTPS
sudo ufw allow 8472/udp   # Calico VXLAN

# Enable firewall
sudo ufw --force enable
sudo ufw status verbose
```

**Note:** For production environments, consider using more restrictive firewall rules with specific source IPs.

---

## Phase 2: Load Balancer Setup

**Purpose:** Configure high availability for the Kubernetes API server using HAProxy for load balancing and Keepalived for Virtual IP (VIP) management. This ensures the cluster remains accessible even if the control plane node experiences issues.

**Why we need this:**
- **High Availability:** Provides a single, stable endpoint (VIP) for accessing the Kubernetes API
- **Load Balancing:** HAProxy distributes API requests (in multi-master setups, we're prepared for future expansion)
- **Automatic Failover:** Keepalived manages the VIP and can move it to another node if needed
- **Ingress Traffic:** HAProxy also load balances HTTP/HTTPS traffic across all worker nodes

**Architecture:**
```
External Clients → VIP (10.0.1.100) → HAProxy (fratelli) → Kubernetes API (fratelli:6443)
                                                          → Worker Nodes (80/443)
```

**Time Required:** ~15-20 minutes

---

### Step 2.1: Install HAProxy and Keepalived

**Why HAProxy:**
- Industry-standard, high-performance TCP/HTTP load balancer
- Supports health checks to detect failed backends
- Provides statistics and monitoring interface
- Minimal resource overhead

**Why Keepalived:**
- Implements VRRP (Virtual Router Redundancy Protocol) for VIP management
- Automatically detects service failures and moves VIP to healthy nodes
- Lightweight and battle-tested in production environments

Execute on **fratelli** (control plane node only):

```bash
# Install load balancer components
sudo apt install -y haproxy keepalived

# Verify installation and check versions
haproxy -v
keepalived -v

# Check if services are installed correctly
systemctl status haproxy --no-pager
systemctl status keepalived --no-pager
```

### Step 2.2: Configure HAProxy

**What this configuration does:**
- **Frontend k8s-api-frontend (port 6444):** Accepts Kubernetes API requests and forwards to the API server
- **Backend k8s-api-backend:** Points to fratelli:6443 (can be expanded to multiple masters)
- **Frontend http-frontend (port 80):** Accepts HTTP traffic and distributes to all worker nodes
- **Frontend https-frontend (port 443):** Accepts HTTPS traffic and distributes to all worker nodes
- **Stats Interface (port 8404):** Provides monitoring dashboard for HAProxy health

**Why these ports:**
- Port 6444: External API access (HAProxy listens here, forwards to 6443)
- Port 6443: Actual Kubernetes API server port
- Port 80/443: Standard HTTP/HTTPS for ingress traffic
- Port 8404: HAProxy statistics and monitoring

**Load Balancing Strategy:**
- **Round-robin:** Distributes requests evenly across all healthy backends
- **Health checks:** Automatically removes failed nodes from rotation
- **Fall/Rise thresholds:** Requires 3 failures to mark down, 2 successes to mark up (prevents flapping)

Execute on **fratelli**:

```bash
# Backup original configuration (always backup before modifying)
sudo cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.backup

# Create new HAProxy configuration
cat << 'EOF' | sudo tee /etc/haproxy/haproxy.cfg
#---------------------------------------------------------------------
# Global settings
#---------------------------------------------------------------------
global
    log stdout local0
    chroot /var/lib/haproxy
    stats socket /run/haproxy/admin.sock mode 660 level admin
    stats timeout 30s
    user haproxy
    group haproxy
    daemon
    maxconn 4000

#---------------------------------------------------------------------
# Default settings
#---------------------------------------------------------------------
defaults
    mode tcp
    log global
    option tcplog
    option dontlognull
    timeout connect 10s
    timeout client 1m
    timeout server 1m
    retries 3

#---------------------------------------------------------------------
# Kubernetes API Server Load Balancer
#---------------------------------------------------------------------
frontend k8s-api-frontend
    bind *:6444
    mode tcp
    option tcplog
    default_backend k8s-api-backend

backend k8s-api-backend
    mode tcp
    option tcp-check
    balance roundrobin
    # Control plane node (single master)
    server fratelli 10.0.1.30:6443 check fall 3 rise 2

#---------------------------------------------------------------------
# HTTP Ingress Load Balancer (All worker nodes)
#---------------------------------------------------------------------
frontend http-frontend
    bind *:80
    mode tcp
    option tcplog
    default_backend http-backend

backend http-backend
    mode tcp
    balance roundrobin
    # Worker nodes
    server pane     10.0.1.25:80 check fall 3 rise 2
    server cicis    10.0.1.35:80 check fall 3 rise 2
    server cesar    10.0.1.40:80 check fall 3 rise 2
    server papaj    10.0.1.45:80 check fall 3 rise 2
    server grimaldi 10.0.1.50:80 check fall 3 rise 2
    server rosso    10.0.1.60:80 check fall 3 rise 2

#---------------------------------------------------------------------
# HTTPS Ingress Load Balancer (All worker nodes)
#---------------------------------------------------------------------
frontend https-frontend
    bind *:443
    mode tcp
    option tcplog
    default_backend https-backend

backend https-backend
    mode tcp
    balance roundrobin
    # Worker nodes
    server pane     10.0.1.25:443 check fall 3 rise 2
    server cicis    10.0.1.35:443 check fall 3 rise 2
    server cesar    10.0.1.40:443 check fall 3 rise 2
    server papaj    10.0.1.45:443 check fall 3 rise 2
    server grimaldi 10.0.1.50:443 check fall 3 rise 2
    server rosso    10.0.1.60:443 check fall 3 rise 2

#---------------------------------------------------------------------
# HAProxy Statistics Page
#---------------------------------------------------------------------
listen stats
    bind *:8404
    mode http
    stats enable
    stats uri /stats
    stats refresh 30s
    stats admin if TRUE
    stats auth admin:admin123
EOF

# Validate HAProxy configuration
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
```

### Step 2.3: Configure Keepalived

**What Keepalived does:**
- **VRRP (Virtual Router Redundancy Protocol):** Manages the Virtual IP (10.0.1.100)
- **Health Monitoring:** Continuously checks if HAProxy is running and healthy
- **Automatic Failover:** If HAProxy fails, Keepalived can move the VIP to another node (in multi-master setups)
- **Priority-based Election:** Highest priority node owns the VIP (fratelli has priority 110)

**Configuration explained:**
- **vrrp_script chk_haproxy:** Health check script that curls HAProxy stats page every 2 seconds
- **state MASTER:** This node is the primary VIP holder
- **virtual_router_id 51:** Unique ID for this VRRP instance (must be same across all nodes in VRRP group)
- **priority 110:** Higher priority = preferred VIP holder
- **authentication:** Prevents rogue VRRP advertisements on the network
- **virtual_ipaddress:** The VIP that will be assigned to this node

**Important:** You must identify your network interface name (e.g., eth0, ens18, ens192) before proceeding.

Execute on **fratelli**:

```bash
# Determine your network interface name
# Look for the interface with IP 10.0.1.30
ip addr show

# Common interface names:
# - ens18 (VMware/Proxmox)
# - eth0 (older systems)
# - ens192 (VMware)
# - enp0s3 (VirtualBox)

# Configure Keepalived (adjust interface name if needed)
cat << 'EOF' | sudo tee /etc/keepalived/keepalived.conf
# Keepalived configuration for single master setup
vrrp_script chk_haproxy {
    script "/bin/curl -f http://localhost:8404/stats || exit 1"
    interval 2
    weight -2
    fall 3
    rise 2
}

vrrp_instance VI_1 {
    state MASTER
    interface ens18  # Change to your actual network interface (e.g., eth0, ens18, etc.)
    virtual_router_id 51
    priority 110
    advert_int 1

    authentication {
        auth_type PASS
        auth_pass krista123
    }

    virtual_ipaddress {
        10.0.1.100/24
    }

    track_script {
        chk_haproxy
    }
}
EOF

# Validate Keepalived configuration
sudo keepalived -t -f /etc/keepalived/keepalived.conf
```

### Step 2.4: Start and Enable Services

Execute on **fratelli**:

```bash
# Enable services to start on boot
sudo systemctl enable haproxy keepalived

# Start services
sudo systemctl start haproxy keepalived

# Check service status
sudo systemctl status haproxy
sudo systemctl status keepalived

# Verify VIP assignment (should show 10.0.1.100 on fratelli)
ip addr show | grep 10.0.1.100

# Test HAProxy stats page
curl http://localhost:8404/stats

# Test VIP connectivity
ping -c 3 10.0.1.100

# View HAProxy logs
sudo journalctl -u haproxy -f
```

---

## Phase 3: RKE2 Control Plane Installation

**Purpose:** Install and configure the Kubernetes control plane using RKE2, which includes the API server, scheduler, controller manager, and etcd database.

**Why RKE2:**
- **Production-grade:** Designed for security and compliance (FIPS 140-2, CIS hardening)
- **Simplified Operations:** Combines Kubernetes components into a single binary
- **Built-in Security:** SELinux policies, Pod Security Standards, and secure defaults
- **Embedded etcd:** No need to manage etcd separately
- **Automatic Certificate Management:** Handles certificate rotation automatically

**What gets installed:**
- Kubernetes API Server (port 6443)
- etcd (distributed key-value store for cluster state)
- Controller Manager (manages cluster state)
- Scheduler (assigns pods to nodes)
- Kubelet (node agent)
- Container runtime (containerd)
- CNI (Calico for pod networking)

**Time Required:** ~5-10 minutes

---

### Step 3.1: Install RKE2 Server on Control Plane

**Configuration explained:**
- **tls-san:** Additional names/IPs for API server certificate (allows access via VIP and domain names)
- **cluster-cidr:** IP range for pod network (10.48.0.0/16 = 65,536 IPs)
- **service-cidr:** IP range for services (10.49.0.0/16 = 65,536 IPs)
- **cluster-init:** Initializes a new cluster (only used on first control plane node)
- **disable rke2-canal:** We're using Calico instead of the default Canal CNI
- **kubelet-arg:** Configures kubelet behavior (pod limits, resource reservations, eviction thresholds)

**Why these settings:**
- **max-pods=200:** Limits pods per node (prevents resource exhaustion)
- **system-reserved:** Reserves CPU/memory for OS and system daemons
- **kube-reserved:** Reserves resources for Kubernetes components
- **eviction-hard:** Triggers pod eviction when resources are critically low

Execute on **fratelli**:

```bash
# Create RKE2 configuration directory
sudo mkdir -p /etc/rancher/rke2

# Create RKE2 server configuration
cat << 'EOF' | sudo tee /etc/rancher/rke2/config.yaml
# RKE2 Server Configuration
tls-san:
  - rancher-uslab.antbrains.com
  - k8s-api.antbrains.com
  - 10.0.1.100
  - 10.0.1.30
  - fratelli

# Custom cluster name
cluster-name: "uslab-cluster"

# Cluster configuration
cluster-cidr: 10.48.0.0/16
service-cidr: 10.49.0.0/16
cluster-dns: 10.49.0.10
cluster-domain: cluster.local

# For first node initialization
cluster-init: true

# Kubeconfig permissions
write-kubeconfig-mode: "0644"

# Disable default CNI (we'll use Calico)
disable:
  - rke2-canal

# CNI configuration
cni:
  - calico

# Kubelet configuration
kubelet-arg:
  - "max-pods=200"
  - "system-reserved=cpu=2,memory=8Gi"
  - "kube-reserved=cpu=1,memory=4Gi"
  - "eviction-hard=memory.available<8%,nodefs.available<10%"

# Node labels
node-label:
  - "node.antbrains.com/role=control-plane"
  - "node.antbrains.com/cpu=40"
  - "node.antbrains.com/memory=128"
EOF

# Download and install RKE2
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.1+rke2r1" sh -

# Enable and start RKE2 server
sudo systemctl enable rke2-server.service
sudo systemctl start rke2-server.service

# Monitor startup logs (this may take 2-5 minutes)
sudo journalctl -u rke2-server -f
```

### Step 3.2: Configure kubectl Access

Execute on **fratelli**:

```bash
# Create kubeconfig directory
mkdir -p ~/.kube

# Copy kubeconfig
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

# Add kubectl to PATH
echo 'export PATH=$PATH:/var/lib/rancher/rke2/bin' >> ~/.bashrc
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
source ~/.bashrc

# Verify cluster access
kubectl get nodes
kubectl get pods -A

# Expected output: fratelli node in Ready state
```

### Step 3.3: Get Node Token for Workers

**What the node token is:**
- A secure authentication token that allows worker nodes to join the cluster
- Contains cluster identification and authentication credentials
- Must be kept secure (treat it like a password)

**Why we need it:**
- Worker nodes use this token to authenticate with the control plane
- Prevents unauthorized nodes from joining the cluster
- Links the worker to the correct cluster (important in multi-cluster environments)

Execute on **fratelli**:

```bash
# Get the node token (save this for worker node setup)
# This token is automatically generated during RKE2 server installation
sudo cat /var/lib/rancher/rke2/server/node-token

# Example output:
# K104f350f244e8ea060aef887d5b2b8b9432d7c93cc3d9ccd71d79440dac2234696::server:5ef6ffae8e77b5563b878f272a0e33eb
```

**IMPORTANT:**
- Save this token securely in a password manager or secure note
- You'll need it to join all worker nodes to the cluster
- If compromised, regenerate it by restarting the RKE2 server
- The token does not expire but can be rotated if needed

---

## Phase 4: Worker Node Addition

**Purpose:** Add worker nodes to the cluster to provide compute capacity for running application workloads. Worker nodes run the actual application pods while the control plane manages the cluster.

**Why separate worker nodes:**
- **Workload Isolation:** Keeps application workloads separate from control plane components
- **Scalability:** Can add/remove workers without affecting cluster management
- **Resource Optimization:** Control plane can focus on cluster management, workers focus on applications
- **High Availability:** Multiple workers provide redundancy for applications

**What gets installed on workers:**
- **RKE2 Agent:** Lightweight agent that communicates with control plane
- **Kubelet:** Manages pods and containers on the node
- **Container Runtime (containerd):** Runs containers
- **kube-proxy:** Manages network rules for services
- **CNI (Calico):** Provides pod networking

**Node Labels:**
We're adding custom labels to enable intelligent workload scheduling:
- `node.antbrains.com/capacity`: standard or high (for resource-intensive workloads)
- `node.antbrains.com/cpu`: Number of CPU cores
- `node.antbrains.com/memory`: RAM in GB
- `node.antbrains.com/storage`: Storage capacity tier

**Time Required:** ~5-10 minutes per worker node (can be parallelized)

---

### Step 4.1: Standard Worker Nodes (pane, cicis, cesar, papaj, rosso)

**Configuration explained:**
- **server:** Control plane endpoint (fratelli:9345) - port 9345 is RKE2's registration port
- **token:** Authentication token from Step 3.3
- **node-name:** Unique identifier for this node (must match hostname)
- **max-pods=200:** Limits pods per node (prevents resource exhaustion)
- **system-reserved:** CPU/memory reserved for OS and system daemons (SSH, monitoring, etc.)
- **kube-reserved:** Resources reserved for Kubernetes components (kubelet, kube-proxy, etc.)
- **eviction-hard:** Triggers pod eviction when resources are critically low (prevents node crashes)

**Why these resource reservations:**
- **system-reserved (2 CPU, 8GB):** Ensures OS remains responsive under load
- **kube-reserved (1 CPU, 4GB):** Ensures Kubernetes components function properly
- **Remaining resources:** Available for application pods (~37 CPU, ~116GB RAM)

Execute on **pane, cicis, cesar, papaj, and rosso**:

```bash
# Create RKE2 agent configuration directory
sudo mkdir -p /etc/rancher/rke2

# Create worker node configuration
# IMPORTANT: Replace <YOUR_NODE_TOKEN> with the actual token from fratelli
# IMPORTANT: Replace <NODE_NAME> with the actual hostname

cat << 'EOF' | sudo tee /etc/rancher/rke2/config.yaml
# RKE2 Worker Node Configuration
server: https://10.0.1.30:9345
token: <YOUR_NODE_TOKEN>

# Node identification
node-name: <NODE_NAME>

# Kubelet configurations for standard workers (40 cores, 128GB RAM)
kubelet-arg:
  - "max-pods=200"
  - "system-reserved=cpu=2,memory=8Gi"
  - "kube-reserved=cpu=1,memory=4Gi"
  - "eviction-hard=memory.available<8%,nodefs.available<10%"

# Node labels for workload scheduling
node-label:
  - "node.antbrains.com/capacity=standard"
  - "node.antbrains.com/cpu=40"
  - "node.antbrains.com/memory=128"
EOF

# Install RKE2 agent
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.1+rke2r1" INSTALL_RKE2_TYPE="agent" sh -

# Enable and start RKE2 agent
sudo systemctl enable rke2-agent.service
sudo systemctl start rke2-agent.service

# Monitor startup logs
sudo journalctl -u rke2-agent -f
```

### Step 4.2: High-Capacity Worker Node (grimaldi)

**Why grimaldi is configured differently:**
- **More Resources:** 80 cores and 640GB RAM (vs. 40 cores and 128GB on standard workers)
- **Higher Pod Limit:** Can run 250 pods (vs. 200 on standard workers)
- **Larger Reservations:** More resources reserved for system and Kubernetes (proportional to total capacity)
- **Special Labels:** Marked as "high-capacity" for scheduling resource-intensive workloads

**Use cases for grimaldi:**
- Large databases (PostgreSQL, MongoDB, Elasticsearch)
- Big data processing (Spark, Hadoop)
- Machine learning workloads
- Memory-intensive applications
- High-concurrency services

**Resource allocation on grimaldi:**
- **Total:** 80 CPU, 640GB RAM
- **System Reserved:** 4 CPU, 16GB RAM (OS, SSH, monitoring)
- **Kube Reserved:** 2 CPU, 8GB RAM (kubelet, kube-proxy, CNI)
- **Available for Pods:** ~74 CPU, ~616GB RAM

Execute on **grimaldi**:

```bash
# Create RKE2 agent configuration directory
sudo mkdir -p /etc/rancher/rke2

# Create worker node configuration for high-capacity server
# IMPORTANT: Replace <YOUR_NODE_TOKEN> with the actual token from fratelli

cat << 'EOF' | sudo tee /etc/rancher/rke2/config.yaml
# RKE2 Worker Node Configuration - High Capacity
server: https://10.0.1.30:9345
token: <YOUR_NODE_TOKEN>

# Node identification
node-name: grimaldi

# Kubelet configurations for high-capacity worker (80 cores, 640GB RAM)
kubelet-arg:
  - "max-pods=250"                                          # Higher pod limit for large workloads
  - "system-reserved=cpu=4,memory=16Gi"                     # More resources for OS (proportional)
  - "kube-reserved=cpu=2,memory=8Gi"                        # More resources for K8s components
  - "eviction-hard=memory.available<5%,nodefs.available<10%" # Lower % threshold (more absolute memory)

# Node labels for workload scheduling
# These labels allow you to target grimaldi specifically for resource-intensive workloads
node-label:
  - "node.antbrains.com/capacity=high"      # Identifies as high-capacity node
  - "node.antbrains.com/cpu=80"             # CPU count for scheduling decisions
  - "node.antbrains.com/memory=640"         # Memory in GB
  - "node.antbrains.com/storage=large"      # Large storage capacity
EOF

# Install RKE2 agent
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.1+rke2r1" INSTALL_RKE2_TYPE="agent" sh -

# Enable and start RKE2 agent
sudo systemctl enable rke2-agent.service
sudo systemctl start rke2-agent.service

# Monitor startup logs
sudo journalctl -u rke2-agent -f
```

### Step 4.3: Verify Cluster

**What we're verifying:**
- All 7 nodes are in "Ready" state
- Nodes have correct roles assigned
- Custom labels are applied correctly
- All system pods are running (Calico, kube-proxy, etc.)
- Cluster networking is functional

**Understanding node status:**
- **Ready:** Node is healthy and ready to accept pods
- **NotReady:** Node has issues (network, kubelet, resources)
- **SchedulingDisabled:** Node is cordoned (maintenance mode)

**What "Ready" means:**
- Kubelet is running and communicating with control plane
- Container runtime is functional
- Sufficient resources available
- Network connectivity established

From **fratelli**, verify all nodes have joined:

```bash
# Check all nodes with detailed information
kubectl get nodes -o wide

# Expected output: 7 nodes (1 control plane + 6 workers)
# All should show STATUS=Ready
# NAME       STATUS   ROLES                       AGE   VERSION           INTERNAL-IP   OS-IMAGE
# fratelli   Ready    control-plane,etcd,master   Xd    v1.33.1+rke2r1    10.0.1.30     Ubuntu 24.04.2 LTS
# pane       Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.25     Ubuntu 24.04.3 LTS
# cicis      Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.35     Ubuntu 24.04.3 LTS
# cesar      Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.40     Ubuntu 24.04.2 LTS
# papaj      Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.45     Ubuntu 24.04.2 LTS
# grimaldi   Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.50     Ubuntu 24.04.2 LTS
# rosso      Ready    <none>                      Xd    v1.33.1+rke2r1    10.0.1.60     Ubuntu 22.04.5 LTS

# Check node labels (verify custom labels are applied)
kubectl get nodes --show-labels | grep "node.antbrains.com"
# Should show capacity, cpu, memory labels for each node

# Check specific node details
kubectl describe node grimaldi | grep -A 10 "Labels:"
# Should show capacity=high, cpu=80, memory=640

# Check all system pods are running
kubectl get pods -A
# All pods should be in Running or Completed state

# Check cluster info
kubectl cluster-info
# Should show Kubernetes control plane and CoreDNS endpoints

# Check Calico networking pods (should be one per node)
kubectl get pods -n calico-system
# Expected: calico-node-* pods (7 total, one per node)
# Expected: calico-kube-controllers-* pod

# Verify pod networking is working
kubectl run test-pod --image=nginx --rm -it --restart=Never -- curl -I https://google.com
# Should successfully connect (tests DNS and external connectivity)
```

**Verification checklist:**
```bash
# Count nodes (should be 7)
kubectl get nodes --no-headers | wc -l

# Check for any NotReady nodes (should be empty)
kubectl get nodes | grep NotReady

# Verify all system namespaces have running pods
kubectl get pods -n kube-system
kubectl get pods -n calico-system

# Check node resource capacity
kubectl top nodes
# Shows CPU and memory usage for each node
```

---

## Phase 5: Storage Configuration (Rook-Ceph)

**Purpose:** Deploy Rook-Ceph to provide persistent storage for Kubernetes workloads. Ceph is a distributed storage system that provides block, file, and object storage.

**Why Rook-Ceph:**
- **Cloud-Native Storage:** Designed specifically for Kubernetes
- **Multiple Storage Types:** Block (RBD), File (CephFS), and Object (S3-compatible)
- **Self-Healing:** Automatically recovers from disk and node failures
- **Scalable:** Can grow storage capacity by adding more nodes/disks
- **No Single Point of Failure:** Data is distributed across multiple nodes

**What we're deploying:**
1. **Rook Operator:** Manages Ceph cluster lifecycle
2. **Ceph Cluster:** Distributed storage system
3. **Storage Classes:** Pre-configured storage types for different workloads

**Storage Architecture:**
```
Applications → PVC → Storage Class → Ceph Pool → OSDs (distributed across worker nodes)
```

**Time Required:** ~15-20 minutes

---

### Step 5.1: Install Rook-Ceph Operator

**What the Rook Operator does:**
- Automates Ceph cluster deployment and management
- Monitors Ceph health and performs automatic recovery
- Manages Ceph upgrades and scaling operations
- Creates and manages storage classes

**Why we use Helm:**
- Simplifies installation and upgrades
- Manages all Kubernetes resources as a single unit
- Allows easy customization through values files

Execute on **fratelli** (or your local machine with kubectl access):

```bash
# Add Rook Helm repository
helm repo add rook-release https://charts.rook.io/release
helm repo update

# Create rook-ceph namespace
# This namespace will contain all Rook and Ceph components
kubectl create namespace rook-ceph

# Install Rook-Ceph operator
# The operator will watch for CephCluster resources and manage them
helm install rook-ceph rook-release/rook-ceph \
  --namespace rook-ceph \
  --version v1.15.8 \
  --set crds.enabled=true

# Wait for operator to be ready (this may take 2-3 minutes)
kubectl -n rook-ceph get pods -w

# Expected output: rook-ceph-operator pod in Running state
# Press Ctrl+C once the operator is Running

# Verify operator is healthy
kubectl -n rook-ceph logs -l app=rook-ceph-operator --tail=50
```

### Step 5.2: Deploy Ceph Cluster

**What this step does:**
- Deploys the actual Ceph storage cluster across all worker nodes
- Creates Ceph monitors (MONs) for cluster coordination
- Creates Object Storage Daemons (OSDs) on each node to store data
- Deploys Ceph manager (MGR) for monitoring and management
- Optionally deploys a toolbox pod for debugging

**Configuration from cluster-values.yaml:**
- **Ceph Version:** v19.2.2 (Squid) - Latest stable release
- **Pool Replication:** Size 1 (single replica) - Suitable for lab/dev, use 3 for production
- **Toolbox Enabled:** Provides CLI access to Ceph commands
- **Monitoring:** Disabled (can be enabled for production)

**Why single replica (size=1):**
- Saves storage space (no data duplication)
- Acceptable for non-production environments
- **WARNING:** For production, use size=3 for data redundancy

**Ceph Components:**
- **MON (Monitor):** Maintains cluster map and state (typically 3 for HA)
- **OSD (Object Storage Daemon):** Stores actual data on disks
- **MGR (Manager):** Provides monitoring, dashboard, and metrics
- **MDS (Metadata Server):** Required only for CephFS (file storage)

Execute on **fratelli**:

```bash
# Download the cluster values file (or use the one in krista_private_cloud/Rack2/ceph-cluster/)
cd /path/to/krista_private_cloud/Rack2/ceph-cluster/

# Review cluster-values.yaml
# Pay attention to: ceph version, pool size, storage class definitions
cat cluster-values.yaml

# Install Ceph cluster
# This will deploy Ceph components across all worker nodes
helm install rook-ceph-cluster rook-release/rook-ceph-cluster \
  --namespace rook-ceph \
  --version v1.15.8 \
  --values cluster-values.yaml

# Monitor Ceph cluster deployment (this may take 5-10 minutes)
# The cluster needs to:
# 1. Deploy monitors and establish quorum
# 2. Deploy OSDs and discover available storage
# 3. Create storage pools
# 4. Deploy manager and dashboard
kubectl -n rook-ceph get pods -w

# Check Ceph cluster status
kubectl -n rook-ceph get cephcluster
```

### Step 5.3: Verify Ceph Cluster

**What we're verifying:**
- Ceph cluster health status (should be HEALTH_OK or HEALTH_WARN initially)
- All Ceph daemons are running (MONs, MGRs, OSDs)
- Storage pools are created
- Storage classes are available for use

**Understanding Ceph health states:**
- **HEALTH_OK:** Everything is working perfectly
- **HEALTH_WARN:** Minor issues (e.g., OSDs still starting, pools not at full replication)
- **HEALTH_ERR:** Critical issues requiring immediate attention

**Using the Ceph toolbox:**
The toolbox pod provides direct access to Ceph CLI commands for debugging and management.

Execute on **fratelli**:

```bash
# Check Ceph cluster health
# This shows overall cluster status, services, and data distribution
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status

# Expected output:
#   cluster:
#     id:     <cluster-id>
#     health: HEALTH_OK (or HEALTH_WARN if still initializing)
#
#   services:
#     mon: 1 daemons (or 3 for HA)
#     mgr: 2 daemons (active + standby)
#     osd: X osds: X up, X in
#
#   data:
#     pools:   X pools, X pgs
#     objects: X objects, X B
#     usage:   X GiB used, X GiB / X GiB avail

# Check Ceph OSD status
# Shows which OSDs are running on which nodes
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd status

# Check storage usage and pool information
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph df

# Check storage classes
# These are the Kubernetes storage classes that applications will use
kubectl get storageclass

# Expected output:
# NAME                     PROVISIONER                     RECLAIMPOLICY   VOLUMEBINDINGMODE
# ceph-block (default)     rook-ceph.rbd.csi.ceph.com      Delete          Immediate
# ceph-filesystem          rook-ceph.cephfs.csi.ceph.com   Delete          Immediate
# ceph-bucket              rook-ceph.ceph.rook.io/bucket   Delete          Immediate
```

### Step 5.4: Access Ceph Dashboard

**What the Ceph Dashboard provides:**
- **Visual Monitoring:** Real-time cluster health, performance metrics, and capacity usage
- **OSD Management:** View OSD status, performance, and disk usage
- **Pool Management:** Monitor and manage storage pools
- **Performance Graphs:** IOPS, throughput, and latency metrics
- **Cluster Topology:** Visual representation of cluster layout
- **Alerts:** Warnings and errors for cluster issues

**Two ways to access the dashboard:**

**Method 1: Port Forwarding (for testing/debugging)**
```bash
# Get Ceph dashboard password
# The password is stored as a base64-encoded Kubernetes secret
kubectl -n rook-ceph get secret rook-ceph-dashboard-password \
  -o jsonpath="{['data']['password']}" | base64 --decode && echo

# Port forward to access dashboard locally
# This creates a secure tunnel from your local machine to the dashboard
kubectl -n rook-ceph port-forward svc/rook-ceph-mgr-dashboard 8443:8443

# Access dashboard at: https://localhost:8443
# Username: admin
# Password: <password from above>

# Note: You'll get a certificate warning (self-signed cert) - this is normal
# Click "Advanced" and "Proceed" to continue
```

**Method 2: Production Access via Ingress (Recommended)**

The Ceph dashboard is already exposed via ingress at: **`https://ceph-uslab.antbrains.com`**

**Configuration details (from ingres.yaml):**
- **Host:** ceph-uslab.antbrains.com
- **Backend Protocol:** HTTPS (Ceph dashboard uses HTTPS internally)
- **SSL Passthrough:** Enabled (preserves end-to-end encryption)
- **Port:** 8443 (Ceph dashboard default port)
- **Ingress Class:** nginx

**To verify ingress is working:**
```bash
# Check ingress resource
kubectl -n rook-ceph get ingress rook-ceph-mgr-dashboard

# Expected output:
# NAME                       CLASS   HOSTS                        ADDRESS       PORTS     AGE
# rook-ceph-mgr-dashboard    nginx   ceph-uslab.antbrains.com     10.0.1.100    80, 443   Xd

# Test DNS resolution
nslookup ceph-uslab.antbrains.com
# Should resolve to 10.0.1.100 (VIP)

# Test HTTPS access
curl -k https://ceph-uslab.antbrains.com
# Should return HTML (dashboard login page)
```

**Dashboard Features to Explore:**
- **Cluster → Hosts:** View all nodes and their OSDs
- **Cluster → OSDs:** Detailed OSD performance and status
- **Pools:** Storage pool usage and configuration
- **Block → Images:** RBD images (persistent volumes)
- **Dashboard:** Overview with key metrics and health status

---

## Phase 6: Rancher Management Platform

**Purpose:** Install Rancher, a powerful multi-cluster Kubernetes management platform that provides a web UI, RBAC, monitoring, and centralized cluster management.

**Why Rancher:**
- **Unified Management:** Manage multiple Kubernetes clusters from a single interface
- **User-Friendly UI:** Intuitive web interface for cluster operations
- **RBAC Integration:** Fine-grained access control and user management
- **Catalog Apps:** Easy deployment of applications via Helm charts
- **Monitoring & Logging:** Built-in Prometheus and Grafana integration
- **Multi-Tenancy:** Project-based isolation and resource quotas

**What we're installing:**
1. **cert-manager:** Automatic TLS certificate management (required by Rancher)
2. **Rancher Server:** The management platform itself

**Time Required:** ~10-15 minutes

---

### Step 6.1: Install cert-manager

**Why cert-manager is required:**
- **Automatic TLS:** Automatically obtains and renews SSL/TLS certificates from Let's Encrypt
- **Certificate Lifecycle:** Manages certificate creation, renewal, and rotation
- **Webhook Integration:** Validates certificate requests before issuance
- **Rancher Dependency:** Rancher uses cert-manager for its own TLS certificates

**What cert-manager does:**
- Monitors Certificate resources in Kubernetes
- Requests certificates from Let's Encrypt (or other issuers)
- Stores certificates as Kubernetes Secrets
- Automatically renews certificates before expiration (typically 30 days before)

**Components:**
- **cert-manager controller:** Main certificate management logic
- **cert-manager-cainjector:** Injects CA bundles into webhooks and API services
- **cert-manager-webhook:** Validates certificate requests

Execute on **fratelli**:

```bash
# Add Jetstack Helm repository (cert-manager maintainers)
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Create cert-manager namespace
kubectl create namespace cert-manager

# Install cert-manager with CRDs
# CRDs define Certificate, Issuer, and ClusterIssuer resources
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.16.2 \
  --set crds.enabled=true

# Verify cert-manager installation (wait for all pods to be Running)
kubectl -n cert-manager get pods

# Expected output: 3 pods in Running state
# - cert-manager-<hash> (main controller)
# - cert-manager-cainjector-<hash> (CA injection)
# - cert-manager-webhook-<hash> (validation webhook)

# Verify cert-manager is ready
kubectl -n cert-manager rollout status deploy/cert-manager
kubectl -n cert-manager rollout status deploy/cert-manager-webhook
kubectl -n cert-manager rollout status deploy/cert-manager-cainjector
```

**Verification:**
```bash
# Check cert-manager logs for any errors
kubectl -n cert-manager logs -l app=cert-manager --tail=50

# Verify CRDs are installed
kubectl get crd | grep cert-manager
# Should show: certificates, issuers, clusterissuers, etc.
```

### Step 6.2: Install Rancher

**Configuration explained:**
- **hostname:** The domain name where Rancher will be accessible (rancher-uslab.antbrains.com)
- **bootstrapPassword:** Initial admin password (change this after first login!)
- **ingress.tls.source=letsEncrypt:** Use Let's Encrypt for automatic TLS certificates
- **letsEncrypt.email:** Email for Let's Encrypt notifications (certificate expiry warnings)
- **letsEncrypt.ingress.class=nginx:** Use nginx ingress controller for routing

**Why these settings:**
- **Let's Encrypt:** Free, automated SSL/TLS certificates (auto-renews every 90 days)
- **Nginx Ingress:** Already installed, handles HTTP/HTTPS routing
- **cattle-system namespace:** Rancher's dedicated namespace for its components

**What gets deployed:**
- Rancher server pods (typically 3 replicas for HA)
- Rancher webhook for admission control
- Ingress resource for external access
- Certificate resource for TLS

**Security Note:**
- Change the bootstrap password immediately after first login
- Use a strong password for production environments
- Consider integrating with external authentication (LDAP, SAML, OAuth)

Execute on **fratelli**:

```bash
# Add Rancher Helm repository
helm repo add rancher-latest https://releases.rancher.com/server-charts/latest
helm repo update

# Create cattle-system namespace
# This namespace will contain all Rancher components
kubectl create namespace cattle-system

# Install Rancher
# This will:
# 1. Deploy Rancher server pods
# 2. Create an ingress resource
# 3. Request a TLS certificate from Let's Encrypt via cert-manager
helm install rancher rancher-latest/rancher \
  --namespace cattle-system \
  --set hostname=rancher-uslab.antbrains.com \
  --set bootstrapPassword=admin \
  --set ingress.tls.source=letsEncrypt \
  --set letsEncrypt.email=admin@antbrains.com \
  --set letsEncrypt.ingress.class=nginx \
  --version v2.11.2

# Wait for Rancher to be ready (this may take 3-5 minutes)
# Rancher needs to:
# 1. Start all pods
# 2. Request and receive TLS certificate from Let's Encrypt
# 3. Initialize the database
kubectl -n cattle-system rollout status deploy/rancher

# Check Rancher pods (should see 3 replicas running)
kubectl -n cattle-system get pods

# Check if TLS certificate was issued successfully
kubectl -n cattle-system get certificate
# Should show: rancher certificate with READY=True
```

### Step 6.3: Access Rancher UI

**What to expect on first login:**
- **Password Change:** You'll be prompted to change the bootstrap password
- **Server URL Configuration:** Rancher will ask you to confirm the server URL
- **Telemetry:** Option to enable/disable anonymous statistics
- **Cluster Registration:** The local cluster will be automatically imported

**Initial Setup Steps:**
1. Change the default password to a strong password
2. Configure authentication (local, LDAP, Active Directory, SAML, OAuth)
3. Set up users and permissions (RBAC)
4. Configure cluster settings and defaults

**Rancher UI Overview:**
- **Home:** Cluster list and quick access
- **Cluster Explorer:** Kubernetes resources (pods, deployments, services, etc.)
- **Apps & Marketplace:** Helm chart catalog for easy app deployment
- **Cluster Management:** Node management, cluster settings, backups
- **Users & Authentication:** User management and access control

Execute on **fratelli**:

```bash
# Get Rancher URL
echo "https://rancher-uslab.antbrains.com"

# Get bootstrap password (if you didn't set one during installation)
kubectl -n cattle-system get secret bootstrap-secret \
  -o jsonpath="{.data.bootstrapPassword}" | base64 --decode && echo

# Verify Rancher is accessible
curl -k https://rancher-uslab.antbrains.com
# Should return HTML (Rancher login page)

# Check Rancher ingress
kubectl -n cattle-system get ingress
# Should show rancher ingress with host rancher-uslab.antbrains.com

# Access Rancher UI:
# 1. Navigate to https://rancher-uslab.antbrains.com
# 2. Login with username: admin
# 3. Password: <bootstrap password from above>
# 4. Set a new strong password when prompted
# 5. Confirm server URL: https://rancher-uslab.antbrains.com
# 6. Complete initial setup wizard
```

**Post-Login Configuration:**
```bash
# After logging in, configure:
# 1. Authentication provider (Settings → Authentication)
# 2. Create additional users (Users & Authentication → Users)
# 3. Set up projects for multi-tenancy (Cluster → Projects/Namespaces)
# 4. Configure monitoring (if desired)
```

**Troubleshooting:**
```bash
# If Rancher UI is not accessible:

# Check Rancher pods
kubectl -n cattle-system get pods
# All pods should be Running

# Check Rancher logs
kubectl -n cattle-system logs -l app=rancher --tail=100

# Check certificate status
kubectl -n cattle-system get certificate
# Should show READY=True

# Check ingress
kubectl -n cattle-system describe ingress rancher
```

---

## Phase 7: Ingress Controller

**Purpose:** The Nginx Ingress Controller routes external HTTP/HTTPS traffic to services inside the cluster. It acts as a reverse proxy and load balancer for web applications.

**Why Nginx Ingress Controller:**
- **Industry Standard:** Most widely used ingress controller in Kubernetes
- **Feature Rich:** SSL termination, path-based routing, virtual hosts, rate limiting
- **High Performance:** Efficient request handling and load balancing
- **Flexible:** Supports annotations for fine-grained control
- **Well Documented:** Extensive documentation and community support

**What it does:**
- **HTTP/HTTPS Routing:** Routes requests to appropriate services based on hostname and path
- **SSL/TLS Termination:** Handles HTTPS encryption/decryption
- **Load Balancing:** Distributes traffic across multiple pod replicas
- **Virtual Hosting:** Multiple domains on a single IP address
- **Path Rewriting:** URL manipulation and redirection

**How it works:**
```
External Request → VIP (10.0.1.100) → HAProxy → Nginx Ingress Controller → Service → Pods
```

The Nginx Ingress Controller is already installed and configured in your cluster.

### Step 7.1: Verify Ingress Controller

**What we're verifying:**
- Ingress controller pods are running
- IngressClass resource is configured
- Ingress controller service is exposed
- Controller is ready to handle ingress resources

**Understanding IngressClass:**
- Defines which ingress controller handles which ingress resources
- Allows multiple ingress controllers in the same cluster
- Ingress resources specify which class to use via `ingressClassName` or annotations

Execute on **fratelli**:

```bash
# Check ingress controller pods
kubectl get pods -n kube-system | grep ingress
# Should show rke2-ingress-nginx-controller pods running

# Alternative: Check all ingress-related pods
kubectl get pods -A | grep ingress

# Check ingress class
kubectl get ingressclass

# Expected output:
# NAME    CONTROLLER             PARAMETERS   AGE
# nginx   k8s.io/ingress-nginx   <none>       XXXd

# Describe ingress class for details
kubectl describe ingressclass nginx

# Check ingress controller service
kubectl get svc -n kube-system | grep ingress
# Should show LoadBalancer or NodePort service

# Check ingress controller configuration
kubectl get cm -n kube-system | grep ingress
# Shows ConfigMaps used for ingress controller configuration

# Verify ingress controller is healthy
kubectl -n kube-system logs -l app.kubernetes.io/name=rke2-ingress-nginx --tail=50
# Should show no errors, just normal request logs
```

**Verification checklist:**
```bash
# Count ingress controller pods (should be at least 1)
kubectl get pods -A | grep ingress | grep Running | wc -l

# Check if ingress class is set as default
kubectl get ingressclass nginx -o jsonpath='{.metadata.annotations.ingressclass\.kubernetes\.io/is-default-class}'
# Should return "true"
```

### Step 7.2: Test Ingress

**Why test the ingress:**
- Verifies ingress controller is working correctly
- Confirms routing rules are applied
- Tests DNS resolution and connectivity
- Validates the complete request flow

**What this test does:**
- Creates a simple nginx deployment
- Exposes it via a Kubernetes service
- Creates an ingress resource to route external traffic
- Tests HTTP access through the ingress

**Understanding the ingress resource:**
- **host:** Domain name for routing (test.antbrains.com)
- **path:** URL path to match (/ = all paths)
- **pathType: Prefix:** Matches all paths starting with /
- **backend:** Target service and port

Execute on **fratelli**:

```bash
# Create test namespace
kubectl create namespace test-ingress

# Create test deployment (simple nginx web server)
kubectl create deployment nginx --image=nginx -n test-ingress

# Wait for deployment to be ready
kubectl -n test-ingress rollout status deployment/nginx

# Expose deployment as a service
# This creates a ClusterIP service (internal only)
kubectl expose deployment nginx --port=80 -n test-ingress

# Verify service is created
kubectl -n test-ingress get svc nginx

# Create ingress resource
# This tells the ingress controller to route test.antbrains.com to our nginx service
cat << 'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: test-ingress
  namespace: test-ingress
  annotations:
    kubernetes.io/ingress.class: nginx
spec:
  rules:
  - host: test.antbrains.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: nginx
            port:
              number: 80
EOF

# Check ingress status
kubectl get ingress -n test-ingress
# Should show ADDRESS populated with VIP or node IP

# Describe ingress for details
kubectl describe ingress test-ingress -n test-ingress

# Add DNS entry or update /etc/hosts for testing
# Add this line to /etc/hosts:
# 10.0.1.100  test.antbrains.com

# Test access
curl http://test.antbrains.com
# Should return nginx welcome page HTML

# Test from browser
# Navigate to: http://test.antbrains.com
# Should see "Welcome to nginx!" page

# Clean up test resources (optional)
# kubectl delete namespace test-ingress
```

**Troubleshooting:**
```bash
# If ingress doesn't work:

# Check ingress controller logs
kubectl -n kube-system logs -l app.kubernetes.io/name=rke2-ingress-nginx --tail=100

# Check if ingress has an address
kubectl get ingress -n test-ingress
# ADDRESS column should not be empty

# Verify backend service exists
kubectl -n test-ingress get svc nginx

# Check if pods are running
kubectl -n test-ingress get pods

# Test service directly (from within cluster)
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- curl http://nginx.test-ingress.svc.cluster.local
```

---

## Phase 8: Certificate Management

**Purpose:** Configure automatic SSL/TLS certificate management using cert-manager and Let's Encrypt. This enables HTTPS for all ingress resources without manual certificate handling.

**Why Let's Encrypt:**
- **Free:** No cost for SSL/TLS certificates
- **Automated:** Certificates are automatically issued and renewed
- **Trusted:** Certificates are trusted by all major browsers
- **Standard:** Industry-standard ACME protocol

**How it works:**
1. You create an Ingress with TLS configuration
2. cert-manager detects the ingress and creates a Certificate resource
3. cert-manager requests a certificate from Let's Encrypt
4. Let's Encrypt validates domain ownership (HTTP-01 challenge)
5. Certificate is issued and stored as a Kubernetes Secret
6. Ingress controller uses the certificate for HTTPS
7. cert-manager automatically renews certificates before expiry (30 days before)

**Two types of issuers:**
- **Staging:** For testing (higher rate limits, not trusted by browsers)
- **Production:** For real use (lower rate limits, trusted certificates)

cert-manager is already installed (from Phase 6). Let's configure Let's Encrypt issuers.

### Step 8.1: Create Let's Encrypt Issuers

**What is a ClusterIssuer:**
- A cluster-wide resource that defines how to obtain certificates
- Can be used by any namespace in the cluster
- Alternative: Issuer (namespace-scoped)

**Configuration explained:**
- **acme.server:** Let's Encrypt API endpoint (production or staging)
- **email:** Contact email for certificate expiry notifications and account recovery
- **privateKeySecretRef:** Where to store the ACME account private key
- **solvers.http01:** HTTP-01 challenge method (proves domain ownership via HTTP)
- **ingress.class:** Which ingress controller to use for challenges

**Why two issuers (staging and production):**
- **Staging:** For testing, higher rate limits (no browser trust)
  - Rate limit: 30,000 certificates per week
  - Use for development and testing
- **Production:** For real use, lower rate limits (browser trusted)
  - Rate limit: 50 certificates per domain per week
  - Use only after testing with staging

**HTTP-01 Challenge Process:**
1. cert-manager requests a certificate from Let's Encrypt
2. Let's Encrypt provides a challenge token
3. cert-manager creates a temporary ingress route for `/.well-known/acme-challenge/<token>`
4. Let's Encrypt makes an HTTP request to verify domain ownership
5. If successful, certificate is issued

Execute on **fratelli**:

```bash
# Create production Let's Encrypt issuer
cat << 'EOF' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    # Production Let's Encrypt server (trusted certificates)
    server: https://acme-v02.api.letsencrypt.org/directory
    # Email for expiry notifications and account recovery
    email: admin@antbrains.com
    # Secret to store ACME account private key
    privateKeySecretRef:
      name: letsencrypt-prod
    # HTTP-01 challenge solver
    solvers:
    - http01:
        ingress:
          class: nginx  # Use nginx ingress for challenges
EOF

# Create staging Let's Encrypt issuer (for testing)
cat << 'EOF' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    # Staging Let's Encrypt server (for testing, not trusted by browsers)
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: admin@antbrains.com
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

# Verify issuers are created
kubectl get clusterissuer

# Expected output:
# NAME                   READY   AGE
# letsencrypt-prod       True    Xs
# letsencrypt-staging    True    Xs

# Describe issuer for details
kubectl describe clusterissuer letsencrypt-prod
# Should show Status: The ACME account was registered with the ACME server
```

**Verification:**
```bash
# Check if issuers are ready
kubectl get clusterissuer -o wide

# View issuer status
kubectl describe clusterissuer letsencrypt-prod | grep -A 5 "Status:"
# Should show "Ready" condition as True
```

### Step 8.2: Use TLS with Ingress

**How automatic TLS works:**
1. You create an Ingress with `tls` section and cert-manager annotation
2. cert-manager detects the ingress and creates a Certificate resource
3. Certificate resource triggers certificate issuance from Let's Encrypt
4. cert-manager handles the HTTP-01 challenge automatically
5. Certificate is stored in the specified Secret (e.g., `example-tls`)
6. Ingress controller reads the Secret and enables HTTPS

**Key annotations:**
- `cert-manager.io/cluster-issuer`: Which ClusterIssuer to use (letsencrypt-prod or letsencrypt-staging)
- `cert-manager.io/issuer`: Alternative for namespace-scoped Issuer

**TLS section explained:**
- **hosts:** List of domains to include in the certificate (can be multiple)
- **secretName:** Name of the Secret where certificate will be stored

**Important notes:**
- Domain must be publicly accessible (Let's Encrypt needs to reach it via HTTP)
- DNS must point to your cluster's VIP (10.0.1.100)
- Port 80 must be open for HTTP-01 challenge
- Certificate issuance takes 1-2 minutes

Example ingress with automatic TLS certificate:

```bash
cat << 'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: example-ingress-tls
  namespace: default
  annotations:
    kubernetes.io/ingress.class: nginx
    # This annotation tells cert-manager to automatically request a certificate
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - example.antbrains.com  # Domain(s) for the certificate
    secretName: example-tls   # Secret name where cert will be stored
  rules:
  - host: example.antbrains.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: example-service
            port:
              number: 80
EOF

# Watch certificate creation
kubectl get certificate -n default -w
# Should show example-tls certificate being created and becoming Ready

# Check certificate status
kubectl describe certificate example-tls -n default
# Should show events: Created new CertificateRequest, Certificate issued successfully

# Verify secret was created
kubectl get secret example-tls -n default
# Should show type: kubernetes.io/tls

# Test HTTPS access
curl -I https://example.antbrains.com
# Should return HTTP/2 200 with valid SSL certificate
```

**Troubleshooting certificate issuance:**
```bash
# Check certificate status
kubectl get certificate -A

# Describe certificate for events
kubectl describe certificate <cert-name> -n <namespace>

# Check certificate request
kubectl get certificaterequest -A

# Check cert-manager logs
kubectl -n cert-manager logs -l app=cert-manager --tail=100

# Common issues:
# 1. Domain not publicly accessible → Ensure DNS points to VIP
# 2. Port 80 blocked → Open firewall for HTTP-01 challenge
# 3. Rate limit exceeded → Use staging issuer for testing
# 4. Invalid email → Update issuer with valid email
```

# Check certificate status
kubectl get certificate -n default
kubectl describe certificate example-tls -n default
```

---

## Phase 9: Load Balancer (MetalLB)

**Purpose:** MetalLB provides LoadBalancer service type support for bare-metal Kubernetes clusters. In cloud environments, LoadBalancer services automatically get external IPs. MetalLB brings this functionality to on-premises clusters.

**Why MetalLB:**
- **LoadBalancer Services:** Enables `type: LoadBalancer` services in bare-metal environments
- **External IPs:** Automatically assigns external IPs from a configured pool
- **Layer 2 Mode:** Uses ARP to announce IPs on the local network (simple, no BGP required)
- **No External Dependencies:** Works without cloud provider integration
- **Production Ready:** Battle-tested in many production environments

**How MetalLB works (Layer 2 mode):**
1. You create a Service with `type: LoadBalancer`
2. MetalLB controller assigns an IP from the configured pool
3. MetalLB speaker pods respond to ARP requests for that IP
4. One speaker becomes the leader for each IP (using leader election)
5. Traffic to the IP is routed to the leader node
6. kube-proxy on that node forwards traffic to service endpoints

**Components:**
- **Controller:** Watches for LoadBalancer services and assigns IPs
- **Speaker:** Announces IPs using ARP (one pod per node)

**IP Pool Configuration:**
- **Range:** 10.0.1.101-10.0.1.200 (100 IPs available)
- **Network:** Same subnet as cluster nodes (10.0.1.0/24)
- **Mode:** Layer 2 (ARP-based)

MetalLB is already installed and configured in your cluster.

---

### Step 9.1: Verify MetalLB Installation

**What we're verifying:**
- MetalLB controller is running (assigns IPs)
- MetalLB speaker pods are running on all nodes (announce IPs)
- IP address pool is configured
- L2 advertisement is configured

Execute on **fratelli**:

```bash
# Check MetalLB pods
kubectl get pods -n metallb-system

# Expected output:
# NAME                          READY   STATUS    RESTARTS   AGE
# controller-<hash>             1/1     Running   0          XXd
# speaker-<hash> (one per node) 1/1     Running   0          XXd

# Should see 1 controller pod and 7 speaker pods (one per node)

# Check MetalLB configuration
kubectl get ipaddresspool -n metallb-system
# Should show configured IP pool (10.0.1.101-10.0.1.200)

kubectl get l2advertisement -n metallb-system
# Should show L2 advertisement configuration

# Describe IP pool for details
kubectl describe ipaddresspool -n metallb-system

# Check speaker logs (should show IP announcements)
kubectl -n metallb-system logs -l component=speaker --tail=50
```

**Verification:**
```bash
# Count speaker pods (should equal number of nodes = 7)
kubectl get pods -n metallb-system -l component=speaker --no-headers | wc -l

# Check if controller is healthy
kubectl -n metallb-system logs -l component=controller --tail=50
```

### Step 9.2: Configure MetalLB IP Pool (if not already configured)

**Configuration explained:**

**IPAddressPool:**
- **addresses:** IP range to allocate for LoadBalancer services
- **Range:** 10.0.1.101-10.0.1.200 (100 IPs)
- **Important:** IPs must be in the same subnet as nodes and not used elsewhere
- **Avoid:** Don't include VIP (10.0.1.100) or node IPs (10.0.1.25-60)

**L2Advertisement:**
- **ipAddressPools:** Which IP pools to advertise via Layer 2 (ARP)
- **Mode:** Layer 2 (simpler than BGP, suitable for single-subnet environments)
- **Behavior:** One node responds to ARP requests for each IP

**Why this IP range:**
- **10.0.1.1-10.0.1.99:** Reserved for infrastructure (routers, switches, VIP)
- **10.0.1.100:** Virtual IP for Kubernetes API
- **10.0.1.101-10.0.1.200:** MetalLB pool (100 IPs for services)
- **10.0.1.201-10.0.1.254:** Available for future use

Execute on **fratelli** (if not already configured):

```bash
# Create IP address pool
cat << 'EOF' | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: default-pool
  namespace: metallb-system
spec:
  addresses:
  # IP range for LoadBalancer services
  # Ensure these IPs are not used by DHCP or other services
  - 10.0.1.101-10.0.1.200
  # You can add multiple ranges:
  # - 10.0.1.101-10.0.1.150
  # - 10.0.1.160-10.0.1.200
EOF

# Create L2 advertisement
cat << 'EOF' | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: default
  namespace: metallb-system
spec:
  ipAddressPools:
  - default-pool  # Reference to the IP pool above
  # Optional: Specify which nodes can announce IPs
  # nodeSelectors:
  # - matchLabels:
  #     kubernetes.io/hostname: pane
EOF

# Verify configuration
kubectl get ipaddresspool -n metallb-system
kubectl get l2advertisement -n metallb-system

# Describe for details
kubectl describe ipaddresspool default-pool -n metallb-system
```

### Step 9.3: Test MetalLB

**What this test does:**
- Creates a simple nginx deployment
- Exposes it as a LoadBalancer service
- Verifies MetalLB assigns an external IP
- Tests connectivity to the external IP

**Expected behavior:**
1. Service is created with `type: LoadBalancer`
2. MetalLB controller assigns an IP from the pool (e.g., 10.0.1.101)
3. MetalLB speaker announces the IP via ARP
4. External clients can access the service via the assigned IP

Execute on **fratelli**:

```bash
# Create test deployment (simple nginx web server)
kubectl create deployment nginx-lb --image=nginx

# Wait for deployment to be ready
kubectl rollout status deployment/nginx-lb

# Expose deployment as LoadBalancer service
# This triggers MetalLB to assign an external IP
kubectl expose deployment nginx-lb --port=80 --type=LoadBalancer

# Check service (should get an EXTERNAL-IP from MetalLB pool)
kubectl get svc nginx-lb

# Expected output:
# NAME       TYPE           CLUSTER-IP      EXTERNAL-IP    PORT(S)        AGE
# nginx-lb   LoadBalancer   10.49.x.x       10.0.1.101     80:xxxxx/TCP   Xs
#
# EXTERNAL-IP should be from the MetalLB pool (10.0.1.101-200)
# If it shows <pending>, wait a few seconds and check again

# Watch service until IP is assigned
kubectl get svc nginx-lb -w

# Get the assigned IP
EXTERNAL_IP=$(kubectl get svc nginx-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "External IP: $EXTERNAL_IP"

# Test access from any machine on the network
curl http://$EXTERNAL_IP
# Should return nginx welcome page HTML

# Test from browser
# Navigate to: http://<EXTERNAL-IP>
# Should see "Welcome to nginx!" page

# Check which node is announcing the IP
kubectl -n metallb-system logs -l component=speaker | grep $EXTERNAL_IP

# Clean up test resources
kubectl delete svc nginx-lb
kubectl delete deployment nginx-lb
```

**Troubleshooting:**
```bash
# If EXTERNAL-IP stays <pending>:

# Check MetalLB controller logs
kubectl -n metallb-system logs -l component=controller --tail=100

# Check if IP pool has available IPs
kubectl describe ipaddresspool -n metallb-system

# Check speaker logs
kubectl -n metallb-system logs -l component=speaker --tail=100

# Verify L2 advertisement is configured
kubectl get l2advertisement -n metallb-system

# Common issues:
# 1. No IP pool configured → Create IPAddressPool
# 2. All IPs allocated → Expand IP pool range
# 3. Speaker pods not running → Check pod status
# 4. Network issues → Verify ARP is working on the network
```

---

## Operations and Maintenance

**Purpose:** This section covers day-to-day operations, monitoring, and maintenance tasks for keeping the cluster healthy and performant.

**Key operational areas:**
- **Health Monitoring:** Regular checks to ensure cluster is functioning properly
- **Resource Management:** Monitoring and optimizing resource usage
- **Updates and Upgrades:** Keeping the cluster up-to-date
- **Backup and Recovery:** Protecting against data loss
- **Troubleshooting:** Diagnosing and resolving issues

### Daily Operations

**Recommended daily checks:**
- Node health and resource usage
- Pod status across all namespaces
- Storage health (Ceph)
- Persistent volume status
- Recent events and errors

---

#### Check Cluster Health

**Why daily health checks:**
- **Early Detection:** Catch issues before they become critical
- **Capacity Planning:** Monitor resource trends
- **Performance:** Identify bottlenecks and optimization opportunities
- **Compliance:** Ensure cluster meets SLAs

**What to look for:**
- All nodes in "Ready" state
- No pods in CrashLoopBackOff or Error state
- Ceph health is HEALTH_OK
- No persistent volumes in "Pending" state
- Resource usage below 80% (CPU/memory)

Execute on **fratelli** or your local machine:

```bash
# Check node status (all should be Ready)
kubectl get nodes
# Look for: STATUS=Ready, no NotReady nodes

# Check node details with resource info
kubectl get nodes -o wide
# Shows: IP, OS, kernel version, container runtime

# Check all pods across all namespaces
kubectl get pods -A
# Look for: No pods in Error, CrashLoopBackOff, or Pending state

# Check for failed pods
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
# Should return empty (no failed pods)

# Check cluster resources (requires metrics-server)
kubectl top nodes
# Shows: CPU and memory usage per node
# Alert if any node >80% CPU or memory

kubectl top pods -A --sort-by=memory | head -20
# Shows: Top 20 memory-consuming pods

kubectl top pods -A --sort-by=cpu | head -20
# Shows: Top 20 CPU-consuming pods

# Check Ceph storage health
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status
# Look for: health: HEALTH_OK

kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph df
# Shows: Storage usage, available capacity
# Alert if >80% full

# Check persistent volumes
kubectl get pv
# Look for: STATUS=Bound (not Released or Failed)

kubectl get pvc -A
# Look for: STATUS=Bound (not Pending)

# Check recent events (last hour)
kubectl get events -A --sort-by='.lastTimestamp' | tail -50
# Look for: Warning or Error events

# Check component status
kubectl get componentstatuses
# All should show: Healthy

# Quick health summary script
echo "=== Cluster Health Summary ==="
echo "Nodes: $(kubectl get nodes --no-headers | wc -l) total, $(kubectl get nodes --no-headers | grep -c Ready) ready"
echo "Pods: $(kubectl get pods -A --no-headers | wc -l) total, $(kubectl get pods -A --no-headers | grep -c Running) running"
echo "PVs: $(kubectl get pv --no-headers | wc -l) total, $(kubectl get pv --no-headers | grep -c Bound) bound"
echo "Ceph: $(kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph health 2>/dev/null | tr -d '\r')"
```

#### Monitor System Resources

**Why monitor system resources:**
- **Prevent Outages:** Detect resource exhaustion before it causes failures
- **Capacity Planning:** Understand usage trends for scaling decisions
- **Performance Optimization:** Identify resource bottlenecks
- **Cost Management:** Right-size resources to avoid waste

**Key metrics to monitor:**
- **CPU Usage:** Should be <80% average, <90% peak
- **Memory Usage:** Should be <80% to allow for bursts
- **Disk Usage:** Should be <80% (Ceph needs space for rebalancing)
- **Disk I/O:** High iowait indicates storage bottleneck
- **Network:** Monitor bandwidth and packet loss

Execute on **fratelli** or directly on nodes:

```bash
# On each node, check system resources
# CPU, memory, and process overview
ssh fratelli "htop"
# Look for: High CPU usage, memory pressure, zombie processes

# Disk usage
ssh fratelli "df -h"
# Alert if any filesystem >80% full
# Critical: / (root), /var/lib/rancher/rke2 (container storage)

# Memory usage
ssh fratelli "free -h"
# Look for: Available memory, swap usage (should be 0)

# Disk I/O statistics
ssh fratelli "iostat -x 1 5"
# Look for: %util >80% indicates disk bottleneck

# Network statistics
ssh fratelli "sar -n DEV 1 5"
# Look for: High packet loss, errors

# Check containerd status (container runtime)
ssh fratelli "sudo systemctl status containerd"
# Should be: active (running)

# Check RKE2 status
ssh fratelli "sudo systemctl status rke2-server"  # On control plane
ssh pane "sudo systemctl status rke2-agent"       # On workers
# Should be: active (running)

# Check for OOM (Out of Memory) kills
ssh fratelli "sudo dmesg | grep -i 'out of memory'"
# Should be empty (no OOM kills)

# Check system load
ssh fratelli "uptime"
# Load average should be < number of CPU cores

# Automated resource check across all nodes
for node in fratelli pane cicis cesar papaj grimaldi rosso; do
    echo "=== $node ==="
    ssh $node "echo 'CPU Cores: \$(nproc)'; \
               echo 'Load Average: \$(uptime | awk -F'load average:' '{print \$2}')'; \
               echo 'Memory: \$(free -h | grep Mem | awk '{print \$3\"/\"\$2\" used\"}')'; \
               echo 'Disk: \$(df -h / | tail -1 | awk '{print \$5\" used\"}')'"
    echo ""
done
```

#### View Logs

```bash
# RKE2 server logs (on fratelli)
sudo journalctl -u rke2-server -f

# RKE2 agent logs (on workers)
sudo journalctl -u rke2-agent -f

# HAProxy logs (on fratelli)
sudo journalctl -u haproxy -f

# Keepalived logs (on fratelli)
sudo journalctl -u keepalived -f

# Pod logs
kubectl logs -f <pod-name> -n <namespace>
kubectl logs -f <pod-name> -n <namespace> --previous  # Previous container logs
```

### Maintenance Tasks

#### Update RKE2

```bash
# On control plane (fratelli):
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.2+rke2r1" sh -
sudo systemctl restart rke2-server

# Wait for control plane to be ready
kubectl get nodes

# On each worker node (one at a time):
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.2+rke2r1" INSTALL_RKE2_TYPE="agent" sh -
sudo systemctl restart rke2-agent

# Verify all nodes are updated
kubectl get nodes -o wide
```

#### Drain and Uncordon Nodes

```bash
# Drain node for maintenance
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Perform maintenance on the node
# ...

# Uncordon node to allow scheduling
kubectl uncordon <node-name>
```

#### Add New Worker Node

```bash
# On the new node, follow Phase 4 instructions
# Use the same node token from fratelli

# Verify node joined
kubectl get nodes
```

#### Remove Worker Node

```bash
# Drain the node
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force

# Delete the node from cluster
kubectl delete node <node-name>

# On the node itself, stop and disable RKE2
sudo systemctl stop rke2-agent
sudo systemctl disable rke2-agent

# Optional: Clean up RKE2 data
sudo /usr/local/bin/rke2-uninstall.sh
```

---

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: Node Not Ready

**Symptoms:**
```bash
kubectl get nodes
# NAME     STATUS     ROLES    AGE   VERSION
# pane     NotReady   worker   1d    v1.33.1+rke2r1
```

**Solution:**
```bash
# Check node logs
ssh pane "sudo journalctl -u rke2-agent -n 100"

# Check kubelet status
ssh pane "sudo systemctl status rke2-agent"

# Restart RKE2 agent
ssh pane "sudo systemctl restart rke2-agent"

# Check network connectivity
ssh pane "ping -c 3 10.0.1.30"  # Ping control plane
ssh pane "ping -c 3 10.0.1.100" # Ping VIP
```

#### Issue 2: Pods Stuck in Pending

**Symptoms:**
```bash
kubectl get pods -A | grep Pending
```

**Solution:**
```bash
# Describe the pod to see events
kubectl describe pod <pod-name> -n <namespace>

# Common causes:
# 1. Insufficient resources
kubectl top nodes

# 2. PVC not bound
kubectl get pvc -A

# 3. Node selector/affinity issues
kubectl get pod <pod-name> -n <namespace> -o yaml | grep -A 10 nodeSelector

# 4. Taints and tolerations
kubectl describe node <node-name> | grep Taints
```

#### Issue 3: Ceph Health Warning

**Symptoms:**
```bash
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status
# health: HEALTH_WARN
```

**Solution:**
```bash
# Check detailed health
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph health detail

# Check OSD status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd status

# Check OSD tree
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd tree

# Restart Ceph operator if needed
kubectl -n rook-ceph rollout restart deployment/rook-ceph-operator
```

#### Issue 4: Ingress Not Working

**Symptoms:**
- Cannot access application via ingress URL
- 404 or 502 errors

**Solution:**
```bash
# Check ingress resource
kubectl get ingress -A
kubectl describe ingress <ingress-name> -n <namespace>

# Check ingress controller pods
kubectl get pods -n kube-system | grep ingress

# Check ingress controller logs
kubectl logs -n kube-system <ingress-controller-pod>

# Verify service exists
kubectl get svc -n <namespace>

# Test service directly
kubectl port-forward svc/<service-name> 8080:80 -n <namespace>
curl http://localhost:8080

# Check DNS resolution
nslookup <your-domain>
```

#### Issue 5: Certificate Not Issued

**Symptoms:**
```bash
kubectl get certificate -A
# NAME          READY   SECRET        AGE
# example-tls   False   example-tls   5m
```

**Solution:**
```bash
# Describe certificate
kubectl describe certificate <cert-name> -n <namespace>

# Check certificate request
kubectl get certificaterequest -n <namespace>
kubectl describe certificaterequest <cr-name> -n <namespace>

# Check cert-manager logs
kubectl logs -n cert-manager deployment/cert-manager

# Check challenge (for HTTP-01)
kubectl get challenge -A
kubectl describe challenge <challenge-name> -n <namespace>

# Verify DNS is pointing to your cluster
nslookup <your-domain>

# Verify ingress is accessible on port 80
curl http://<your-domain>/.well-known/acme-challenge/test
```

---

## Backup and Disaster Recovery

### Velero Backup Solution

Velero is already installed in your cluster for backup and disaster recovery.

#### Verify Velero Installation

```bash
# Check Velero pods
kubectl get pods -n velero

# Check Velero backup locations
kubectl get backupstoragelocation -n velero

# Check Velero schedules
kubectl get schedule -n velero
```

#### Create Manual Backup

```bash
# Backup entire cluster
velero backup create full-cluster-backup

# Backup specific namespace
velero backup create namespace-backup --include-namespaces <namespace>

# Backup with specific labels
velero backup create app-backup --selector app=myapp

# Check backup status
velero backup describe full-cluster-backup
velero backup logs full-cluster-backup
```

#### Restore from Backup

```bash
# List available backups
velero backup get

# Restore from backup
velero restore create --from-backup full-cluster-backup

# Restore specific namespace
velero restore create --from-backup namespace-backup --include-namespaces <namespace>

# Check restore status
velero restore describe <restore-name>
velero restore logs <restore-name>
```

#### Schedule Automated Backups

```bash
# Create daily backup schedule
velero schedule create daily-backup --schedule="0 2 * * *"

# Create weekly backup schedule
velero schedule create weekly-backup --schedule="0 3 * * 0"

# List schedules
velero schedule get
```

### etcd Backup (Manual)

```bash
# On fratelli (control plane), backup etcd
sudo /var/lib/rancher/rke2/bin/etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  snapshot save /var/lib/rancher/rke2/server/db/etcd-snapshot-$(date +%Y%m%d-%H%M%S).db

# List snapshots
sudo ls -lh /var/lib/rancher/rke2/server/db/

# Copy snapshot to safe location
sudo cp /var/lib/rancher/rke2/server/db/etcd-snapshot-*.db /backup/location/
```

---

## Security Best Practices

### Network Security

1. **Firewall Configuration**
   - Use UFW or iptables to restrict access
   - Only allow necessary ports
   - Restrict SSH access to specific IPs

2. **Network Policies**
   ```bash
   # Example: Deny all ingress traffic by default
   cat << 'EOF' | kubectl apply -f -
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: default-deny-ingress
     namespace: default
   spec:
     podSelector: {}
     policyTypes:
     - Ingress
   EOF
   ```

3. **TLS Everywhere**
   - Use TLS for all ingress resources
   - Enable TLS for internal services
   - Rotate certificates regularly

### Access Control

1. **RBAC (Role-Based Access Control)**
   ```bash
   # Create read-only user
   kubectl create serviceaccount readonly-user
   kubectl create clusterrolebinding readonly-binding \
     --clusterrole=view \
     --serviceaccount=default:readonly-user
   ```

2. **Pod Security Standards**
   ```bash
   # Enable Pod Security Admission
   kubectl label namespace default \
     pod-security.kubernetes.io/enforce=restricted \
     pod-security.kubernetes.io/audit=restricted \
     pod-security.kubernetes.io/warn=restricted
   ```

3. **Secrets Management**
   - Use Kubernetes secrets for sensitive data
   - Consider using external secret management (Vault, Sealed Secrets)
   - Encrypt secrets at rest

### Monitoring and Auditing

1. **Enable Audit Logging**
   - Configure RKE2 audit policy
   - Store audit logs securely
   - Review logs regularly

2. **Monitor Security Events**
   - Use Rancher's security scanning
   - Monitor for suspicious activity
   - Set up alerts for security events

---

## Appendices

### Appendix A: Port Requirements

**Control Plane Node (fratelli):**

| Port Range    | Protocol | Purpose                          |
|---------------|----------|----------------------------------|
| 22            | TCP      | SSH                              |
| 80            | TCP      | HTTP Ingress                     |
| 443           | TCP      | HTTPS Ingress                    |
| 2379-2380     | TCP      | etcd client and peer             |
| 6443          | TCP      | Kubernetes API Server            |
| 6444          | TCP      | HAProxy (load balanced API)      |
| 8404          | TCP      | HAProxy stats                    |
| 9345          | TCP      | RKE2 supervisor API              |
| 10250         | TCP      | Kubelet API                      |
| 8472          | UDP      | Calico VXLAN                     |

**Worker Nodes:**

| Port Range    | Protocol | Purpose                          |
|---------------|----------|----------------------------------|
| 22            | TCP      | SSH                              |
| 80            | TCP      | HTTP Ingress                     |
| 443           | TCP      | HTTPS Ingress                    |
| 10250         | TCP      | Kubelet API                      |
| 30000-32767   | TCP      | NodePort services                |
| 8472          | UDP      | Calico VXLAN                     |

### Appendix B: Useful Commands Reference

```bash
# Cluster Information
kubectl cluster-info
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get svc -A
kubectl get ingress -A

# Resource Usage
kubectl top nodes
kubectl top pods -A

# Logs
kubectl logs -f <pod> -n <namespace>
kubectl logs -f <pod> -n <namespace> --previous
kubectl logs -f <pod> -c <container> -n <namespace>

# Debugging
kubectl describe node <node>
kubectl describe pod <pod> -n <namespace>
kubectl exec -it <pod> -n <namespace> -- /bin/bash
kubectl port-forward <pod> 8080:80 -n <namespace>

# Configuration
kubectl get configmap -A
kubectl get secret -A
kubectl edit deployment <deployment> -n <namespace>

# Scaling
kubectl scale deployment <deployment> --replicas=3 -n <namespace>
kubectl autoscale deployment <deployment> --min=2 --max=10 --cpu-percent=80 -n <namespace>

# Rollout Management
kubectl rollout status deployment/<deployment> -n <namespace>
kubectl rollout history deployment/<deployment> -n <namespace>
kubectl rollout undo deployment/<deployment> -n <namespace>

# Ceph Commands
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph df
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph health detail
```

### Appendix C: Configuration File Locations

**On Control Plane (fratelli):**
- RKE2 config: `/etc/rancher/rke2/config.yaml`
- Kubeconfig: `/etc/rancher/rke2/rke2.yaml`
- Node token: `/var/lib/rancher/rke2/server/node-token`
- HAProxy config: `/etc/haproxy/haproxy.cfg`
- Keepalived config: `/etc/keepalived/keepalived.conf`
- etcd data: `/var/lib/rancher/rke2/server/db/`

**On Worker Nodes:**
- RKE2 config: `/etc/rancher/rke2/config.yaml`
- RKE2 data: `/var/lib/rancher/rke2/`

**System Configuration:**
- Kernel modules: `/etc/modules-load.d/k8s.conf`
- Sysctl parameters: `/etc/sysctl.d/99-kubernetes.conf`
- System limits: `/etc/security/limits.d/kubernetes.conf`
- Hosts file: `/etc/hosts`

### Appendix D: Cluster Specifications Summary

**Cluster Details:**
- **Name:** uslab-cluster
- **Kubernetes Version:** v1.33.1+rke2r1
- **Distribution:** RKE2
- **Container Runtime:** containerd 2.0.5-k3s1
- **CNI:** Calico (VXLAN mode)
- **Pod CIDR:** 10.48.0.0/16
- **Service CIDR:** 10.49.0.0/16
- **Cluster DNS:** 10.49.0.10

**Installed Components:**
- Rancher v2.11.2
- Rook-Ceph v1.15.8
- cert-manager v1.16.2
- Nginx Ingress Controller
- MetalLB
- Velero (backup solution)
- Calico CNI

**Storage Classes:**
- ceph-block (default) - RBD block storage
- ceph-filesystem - CephFS shared filesystem
- ceph-bucket - Object storage (S3-compatible)
- postgres-fast-ssd - Optimized for PostgreSQL data
- postgres-wal-ssd - Optimized for PostgreSQL WAL

---

## Document Revision History

| Version | Date       | Author              | Changes                                    |
|---------|------------|---------------------|--------------------------------------------|
| 1.0     | 2025-06-10 | Infrastructure Team | Initial cluster deployment                 |
| 2.0     | 2026-01-22 | Infrastructure Team | Complete documentation rewrite and update  |

---

## Support and Contact

For questions or issues related to this cluster:

- **Documentation:** This guide
- **Rancher UI:** https://rancher-uslab.antbrains.com
- **Ceph Dashboard:** https://ceph-uslab.antbrains.com
- **Infrastructure Team:** infrastructure@antbrains.com

---

**End of Document**