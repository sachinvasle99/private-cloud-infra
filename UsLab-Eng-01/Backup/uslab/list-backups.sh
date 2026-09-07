#!/usr/bin/env bash
# List every available backup VERSION (restore point) in uslab-eng-01-backup,
# so you can pick one to restore.
#   - File backups (restic): dated snapshots with IDs  -> restore by ID or restoreAsOf
#   - Postgres dumps: dated .sql.gz objects             -> restore the chosen file
#
#   KUBECONFIG=/path/uslab-Eng-01.yaml ./list-backups.sh
set -euo pipefail
NS=uslab-eng-01

echo "##################################################################"
echo "#  FILE BACKUPS (restic) — dated snapshots = restore points       #"
echo "##################################################################"
for pair in \
  "krista-ai:restic-krista-ai" \
  "shared-mediadb:restic-mediadb" \
  "elasticsearch:restic-elasticsearch" \
  "node1-accesspoint:restic-accesspoint-node1" \
  "node2-accesspoint:restic-accesspoint-node2" ; do
  name=${pair%%:*}; secret=${pair##*:}
  echo; echo "=== $name (secret: $secret) ==="
  kubectl -n "$NS" delete job lsbk --ignore-not-found >/dev/null 2>&1
  cat <<EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: batch/v1
kind: Job
metadata: { name: lsbk, namespace: $NS }
spec:
  ttlSecondsAfterFinished: 60
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: s
          image: restic/restic:latest
          args: ["snapshots","--insecure-tls"]
          envFrom: [{ secretRef: { name: $secret } }]
EOF
  until [ -n "$(kubectl -n "$NS" get job lsbk -o jsonpath='{.status.succeeded}{.status.failed}' 2>/dev/null)" ]; do sleep 3; done
  kubectl -n "$NS" logs job/lsbk 2>&1 | grep -E '^[0-9a-f]{8}|^ID|^---' || echo "  (no snapshots yet)"
  kubectl -n "$NS" delete job lsbk --ignore-not-found >/dev/null 2>&1
done

echo
echo "##################################################################"
echo "#  POSTGRES DUMPS — dated .sql.gz objects (browsable in MinIO)    #"
echo "##################################################################"
kubectl -n "$NS" delete pod lsbk-mc --ignore-not-found >/dev/null 2>&1
kubectl -n "$NS" run lsbk-mc --image=minio/mc:latest --restart=Never --command -- sh -c \
 'mc alias set bk https://10.35.0.36:9000 cgyOLlOA8AtDDXLb8pnJ mmCrbFSIB4F3F6vnPjx5ULzNHuWw8OP3w9FPva20 --insecure >/dev/null 2>&1
  mc ls --recursive --insecure bk/uslab-eng-01-backup/pgdump/' >/dev/null 2>&1
until [ "$(kubectl -n "$NS" get pod lsbk-mc -o jsonpath='{.status.phase}' 2>/dev/null)" = "Succeeded" ] || [ "$(kubectl -n "$NS" get pod lsbk-mc -o jsonpath='{.status.phase}' 2>/dev/null)" = "Failed" ]; do sleep 3; done
kubectl -n "$NS" logs lsbk-mc 2>&1 | grep -vE '^\s*$'
kubectl -n "$NS" delete pod lsbk-mc --ignore-not-found >/dev/null 2>&1

echo
echo "TO RESTORE:"
echo "  file backup  -> VolSync ReplicationDestination, restic.restoreAsOf: '<date>'  (or restore by snapshot ID)"
echo "  postgres     -> mc cp the chosen dated .sql.gz, gunzip, psql -f into the instance"
