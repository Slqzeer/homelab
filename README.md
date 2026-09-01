# homelab

GitOps configuration for a single-node k3s homelab. Argo CD reconciles this
repository into the cluster; `environments/homelab/root.yaml` is the only
manifest ever applied by hand.

## Layout

| Path | Contents |
| --- | --- |
| `bootstrap/` | Argo CD values and the install/recovery script; namespaces |
| `environments/homelab/` | Root Application and one Application per component |
| `infrastructure/` | Storage, ingress, cert-manager, networking |
| `platform/` | Vault, databases, registry |
| `observability/` | Prometheus, Grafana, logging |
| `apps/` | Applications pointing at external application repositories |

## Adding a component

Commit an Application to `environments/homelab/apps/` and push. The root
Application picks it up; nothing is applied by hand. Order components with the
`argocd.argoproj.io/sync-wave` annotation: infrastructure 1-5, platform 10,
apps 20.

## Removing a component

The root Application deliberately carries no
`resources-finalizer.argocd.argoproj.io` finalizer. That is a deliberate
trade-off, not an oversight: it means deleting `root` itself can never cascade
into tearing down every component it manages.

The consequence is an asymmetry when retiring a single component. Deleting
its Application file from `environments/homelab/apps/` and pushing causes
root's `prune: true` to remove only the child Application object. The
Kubernetes resources that child Application was managing — Deployments,
Services, Secrets, PVCs — are **not** cascade-deleted and are silently
orphaned in the cluster. Removing the file is not the same as cleaning up the
cluster.

To fully retire a component, delete its Application with cascade before or
instead of merely removing the file:

```bash
kubectl -n argocd delete application <name>
```

`kubectl delete` on an Application cascades to its managed resources by
default. Do this first (or alongside removing the file), not after — once the
file is gone and root has pruned the Application object, there is nothing
left in git to tell Argo CD what to clean up.

## Application repositories

An application's own repository owns its Dockerfile, CI, and deploy manifests.
This repository holds only an Application pointing at it, selecting a branch,
tag, or commit via `targetRevision`.

Each application repository must **pin its concrete image tag inside its own
manifests at that revision**. A revision whose manifests say `:latest` pins the
manifests but not the image, and the deployment drifts.

## Access

```bash
kubectl port-forward -n argocd svc/argocd-server 8080:80
```

Ingress, DNS, and TLS are not yet configured.

## Recovery

If a bad commit breaks Argo CD itself, reinstall it from the last good commit:

```bash
./bootstrap/argocd/bootstrap.sh
```

## Documentation

- `docs/workstation-plan.md` — overall roadmap
- `docs/superpowers/specs/` — design documents
