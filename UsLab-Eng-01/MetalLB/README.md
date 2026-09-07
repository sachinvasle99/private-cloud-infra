# MetalLB — UsLab-Eng-01

Gives `Service type=LoadBalancer` real IPs on the LAN. In the **initial stage**
there is no ingress controller — MetalLB + **Nginx Proxy Manager** (see
`../NginxProxyManager/`) handle all north-south traffic. (Traefik/cert-manager
ingress is deferred to Phase 2, see `../Traefik.phase2/`.)

## Why MetalLB (not the keepalived VIP)

`roso` still runs HAProxy + keepalived for the **kube-API** VIP (control-plane
HA path). MetalLB is for **application** LoadBalancer services and is what makes
`type: LoadBalancer` actually allocate an IP on bare metal. NPM consumes one.

Keep the two IP spaces separate:
- keepalived API VIP — one IP (e.g. `10.35.0.100`).
- MetalLB pool — a small range (e.g. `10.35.0.200-10.35.0.210`), **outside DHCP**,
  not overlapping node IPs or the API VIP.

## Install (L2 mode)

```bash
helm repo add metallb https://metallb.github.io/metallb && helm repo update
kubectl create namespace metallb-system
helm install metallb metallb/metallb -n metallb-system
kubectl -n metallb-system rollout status deploy/metallb-controller
kubectl -n metallb-system get pod          # controller + one speaker per node

# Configure the pool (edit <METALLB_POOL> first):
kubectl apply -f UsLab-Eng-01/MetalLB/metallb-pools.yaml
kubectl -n metallb-system get ipaddresspool,l2advertisement
```

## Verify

```bash
# A LoadBalancer service should now get an EXTERNAL-IP from the pool.
kubectl get svc -A | grep LoadBalancer
# Ping the assigned IP from another LAN host; arping should answer from one node.
```

## Files

| File | Purpose |
|------|---------|
| `metallb-pools.yaml` | `IPAddressPool` (`<METALLB_POOL>`) + `L2Advertisement`. |
