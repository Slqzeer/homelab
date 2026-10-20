# Qdrant

Qdrant 1.19.2 runs as one persistent StatefulSet in `databases`. It exposes
REST on 6333 and gRPC on 6334 at `qdrant.taildf6cd4.ts.net` through a
Tailscale layer-3 `LoadBalancer` Service. The single-node cluster protocol on
6335 is not exposed.

## Storage and availability

The `data-qdrant-0` local-path PVC requests 20 GiB. Local-path capacity is
nominal and the data remains tied to this node. The StatefulSet has one
replica, so upgrades and node outages interrupt service.

Qdrant snapshots are written below `/qdrant/snapshots` on the same PVC. They
are useful restore points but are not backups until copied to another device.
No scheduled off-node backup exists yet; do not treat this instance as the
only copy of irreplaceable vectors.

## Authentication and connecting

The Vault configure ceremony creates `homelab/qdrant` with a random admin API
key. VSO projects only `api-key` into `qdrant-credentials` and restarts the
StatefulSet after rotation. REST clients send it as the `api-key` header;
gRPC clients use the same key through their SDK's API-key option.

Run `platform/vault/configure-vault.sh` through the ceremony documented in
`platform/vault-secrets-operator/README.md` before Qdrant first reconciles.
Without it, the `vso-qdrant` role and `homelab/qdrant` path do not exist, VSO
cannot create `qdrant-credentials`, and the pod remains pending.

From a tailnet device:

```text
REST: http://qdrant.taildf6cd4.ts.net:6333
gRPC: qdrant.taildf6cd4.ts.net:6334
```

Tailscale encrypts the client-to-ProxyGroup path. The final hop from the
ProxyGroup pod to Qdrant stays inside the single-node cluster and is plain
TCP. Do not expose either Service outside the tailnet.

To verify without printing the key, write a temporary curl header file with
mode 0600, use `curl -H @file`, and remove it afterwards. A successful request
to `/collections` returns JSON; the unauthenticated request must be rejected.

## Rotation

Update `homelab/qdrant` key `api-key` in Vault. Within five minutes VSO
updates the Kubernetes Secret and restarts Qdrant. Update clients immediately:
there is no overlap window in this simple admin-key configuration.

## Recovery

Restore the retained PVC before the Application reconciles if the node is
being rebuilt. For a Qdrant snapshot restore, use the procedure matching the
running Qdrant minor version; snapshots are compatible only with the same or
next minor version. Verify `/readyz`, then authenticate and list collections
before allowing writers back in.

Static validation and an Argo CD `Healthy` result do not prove tailnet routing,
Vault authentication, collection contents, or restore viability. Those need
live checks after reconciliation.
