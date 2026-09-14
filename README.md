# homelab

GitOps configuration for a single-node k3s homelab. Argo CD reconciles this
repository into the cluster; `environments/homelab/root.yaml` is the only
manifest ever applied by hand.

## Layout

| Path | Contents |
| --- | --- |
| `bootstrap/` | Argo CD values and the install/recovery script; namespaces |
| `environments/homelab/` | Root Application and one Application per component |
| `infrastructure/ingress/` | Tailscale operator values and the Ingress manifests it serves |
| `infrastructure/networking/` | Tailnet ACL policy — **not** reconciled by Argo CD |
| `infrastructure/storage/` | PVC storage notes |
| `infrastructure/cert-manager/` | Empty; deferred, see the 2026-09-02 spec |
| `platform/` | Vault, databases, registry |
| `platform/vault/` | HashiCorp Vault: Helm values, unsealer manifest, init/backup docs |
| `platform/vault-secrets-operator/` | Vault Secrets Operator: Helm values, `VaultConnection`/`VaultAuth`/`VaultStaticSecret` manifests |
| `platform/databases/postgres/` | PostgreSQL: StatefulSet, PVC, its own Vault-Secrets-Operator wiring, README |
| `observability/` | Prometheus, Grafana, logging |
| `apps/` | Currently unused; reserved for per-application values/manifests, not Application objects |

## Adding a component

Commit an Application to `environments/homelab/apps/` and push. The root
Application picks it up; nothing is applied by hand. Order components with the
`argocd.argoproj.io/sync-wave` annotation: infrastructure 0-2, platform 10
(`vault`), apps 20, `ingress-config` and `vso-operator` sharing wave 21
deliberately (see below for why `vso-operator` is not right after `vault`),
`vso-config` at 22, and `postgres` last of all at 23 — it cannot start
without the Secret `vso-config` creates, so it has to come after it, and
nothing in this cluster yet depends on Postgres, so nothing is gated by
putting it last. Platform (10) gates apps (20) and wave 21 the same way
infrastructure gates platform — see below for what that means on a rebuild.

Every Application object belongs in `environments/homelab/apps/` — `root.yaml`
recurses only that directory. The top-level `apps/` directory is a different
path and is not watched by anything: an Application placed there is never
applied, and there is no error. It stays out of Argo CD entirely, so nothing
reports it as missing or out of sync — the component simply never appears.

### A new component can also "never appear" for a reason with nothing in its own manifest

By Argo CD's wave semantics, a wave gates every later wave — nothing at a
later wave starts syncing until every earlier wave is `Healthy`. Before
phase 16, `environments/homelab/apps/ingress-config.yaml` sat at sync-wave
3, early enough to gate the platform (10) and apps (20) tiers. It depends on
cluster-only state (the `operator-oauth` Secret, the OAuth client, Let's
Encrypt) and an Ingress is `Progressing`, not `Healthy`, until the Tailscale
control plane finishes provisioning it — so any ingress trouble at all
silently blocked every component behind it, with nothing in the blocked
component's own Application object naming ingress as the cause.

Phase 16 removed that coupling: `ingress-config` now sits at wave 21,
deliberately after everything else, specifically so that Vault at wave 10
could not be blocked by it. Nothing in this repository now waits on ingress
health, so ingress is no longer the likely culprit for a component that
never appears.

The coupling was moved, not eliminated — **Vault at wave 10 is now the
early gate**, ahead of both apps (20) and `ingress-config` (21), and it
depends on hand-created cluster-only state the same way ingress did (the
`vault-unseal-keys` Secret, created by a human running the init ceremony —
see `platform/vault/README.md`). It is a stricter gate in one respect:
ingress could eventually recover on its own once its dependencies came
back, but a perfect rebuild still needs a human to run the ceremony before
Vault ever goes `Healthy`. If `vault-unseal-keys` is deleted, or Vault
otherwise stays sealed, the `vault` Application freezes at `Progressing`,
and `ingress-config` freezes with it — any future change to an Ingress or
the ProxyClass silently stops being applied until Vault is unsealed again.

The general lesson still holds, and is worth keeping regardless of which
Application happens to sit early: if a newly committed component never
appears, the first thing to check is not that component — it is the overall
wave picture:

```bash
sg k3s-admin -c 'kubectl -n argocd get applications'
```

Anything at or before your component's wave that is not `Synced`/`Healthy`
is blocking it. See the design spec's §6 for the full wave table and why
`ingress-config` moved.

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

Argo CD is at **<https://argocd.taildf6cd4.ts.net>** from any device on
the tailnet. The certificate is a real Let's Encrypt certificate issued
by Tailscale, so no CA needs installing anywhere.

Vault is at **<https://vault.taildf6cd4.ts.net>**, same tailnet, same
certificate arrangement. See `platform/vault/README.md` for the init
ceremony — Vault comes up sealed and uninitialized until that runs once.

Log in as `admin`. The initial-password Secret was deleted after the
first password change; there is no recovery path from the cluster, so the
password must be kept in a password manager.

If Tailscale itself is unavailable, the port-forward still works:

```bash
kubectl port-forward -n argocd svc/argocd-server 8080:80
```

