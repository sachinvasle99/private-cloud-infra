# Developer cluster access — UsLab-Eng-01

Implements the **"developers"** access tier from `../../ACCESS-STRATEGY.md`:
one shared developer group, **deploy + debug** rights, **read-only on secrets**,
scoped to the Krista **app namespaces** only. No cluster-admin, no node SSH,
no RBAC/namespace/quota editing.

## Live state (as deployed)

- **Shared user:** `Developer` (Rancher local user, id `m-7cj6d`). One shared login for the dev team.
- **Project:** `Developers` (`local:p-8mhj8`) — members: `kme-service`, `krista-mcp-server`,
  `krista-auth-server`, `uslab-eng-01`, `smoke-test`. (`global-service` is NOT a member.)
- **Role:** `Developer` holds **only** `developer-deploy-debug` on the project — verified:
  deploy/exec/logs/read-secrets = yes; delete & secret-write & nodes & out-of-project = no.
- **Kubeconfig:** `developer-kubeconfig.yaml` (this folder) — Rancher-proxied, scoped to the above.
  ⚠️ Contains a live token — **git-ignored, do not commit/paste**. Hand to devs over a secure channel.
- **Login:** username `Developer`, password set out-of-band (rotate in Rancher UI →
  Users & Authentication → Users). Devs can also self-download a kubeconfig after logging into Rancher.

> Do NOT add the `Developer` user as project **Owner/Member** in the UI — the Owner role grants
> full admin and overrides this scoped role. Assign only "Developer (deploy & debug, no secret write)".

## Files

| File | What it is | Applied? |
|------|------------|----------|
| `developer-clusterrole.yaml` | Native `ClusterRole/developer` — the canonical permission set (used per-namespace via RoleBindings). | ✅ applied |
| `developer-rancher-roletemplate.yaml` | Rancher `RoleTemplate` with the **same** rules — the assignable "Developer (deploy & debug, no secret write)" role in the Rancher UI. | ✅ applied |
| `developer-rolebindings.yaml` | Native `RoleBinding`s granting the role to **Group `developers`** in each app namespace. | ⏸ apply only with the native/OIDC path (see below) |

Two files, one permission set — **keep their rules in sync** if you edit either.

## Which path to use

This is a **Rancher-managed** cluster, so there are two ways a developer can end
up with this access. Pick based on how developers get their kubeconfig.

### Path A — Rancher (matches our chosen "Rancher-managed" identity) ✅ recommended

Rancher authenticates the user and issues their kubeconfig; access is governed by
Rancher bindings, so use the **RoleTemplate**, not the native RoleBindings.

1. **Group the app namespaces into a Project** — ✅ DONE. Project **"Developers"**
   (`local:p-8mhj8`) now contains `global-service`, `kme-service`,
   `krista-mcp-server`, `uslab-eng-01`, `smoke-test`. `krista-auth-server` is
   intentionally excluded (holds auth material).
2. **Add members with the Developer role:** Project **Developers** → Members →
   Add → pick the user (or, once an IdP is wired, the **group**) → Role =
   **"Developer (deploy & debug, no secret write)"**.
3. Developer logs into Rancher → **Download kubeconfig** → `kubectl` works, scoped
   to those namespaces only.

> The "shared developers group" is a real directory group only once an external
> IdP is connected (Rancher → Auth Provider → OIDC/GitHub/AD, map group
> `developers`). Until then, add developers as individual project members with
> the same role — functionally identical, just managed per-user.

### Path B — Native RBAC (no Rancher in the loop)

For kubeconfigs that already carry the **`developers`** group — i.e. an IdP wired
directly to the kube-apiserver (`--oidc-groups-claim`), or per-developer
ServiceAccount-token / client-cert kubeconfigs I mint with that group:

```bash
export KUBECONFIG=/Users/kiranmane/Documents/uslab-Eng-01.yaml
kubectl apply -f developer-rolebindings.yaml
```

`krista-auth-server` is **excluded** (commented out) — it holds auth/identity
material; enable it only if developers genuinely need it.

## Verify the role does what we intend

```bash
NS=global-service
# should be yes:
kubectl auth can-i create deployments  -n $NS --as=dev --as-group=developers
kubectl auth can-i get    pods/log     -n $NS --as=dev --as-group=developers
kubectl auth can-i create pods/exec    -n $NS --as=dev --as-group=developers
# should be NO:
kubectl auth can-i create secrets      -n $NS --as=dev --as-group=developers
kubectl auth can-i delete networkpolicies -n $NS --as=dev --as-group=developers
kubectl auth can-i get    nodes               --as=dev --as-group=developers
kubectl auth can-i '*'    '*'          -n kube-system --as=dev --as-group=developers
```

## What developers CAN and CANNOT do

| Can | Cannot |
|-----|--------|
| **Create / update / patch** Deployments, StatefulSets, DaemonSets, Jobs/CronJobs, Services, Ingress, ConfigMaps, PVCs, HPAs, PDBs | **Delete** any of those resources (no tear-down; no PVC data loss) |
| Manage **Pods** incl. delete (restart a stuck pod) | **Write Secrets** (read-only), edit ServiceAccounts |
| Read logs, `exec`, `port-forward`, `kubectl debug` | Edit **NetworkPolicies**, ResourceQuotas, LimitRanges |
| Work only in the assigned app namespaces | Touch system namespaces, nodes, RBAC, CRDs, PVs |
