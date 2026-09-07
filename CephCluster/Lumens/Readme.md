# Camond to use

# Cluster Update / Upgrade

helm upgrade --namespace rook-ceph rook-ceph-cluster \                                           
    rook-release/rook-ceph-cluster \
    --version v1.19.5 \
    --set operatorNamespace=rook-ceph \
    -f ##Path to File cluster-values.yaml ##

# Operator Upgrade

helm upgrade --namespace rook-ceph rook-ceph \           
    rook-release/rook-ceph \        
    --version v1.19.5 \
    -f ## Path To values.yaml ##