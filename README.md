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
| `apps/` | Currently unused; reserved for per-application values/manifests, not Application objects |

## Adding a component

Commit an Application to `environments/homelab/apps/` and push. The root
Application picks it up; nothing is applied by hand. Order components with the
`argocd.argoproj.io/sync-wave` annotation: infrastructure 1-5, platform 10,
apps 20.

Every Application object belongs in `environments/homelab/apps/` — `root.yaml`
recurses only that directory. The top-level `apps/` directory is a different
path and is not watched by anything: an Application placed there is never
applied, and there is no error. It stays out of Argo CD entirely, so nothing
reports it as missing or out of sync — the component simply never appears.

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

To genuinely retire a component, the finalizer must be in place **before**
the Application's file is removed from git. Doing it the other way around
loses the race: while the file is still in git, root's `selfHeal` notices
the child Application is gone and recreates it — with automated sync — the
instant anything deletes it, redeploying the very resources the cascade was
supposed to remove. Where the cascade already deleted a PVC, local-path has
reclaimed the volume, so the recreated Application provisions a fresh
**empty** one. There is no window in the correct order below for that to
happen, because nothing is ever deleted by hand:

1. **Check for existing finalizers, then add the cascade finalizer.** A
   `merge` patch *replaces* the whole `finalizers` array rather than
   appending to it, so confirm what is already there first — this repo's
   Applications carry none, but that could change:

   ```bash
   kubectl -n argocd get application <name> -o jsonpath='{.metadata.finalizers}'
   ```

   If that prints nothing, patch in just the cascade finalizer:

   ```bash
   kubectl -n argocd patch application <name> --type merge \
     -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'
   ```

   If it printed existing entries, include them in the array alongside
   `resources-finalizer.argocd.argoproj.io` — the merge patch above would
   otherwise silently drop them.

2. **Remove the Application's file from `environments/homelab/apps/` and
   push.** This is the only delete trigger in the whole procedure.

3. **Let root prune it.** Root's `prune: true` deletes the child
   Application object; because it now carries the cascade finalizer, that
   deletion cascades to every resource the child Application manages
   (Deployments, Services, Secrets, PVCs). This happens automatically —
   there is nothing to run by hand, and so no window for `selfHeal` to
   recreate anything.

4. **Verify the cascade actually completed** — do not assume it did:

   ```bash
   kubectl -n argocd get application <name>
   kubectl get all -n <component-namespace>
   ```

   The first should return `NotFound`; the second should be empty (or gone,
   if the namespace itself was retired too).

The Argo CD CLI's `argocd app delete <name> --cascade` injects the same
finalizer for you, but it only avoids the race if the Application's file has
already been removed from git and pushed *before* running it — run it while
the file is still present and it has the identical selfHeal race described
above. The `argocd` CLI is not installed on this host, so the `kubectl`
sequence above is the primary route here regardless.

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

Log in as `admin`. The initial password is in the `argocd-initial-admin-secret`
Secret in the `argocd` namespace:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
```

That Secret is deleted after the first password change, so this only works
until then.

## Recovery

If a bad commit breaks Argo CD itself, the fix must happen in this order —
running the bootstrap script against a bad checkout, or against `main`
before it is fixed, reinstates the same breakage:

1. **Revert the bad commit in git and push it to `main` first.** The
   `argocd` Application targets `main` with `selfHeal: true`, so as long as
   `main` still holds the bad values, a freshly reinstalled Argo CD
   reconciles against `main` immediately and breaks itself again.
2. **Then, from a working tree checked out at that reverted revision, run:**

   ```bash
   ./bootstrap/argocd/bootstrap.sh
   ```

   The script always reads `values.yaml` from the working tree it is run
   in, never from git history — running it from a checkout still sitting on
   the bad commit reinstalls the bad values regardless of what has been
   pushed.

## First install / rebuild

1. Run `./bootstrap/argocd/bootstrap.sh`. This creates the `argocd`
   namespace and installs Argo CD from the pinned chart. It needs no
   repository credential — it pulls the chart over HTTPS.
2. Create the `repo-homelab` deploy-key Secret in the `argocd` namespace.
   See `docs/superpowers/plans/2026-09-01-k3s-argocd-bootstrap.md`, Task 5,
   for regenerating the key and the exact commands. This Secret exists only
   in the cluster; it is not reproducible from anything in this repository.
3. `kubectl apply -f environments/homelab/root.yaml` — the one and only
   manual apply.

The order matters: step 2 must follow step 1, because the `argocd`
namespace does not exist until `bootstrap.sh` creates it. The credential
is needed only before step 3, which is the first thing that clones this
repository.

## Documentation

- `docs/workstation-plan.md` — overall roadmap
- `docs/troubleshooting.md` — faults hit on this cluster and how they were
  diagnosed. Read entry 1 before debugging any pod that fails to start.
- `docs/superpowers/specs/` — design documents
- `docs/superpowers/plans/` — implementation plans