then <http://localhost:8080> — plain HTTP, no TLS.

Ingress is Tailscale-only; there is no LAN hostname and no `.home.arpa`.
See `infrastructure/ingress/README.md` to expose another service.

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

### Vault: snapshots and keys are both required

A Vault Raft snapshot is encrypted with Vault's master key. Restoring one
into a fresh Vault requires the same unseal keys. Snapshots live on disk at
`/backups/vault`; the unseal keys live only in a password manager. Neither
is sufficient alone, and that separation is deliberate — keeping both in one
place would create a single point of total loss and total compromise at
once. See `platform/vault/README.md`.

## First install / rebuild

1. **Enable HTTPS Certificates on the tailnet first**: Tailscale admin
   console → DNS → HTTPS Certificates → Enable. Without this, no
   certificate is ever issued for any Ingress — the Ingress still comes up
   and gets a hostname, but every browser hitting it gets a TLS error, with
   nothing in the cluster or in Argo CD naming HTTPS Certificates as the
   cause. This is a tailnet-wide setting, done once in the admin console,
   not a Kubernetes object; it does not live in this repository and a
   rebuild does not recreate it. See `infrastructure/ingress/README.md` for
   how to verify it is on.
2. Run `./bootstrap/argocd/bootstrap.sh`. This creates the `argocd`
   namespace and installs Argo CD from the pinned chart. It needs no
   repository credential — it pulls the chart over HTTPS.
3. Create the `repo-homelab` deploy-key Secret in the `argocd` namespace.
   See `docs/superpowers/plans/2026-09-01-k3s-argocd-bootstrap.md`, Task 5,
   for regenerating the key and the exact commands. This Secret exists only
   in the cluster; it is not reproducible from anything in this repository.
4. `kubectl apply -f environments/homelab/root.yaml` — the one and only
   manual apply.
5. Create the `operator-oauth` Secret in the `tailscale` namespace once
   root has created that namespace at sync-wave 0. Until it exists the
   Tailscale operator stays in `ContainerCreating` and no tailnet
   hostname resolves. See `infrastructure/ingress/README.md`. Argo CD
   itself is reachable by port-forward throughout, so this does not
   block recovery.
6. Run the Vault init ceremony — see `platform/vault/README.md`. **This is
   not optional on a rebuild, and it now has two halves: unseal, then
   configure.** Vault sits at sync-wave 10, and Argo CD does not advance a
   wave whose resources are not `Healthy`. A freshly deployed Vault comes up
   sealed, with a failing readiness probe, so the `vault` Application sits
   `Progressing` at wave 10 until the unseal half completes and
   `vault-unseal-keys` is created — and **nothing at wave 21 is applied
   until then**, including the ProxyClass and every Ingress, Argo CD's own
   included. Until unseal runs, no tailnet hostname resolves for anything
   and `kubectl port-forward` is the only route in. This is not a problem in
   practice: the ceremony itself needs `kubectl`, not a browser.

   The second half — running `platform/vault/configure-vault.sh`, see
   `platform/vault-secrets-operator/README.md` — has a different blast
   radius, and the contrast is the whole reason `vso-config` sits at
   sync-wave 22, after both `ingress-config` and its own operator,
   `vso-operator`, which share wave 21: skipping the configure half leaves
   `vso-config` `Progressing`/unhealthy, but because wave 22 sits after
   every Ingress, that failure does **not** cost any tailnet URL. The
   ceremony now also seeds PostgreSQL's credential (`homelab/postgres`), so
   the same skip leaves a second Application unhealthy too: `postgres` at
   wave 23 cannot start without the Secret `vso-config` creates one wave
   earlier. Both `vso-config` (22) and `postgres` (23) sit after
   `ingress-config` (21), so this still costs no tailnet URL — the same
   reasoning as for `vso-config` alone, just now covering two Applications
   instead of one. `vso-operator` itself was deliberately moved off wave 11
   (right after `vault`) for the same reason `ingress-config` sits at 21 —
   see the design spec's §6 and `environments/homelab/apps/vso-operator.yaml`
   for why a component whose own health says nothing about Vault should
   still not sit in front of every tailnet URL.

The order matters: step 3 must follow step 2, because the `argocd`
namespace does not exist until `bootstrap.sh` creates it. The credential
is needed only before step 4, which is the first thing that clones this
repository. Step 1 (HTTPS Certificates) has no ordering dependency on the
others — it is a tailnet setting, not a cluster step — but it must be done
before anyone relies on TLS working, so do it first and be done with it.
Step 6's unseal half has no fixed position either — it only needs Vault
deployed, which happens automatically at wave 10 — but until it runs,
everything from wave 21 on stays blocked, so do it as soon as `vault-0`
exists. The configure half can trail behind it without that same urgency,
precisely because wave 22 already sits after every Ingress.

## Documentation

- `docs/workstation-plan.md` — overall roadmap
- `docs/troubleshooting.md` — faults hit on this cluster and how they were
  diagnosed. Read entry 1 before debugging any pod that fails to start.
- `docs/superpowers/specs/` — design documents
- `docs/superpowers/plans/` — implementation plans
