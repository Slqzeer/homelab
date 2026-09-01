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

Argo CD only cascades a deletion when the Application carries the
`resources-finalizer.argocd.argoproj.io` finalizer. This repository's
Applications deliberately do not carry it (see above for why `root` never
should), so a plain `kubectl -n argocd delete application <name>` deletes
only the Application object and leaves its managed resources orphaned —
the same outcome as pruning via a file removal, just triggered by hand
instead of by root.

To genuinely retire a component, add the finalizer to that child
Application, delete it, and only then remove its file:

```bash
kubectl -n argocd patch application <name> --type merge \
  -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'
kubectl -n argocd delete application <name>
```

Then remove the Application's file from `environments/homelab/apps/` and
push. Order matters: patch and delete the child first, then remove the file.
If the file is removed first, root prunes the Application object before the
cascade can happen, and there is nothing left to delete by hand.

The Argo CD CLI's `argocd app delete <name> --cascade` achieves the same
thing in one step by injecting that finalizer for you, but the `argocd` CLI
is not installed on this host, so the `kubectl patch` + `kubectl delete`
sequence above is the primary route here.

Never add this finalizer to `root` itself — that would reintroduce the
exact cascade `root` is designed to avoid.

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
