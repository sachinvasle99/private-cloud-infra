# US Lab RKE2 Cluster Setup Guide

**Document Version:** 2.0
**Last Updated:** January 2026
**Cluster Name:** uslab-cluster
**Environment:** Production
**Author:** Krista Infrastructure Team

---

## Table of Contents

1. [Overview](#overview)
2. [Infrastructure Planning](#infrastructure-planning)
3. [Prerequisites](#prerequisites)
4. [Server Preparation](#server-preparation)
5. [Load Balancer Setup (HAProxy + Keepalived)](#load-balancer-setup)
6. [Kubernetes Installation (RKE2)](#kubernetes-installation)
7. [Rancher Installation](#rancher-installation)
8. [Storage Setup (Ceph via Rook)](#storage-setup)
9. [Verification and Testing](#verification-and-testing)
10. [Troubleshooting](#troubleshooting)
11. [Maintenance and Upgrades](#maintenance-and-upgrades)

---

## Overview

This document provides a comprehensive guide for deploying a production-grade RKE2 Kubernetes cluster in the US Lab environment. The cluster features:

- **High Availability**: Multi-master control plane with HAProxy/Keepalived load balancing
- **High Capacity**: Optimized for servers with 40-128 CPU cores and 128-900GB RAM
- **Enterprise Storage**: Ceph distributed storage via Rook operator
- **Management UI**: Rancher for centralized cluster management
- **Security**: TLS encryption, RBAC, and network policies
- **Scalability**: Designed for multi-tenant workloads with resource isolation

---

## Infrastructure Planning

### Cluster Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Load Balancer Layer                          │
│      VIP: 10.0.1.100 (HAProxy + Keepalived on fratelli)        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │
                    ┌─────────▼────────┐
                    │    fratelli      │
                    │  Control Plane   │
                    │    10.0.1.30     │
                    │  Ubuntu 24.04    │
                    └──────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┬─────────────┐
        │                     │                     │             │
┌───────▼────────┐  ┌─────────▼────────┐  ┌────────▼────────┐  ┌─▼──────────┐
│     pane       │  │      cicis       │  │     cesar       │  │   papaj    │
│    Worker      │  │     Worker       │  │     Worker      │  │   Worker   │
│   10.0.1.25    │  │    10.0.1.35     │  │   10.0.1.40     │  │ 10.0.1.45  │
│ Ubuntu 24.04   │  │  Ubuntu 24.04    │  │  Ubuntu 24.04   │  │Ubuntu 24.04│
└────────────────┘  └──────────────────┘  └─────────────────┘  └────────────┘
                              │
                    ┌─────────▼────────┐
                    │    grimaldi      │
                    │     Worker       │
                    │    10.0.1.50     │
                    │  Ubuntu 24.04    │
                    └──────────────────┘
```

### Server Inventory

| Hostname  | IP Address | Role          | CPU Cores | RAM    | Storage | Notes                    |
|-----------|------------|---------------|-----------|--------|---------|--------------------------|
| fratelli  | 10.0.1.30  | Control Plane | 40        | 128GB  | 1TB     | Single master node       |
| pane      | 10.0.1.25  | Worker        | 40        | 128GB  | 1TB     | Worker node              |
| cicis     | 10.0.1.35  | Worker        | 40        | 128GB  | 1TB     | Worker node              |
| cesar     | 10.0.1.40  | Worker        | 40        | 128GB  | 1TB     | Worker node              |
| papaj     | 10.0.1.45  | Worker        | 40        | 128GB  | 1TB     | Worker node              |
| grimaldi  | 10.0.1.50  | Worker        | 80        | 640GB  | 2TB     | High-capacity worker     |

### Network Configuration

- **Cluster API VIP**: `10.0.1.100` (HAProxy + Keepalived)
- **Pod Network (CIDR)**: `10.48.0.0/16`
- **Service Network (CIDR)**: `10.49.0.0/16`
- **Cluster DNS**: `10.49.0.10`
- **Domain**: `antbrains.com`

### DNS Records Required

| Record Type | Hostname                      | IP Address  | Purpose                    |
|-------------|-------------------------------|-------------|----------------------------|
| A           | k8s-api.antbrains.com         | 10.0.1.100  | Kubernetes API endpoint    |
| A           | rancher-uslab.antbrains.com   | 10.0.1.100  | Rancher management UI      |
| A           | *.apps.antbrains.com          | 10.0.1.100  | Application ingress        |

---

## Prerequisites

### Required Access and Credentials

- ✅ Root or sudo access to all servers
- ✅ SSH key-based authentication configured
- ✅ DNS records configured and propagated
- ✅ Valid email address for Let's Encrypt certificates
- ✅ Network connectivity between all nodes

### Software Requirements

- **Operating System**: Ubuntu 24.04 LTS (all nodes)
- **Kernel**: Linux 5.15+ with required modules
- **Container Runtime**: Included with RKE2 (containerd)
- **Network**: Private network with Layer 2 connectivity for VRRP

### Firewall Ports

If using host-based firewalls, ensure these ports are open:

| Port Range    | Protocol | Purpose                          | Direction |
|---------------|----------|----------------------------------|-----------|
| 22            | TCP      | SSH                              | Inbound   |
| 6443          | TCP      | Kubernetes API Server            | Inbound   |
| 6444          | TCP      | HAProxy Kubernetes API           | Inbound   |
| 2379-2380     | TCP      | etcd client/peer                 | Inbound   |
| 10250         | TCP      | Kubelet API                      | Inbound   |
| 9345          | TCP      | RKE2 supervisor API              | Inbound   |
| 30000-32767   | TCP      | NodePort Services                | Inbound   |
| 80, 443       | TCP      | HTTP/HTTPS Ingress               | Inbound   |
| 8404          | TCP      | HAProxy Stats                    | Inbound   |
| VRRP          | 112      | Keepalived VRRP                  | Multicast |

---

## Server Preparation

### Step 1: Initial System Setup

Execute the following commands on **ALL 6 servers** (fratelli, pane, cicis, cesar, papaj, grimaldi):

#### 1.1 Update System and Install Essential Packages

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install essential packages
sudo apt install -y \
    curl wget git vim htop net-tools \
    software-properties-common apt-transport-https \
    ca-certificates gnupg lsb-release \
    nfs-common open-iscsi lvm2 cryptsetup

# Verify installation
which curl wget git vim htop
```

#### 1.2 Configure Hostname and Hosts File

```bash
# Set hostname (run on each server with appropriate hostname)
# Example for fratelli:
sudo hostnamectl set-hostname fratelli

# Verify hostname
hostnamectl

# Add all cluster nodes to /etc/hosts
cat << 'EOF' | sudo tee -a /etc/hosts
# RKE2 Cluster Nodes
10.0.1.30  fratelli
10.0.1.25  pane
10.0.1.35  cicis
10.0.1.40  cesar
10.0.1.45  papaj
10.0.1.50  grimaldi

# Virtual IP for Load Balancer
10.0.1.100 k8s-api.antbrains.com rancher-uslab.antbrains.com
EOF

# Verify connectivity
ping -c 2 fratelli
ping -c 2 k8s-api.antbrains.com
```

#### 1.3 Disable Swap

```bash
# Disable swap immediately
sudo swapoff -a

# Disable swap permanently
sudo sed -i '/swap.img/s|^|#|' /etc/fstab
sudo sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab

# Verify swap is disabled
free -h | grep -i swap
```

#### 1.4 Load Required Kernel Modules

```bash
# Create kernel modules configuration
cat << 'EOF' | sudo tee /etc/modules-load.d/k8s.conf
# Kernel modules required for Kubernetes
overlay
br_netfilter
EOF

# Load modules immediately
sudo modprobe overlay
sudo modprobe br_netfilter

# Verify modules are loaded
lsmod | grep -E 'overlay|br_netfilter'
```

#### 1.5 Configure Kernel Parameters

```bash
# Create sysctl configuration for Kubernetes
cat << 'EOF' | sudo tee /etc/sysctl.d/k8s.conf
# Kubernetes networking requirements
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF

# Apply sysctl settings
sudo sysctl --system

# Verify settings
sysctl net.bridge.bridge-nf-call-iptables
sysctl net.ipv4.ip_forward
```

#### 1.6 Configure Firewall (Optional)

> **Note**: For private networks with network-level security, host-based firewalls may not be necessary. Skip this section if you have perimeter firewalls.

```bash
# OPTIONAL: Configure UFW firewall
# Only run if host-level firewall is required

sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH
sudo ufw allow ssh

# Allow Kubernetes ports
sudo ufw allow 6443/tcp        # Kubernetes API
sudo ufw allow 6444/tcp        # HAProxy API
sudo ufw allow 2379:2380/tcp   # etcd
sudo ufw allow 10250/tcp       # kubelet
sudo ufw allow 9345/tcp        # RKE2 supervisor
sudo ufw allow 30000:32767/tcp # NodePort services
sudo ufw allow 80/tcp          # HTTP
sudo ufw allow 443/tcp         # HTTPS
sudo ufw allow 8404/tcp        # HAProxy stats

# Reload firewall
sudo ufw reload
sudo ufw status
```

---

### Step 2: High-Capacity Server Optimizations

For servers with high CPU/RAM (grimaldi with 80 cores / 640GB RAM), apply additional optimizations:

```bash
# Optimize kernel parameters for high-capacity nodes
cat << 'EOF' | sudo tee /etc/sysctl.d/k8s-high-capacity.conf
# File system limits
fs.file-max = 2097152
fs.nr_open = 1048576

# Network optimizations
net.core.somaxconn = 32768
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.core.netdev_max_backlog = 4000
net.core.netdev_budget = 600

# TCP buffer sizes
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728

# TCP congestion control
net.ipv4.tcp_congestion_control = bbr

# Memory management
vm.max_map_count = 524288
EOF

# Apply settings
sudo sysctl --system
```

```bash
# Set ulimits for container runtime
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

---

## Load Balancer Setup

### Step 3: Install and Configure HAProxy + Keepalived

Execute on **fratelli** (control plane node only):

#### 3.1 Install HAProxy and Keepalived

```bash
# Install load balancer components
sudo apt install -y haproxy keepalived

# Verify installation
haproxy -v
keepalived -v
```

#### 3.2 Configure HAProxy

Create HAProxy configuration on **fratelli**:

```bash
# Backup original configuration
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

#### 3.3 Configure Keepalived

Configure Keepalived on **fratelli** to manage the Virtual IP (10.0.1.100):

```bash
# Determine your network interface name
ip addr show

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

#### 3.4 Start and Enable Services

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
```

---

## Kubernetes Installation

### Step 4: Install RKE2 on Control Plane Node

Execute on **fratelli** (control plane node):

```bash
# Create RKE2 configuration directory
sudo mkdir -p /etc/rancher/rke2

# Configure RKE2 for high-capacity servers
cat << 'EOF' | sudo tee /etc/rancher/rke2/config.yaml
# RKE2 Server Configuration
tls-san:
  - rancher-uslab.antbrains.com
  - 10.0.1.100
  - 10.0.1.30

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

# Disable default CNI and use Calico
disable:
  - rke2-canal
cni:
  - calico

# Enable etcd metrics
etcd-expose-metrics: true

# Optional: Remove node-taint to allow workloads on control plane
# Remove node-taint to allow workloads on control plane (high-capacity servers)
node-taint:
  - "CriticalAddonsOnly=true:NoExecute"
# Resource configurations for high-capacity nodes
#kubelet-arg:
#  - "max-pods=200"
#  - "kube-reserved=cpu=4,memory=8Gi,ephemeral-storage=10Gi"
#  - "system-reserved=cpu=2,memory=4Gi,ephemeral-storage=5Gi"
#  - "eviction-hard=memory.available<5%,nodefs.available<10%"
#kube-apiserver-arg:
#  - "max-requests-inflight=1000"
#  - "max-mutating-requests-inflight=500"
#kube-controller-manager-arg:
#  - "node-monitor-period=5s"
#  - "node-monitor-grace-period=40s"
#kube-scheduler-arg:
#  - "kube-api-qps=100"
#  - "kube-api-burst=200"
EOF

# Install RKE2
curl -sfL https://get.rke2.io | sudo sh -

# Enable and start RKE2
sudo systemctl enable rke2-server.service
sudo systemctl start rke2-server.service

# Wait for startup
sudo journalctl -u rke2-server -f

# Setup kubectl
sudo cp /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/
mkdir -p ~/.kube
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

# Verify cluster
kubectl get nodes
kubectl get pods -A
```
#######To upgrade the existing RKE2 setup
# curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=v1.33.1+rke2r1 sh -
# systemctl restart rke2-server.service
# systemctl restart rke2-agent.service

---

### Step 5: Add Worker Nodes

Get the node token from **fratelli** first:

```bash
# On fratelli, get the node token
sudo cat /var/lib/rancher/rke2/server/node-token
```

Copy the token output and use it in the worker node configurations below.

#### 5.1 Standard Worker Nodes (pane, cicis, cesar, papaj)

Execute on **pane, cicis, cesar, and papaj**:

```bash
# Create RKE2 agent configuration directory
sudo mkdir -p /etc/rancher/rke2

# Create worker node configuration
# IMPORTANT: Replace <YOUR_NODE_TOKEN> with the actual token from fratelli
# IMPORTANT: Replace <NODE_NAME> with the actual hostname (pane, cicis, cesar, or papaj)

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

#### 5.2 High-Capacity Worker Node (grimaldi)

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
  - "max-pods=250"
  - "system-reserved=cpu=4,memory=16Gi"
  - "kube-reserved=cpu=2,memory=8Gi"
  - "eviction-hard=memory.available<5%,nodefs.available<10%"

# Node labels for workload scheduling
node-label:
  - "node.antbrains.com/capacity=high"
  - "node.antbrains.com/cpu=80"
  - "node.antbrains.com/memory=640"
  - "node.antbrains.com/storage=large"
EOF

# Install RKE2 agent
curl -sfL https://get.rke2.io | sudo INSTALL_RKE2_VERSION="v1.33.1+rke2r1" INSTALL_RKE2_TYPE="agent" sh -

# Enable and start RKE2 agent
sudo systemctl enable rke2-agent.service
sudo systemctl start rke2-agent.service

# Monitor startup logs
sudo journalctl -u rke2-agent -f
```

#### 5.3 Verify Cluster

From **fratelli**, verify all nodes have joined:

```bash
# Check all nodes
kubectl get nodes -o wide

# Expected output: 6 nodes (1 control plane + 5 workers)
# fratelli - Ready - control-plane,master
# pane     - Ready - <none>
# cicis    - Ready - <none>
# cesar    - Ready - <none>
# papaj    - Ready - <none>
# grimaldi - Ready - <none>

# Check node labels
kubectl get nodes --show-labels

# Check all system pods
kubectl get pods -A

# Check cluster info
kubectl cluster-info
```

---

## Rancher Installation
### Step 6: Install Cert-Manager

```bash
# Add Jetstack Helm repository
curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
sudo apt update
sudo apt install helm

# Add cert-manager repository
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Create namespace
kubectl create namespace cert-manager

# Install cert-manager
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.13.2 \
  --set installCRDs=true

#New Version available need to check with implementation.
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.18.0 \
  --set crds.enabled=true

# Verify installation
kubectl get pods --namespace cert-manager
```

### Step 7: Install Rancher

```bash
# Add Rancher Helm repository
helm repo add rancher-stable https://releases.rancher.com/server-charts/stable
helm repo update

# Create namespace
kubectl create namespace cattle-system

# Install Rancher with Let's Encrypt
helm install rancher rancher-stable/rancher \
  --namespace cattle-system \
  --set hostname=rancher-uslab.antbrains.com \
  --set bootstrapPassword=admin123 \
  --set ingress.tls.source=letsEncrypt \
  --set letsEncrypt.email=kiran.mane@kristasoft.com \
  --set letsEncrypt.environment=prod

# Wait for deployment
kubectl -n cattle-system rollout status deploy/rancher

# Get Rancher URL
echo "Rancher URL: https://rancher-uslab.antbrains.com"
echo "Bootstrap Password: admin"
```


#########################################################################################################################################################################################################################################
# Ceph Storage Implementation for RKE2 Cluster

## Overview

This guide will replace the Longhorn storage setup with Ceph using Rook-Ceph operator. Rook simplifies Ceph deployment and management in Kubernetes environments.

## Prerequisites Check

Before proceeding, ensure your nodes meet these requirements:

```bash
# Run on ALL nodes to verify prerequisites
# Check available disks (should have unused disks/partitions for Ceph)
lsblk -f

# Check if LVM is available
which lvm

# Install required packages if missing
sudo apt update
sudo apt install -y lvm2 cryptsetup

# Verify raw devices (unformatted disks for Ceph OSDs)
lsblk | grep -v "part\|lvm" | grep disk
```

## Step 2: Prepare Nodes for Ceph

```bash
# Run on ALL nodes
# Clean any existing Ceph installations (if any)
sudo rm -rf /var/lib/rook
sudo rm -rf /var/lib/ceph

# Create directories for Ceph
sudo mkdir -p /var/lib/rook

# If using specific disks, clean them (REPLACE /dev/sdX with your actual disk)
# WARNING: This will destroy all data on the disk
sudo wipefs -a /dev/sdb
sudo dd if=/dev/zero of=/dev/sdb bs=1M count=100

# For high-capacity servers, you might want to use multiple disks
# Example for servers with multiple NVMe drives:
sudo wipefs -a /dev/nvme1n1
sudo wipefs -a /dev/nvme2n1
```

## Step 2: Install Rook-Ceph Operator
URL:- Followed:- 
https://rook.io/docs/rook/latest-release/Helm-Charts/helm-charts/

```bash
# Install Rook Operator
helm repo add rook-release https://charts.rook.io/release

curl -L -o values.yaml https://raw.githubusercontent.com/rook/rook/refs/heads/release-1.17/deploy/charts/rook-ceph/values.yaml

helm install --create-namespace --namespace rook-ceph rook-ceph rook-release/rook-ceph -f values.yaml

# Verify operator installation
kubectl -n rook-ceph get pod -l app=rook-ceph-operator

# Wait for operator to be ready
kubectl -n rook-ceph wait --for=condition=Ready pod -l app=rook-ceph-operator --timeout=300s

## After oprater is running install rook-ceph-cluster
curl -L -o cluster-values.yaml https://raw.githubusercontent.com/rook/rook/refs/tags/v1.17.4/deploy/charts/rook-ceph-cluster/values.yaml
#### Modify cluster-values.yaml as per your requirement.
helm repo add rook-release https://charts.rook.io/release

helm install --create-namespace --namespace rook-ceph rook-ceph-cluster \
   --set operatorNamespace=rook-ceph rook-release/rook-ceph-cluster -f cluster-values.yaml

#Tune ceph cluster
bash-5.1$ ceph config set osd osd_memory_target 4294967296
bash-5.1$ ceph config set osd osd_max_backfills 4
bash-5.1$ ceph config set osd osd_recovery_max_active 8
bash-5.1$ ceph config set global osd_pool_default_size 3
bash-5.1$ ceph config set global osd_pool_default_min_size 2
bash-5.1$ ceph balancer on
bash-5.1$ ceph balancer mode upmap
bash-5.1$ ceph pg stat
81 pgs: 81 active+clean; 196 GiB data, 586 GiB used, 1.4 TiB / 2.0 TiB avail; 852 B/s rd, 25 KiB/s wr, 5 op/s
bash-5.1$ ceph osd tree