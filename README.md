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
| `platform/` | Vault, databases, registry, Nexus |
| `platform/vault/` | HashiCorp Vault: Helm values, unsealer manifest, init/backup docs |
| `platform/vault-secrets-operator/` | Vault Secrets Operator: Helm values, `VaultConnection`/`VaultAuth`/`VaultStaticSecret` manifests |
| `platform/databases/postgres/` | PostgreSQL: StatefulSet, PVC, its own Vault-Secrets-Operator wiring, README |
| `platform/databases/redis/` | Redis: ephemeral cache, its own Vault-Secrets-Operator wiring, README |
| `platform/registry/` | GHCR pull credential: Vault Secrets Operator wiring, README covering issuing, seeding and rotating the token |
| `platform/nexus/` | Nexus Repository CE: manifests, the bootstrap Job that configures it over REST, the probed REST schemas, and the k3s `registries.yaml` that is **not** reconciled |
| `observability/` | Prometheus, Grafana, logging |
| `apps` namespace | Created by `bootstrap/namespaces/namespaces.yaml`; holds `beacon` and the GHCR pull Secret it consumes — **not** the same thing as the `apps/` directory below, despite the shared name |
| `apps/` | Currently unused; reserved for per-application values/manifests, not Application objects |
| `artifacts` namespace | Created by `bootstrap/namespaces/namespaces.yaml`; holds Nexus, its PVC and its admin Secret. See `platform/nexus/README.md` |

## Adding a component

Commit an Application to `environments/homelab/apps/` and push. The root
Application picks it up; nothing is applied by hand. Order components with the
`argocd.argoproj.io/sync-wave` annotation: infrastructure 0-2, platform 10
(`vault`), apps 20, `ingress-config` and `vso-operator` sharing wave 21
deliberately (see below for why `vso-operator` is not right after `vault`),
`vso-config` at 22, and wave 23 shared by `postgres`, `redis`, `registry`
and `nexus` — **not** because `vso-config` creates a Secret any of them
consumes (it does not: `postgres-credentials`, `redis-credentials`,
`ghcr-pull` and `nexus-admin` are each created by that component's own
`VaultStaticSecret`, shipped in its own Application at wave 23), but because
all four need the
VSO **operator** (`vso-operator`, wave 21) already running and Vault's
configure ceremony already run — the same two preconditions `vso-config`
itself depends on, which is why they naturally land after it rather than
because of it. Sharing the wave rather than stacking one behind another
lets them reconcile in parallel since none of the four depends on
another. Wave 23 is **not** last of all any more: `beacon` sits alone at
wave 24, one wave above, because it is the one component in this list with
a *real* dependency — its pod cannot pull its image until `registry` (23)
has created the `ghcr-pull` Secret it consumes. That is the exception the
rule below exists to describe, not a violation of it. The rule going
forward: a component that does not depend on Postgres, Redis, the
`ghcr-pull` credential, or anything else at wave 23 belongs at or below
23, not above it out of habit — `beacon` sits above it precisely because
it does. Platform (10) gates apps (20) and wave 21 the same way
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

The `beacon` repository is the worked example. Its CI publishes to GHCR and
then rewrites `deploy/kustomization.yaml` with the new `tag@digest` and
commits that back; Argo CD watches that branch, so the write-back commit *is*
the deploy. Pinning both tag and digest is deliberate: the tag keeps
`kubectl get pod` readable while the digest is what containerd resolves, and
kustomize renders them together as `name:tag@digest`.

Note that `beacon` is a canary, not an application. It exists to keep this
chain proved. Do not add features to it — build the real thing as its own
repository and leave the canary boring.

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
2. Set `beacon`'s repository-level Actions workflow permission to
   **Read and write**: `beacon` repository → Settings → Actions → General →
   Workflow permissions. GitHub's default for a new repository is
   **read-only**, and that setting is a *ceiling* — the write-back job's own
   `permissions: contents: write` (in `beacon/.github/workflows/ci.yaml`)
   cannot elevate past it. On a freshly created or rebuilt `beacon`
   repository left at the default, the write-back push is rejected and
   nothing ever deploys, no matter what the workflow file says. This is a
   GitHub repository setting, not a Kubernetes object or a file in either
   repository; a cluster rebuild does not touch it. See
   `/srv/projects/beacon/README.md`.
3. Create the GitHub Actions environment named `homelab` in **this**
   repository (Settings → Environments → New environment) and add two
   Environment secrets to it, not repository secrets: `TS_OAUTH_CLIENT_ID`
   and `TS_OAUTH_SECRET`, from an OAuth client with write access to the
   Policy File scope — see
   `docs/superpowers/plans/2026-09-16-github-actions-ghcr.md`, Task 2, for
   generating that client. Both jobs in `.github/workflows/tailscale-acl.yaml`
   declare `environment: homelab`; that declaration is what makes
   `secrets.*` resolve at all — an Environment secret is invisible to a job
   that does not declare its environment, even if the secret exists and is
   spelled correctly. This lives in GitHub, not the cluster, and a rebuild
   does not recreate it.
4. Run `./bootstrap/argocd/bootstrap.sh`. This creates the `argocd`
   namespace and installs Argo CD from the pinned chart. It needs no
   repository credential — it pulls the chart over HTTPS.
5. Create the `repo-homelab` deploy-key Secret in the `argocd` namespace.
   See `docs/superpowers/plans/2026-09-01-k3s-argocd-bootstrap.md`, Task 5,
   for regenerating the key and the exact commands. This Secret exists only
   in the cluster; it is not reproducible from anything in this repository.
6. Create the `repo-beacon` deploy-key Secret in the `argocd` namespace.
   Argo CD clones a **second** private repository — `beacon` — and the
   `repo-homelab` credential grants no access to it. Without this, the
   `beacon` Application does not fail with anything that names a missing
   Secret: the observed production error was
   `failed to list refs: error creating SSH agent: "SSH agent requested but
   SSH_AUTH_SOCK not-specified"` — a client-side SSH-agent message that gives
   no hint the real problem is a Secret that was never created. See
   `docs/superpowers/plans/2026-09-16-github-actions-ghcr.md`, Task 5,
   for the key generation and the exact commands. Read-only: Argo CD never
   writes, and a writable key here would let anything that compromised the
   cluster push to the repository that deploys into it.
7. `kubectl apply -f environments/homelab/root.yaml` — the one and only
   manual apply.
8. Create the `operator-oauth` Secret in the `tailscale` namespace once
   root has created that namespace at sync-wave 0. Until it exists the
   Tailscale operator stays in `ContainerCreating` and no tailnet
   hostname resolves. See `infrastructure/ingress/README.md`. Argo CD
   itself is reachable by port-forward throughout, so this does not
   block recovery.
9. Run the Vault init ceremony — see `platform/vault/README.md`. **This is
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
   `vso-config` `Synced`/`Healthy` regardless — Argo CD has no health
   assessment for any of the CRD kinds `vso-config` manages
   (`ServiceAccount`, `VaultConnection`, `VaultAuth`, `VaultStaticSecret`),
   so a dead credential path there never shows up as anything other than
   Healthy — but because wave 22 sits after every Ingress, that failure does
   **not** cost any tailnet URL regardless of how it's reported. The
   ceremony now also seeds PostgreSQL's and Redis's credentials
   (`homelab/postgres`, `homelab/redis`) and writes the `vso-ghcr-read`
   policy and `vso-ghcr` role that `registry`'s `VaultAuth` needs, so the
   same skip leaves `postgres` and `redis` genuinely `Progressing`/unhealthy
   — both Applications include a `StatefulSet`/`Deployment` Argo CD *does*
   assess, so a Secret that never mounts really does show up there — while
   `registry`, built entirely from the same unassessed CRD kinds as
   `vso-config`, again reports `Synced`/`Healthy` no matter what. None of the
   three depends on a Secret `vso-config` creates — each has its own
   `VaultStaticSecret` that reads its own path in Vault directly — but all
   three depend on the same two things `vso-config` itself depends on: the
   VSO operator running (wave 21) and this ceremony having populated Vault,
   so skipping it breaks all four for the same underlying reason, not
   because one creates something the others consume. `registry` has one
   further requirement this ceremony does not satisfy: the GHCR token
   itself, seeded by the next step, because GitHub issues that token rather
   than Vault generating it — running `configure-vault.sh` is necessary but
   not sufficient for `registry`'s credential path to actually work, and
   nothing in `kubectl -n argocd get applications` will say so either way.
   `vso-config` (22), `postgres`, `redis` and `registry` (23) all sit after
   `ingress-config` (21), so this still costs no tailnet URL — the same
   reasoning as for `vso-config` alone, just now covering four Applications
   instead of one.
   `vso-operator` itself was deliberately moved off wave 11
   (right after `vault`) for the same reason `ingress-config` sits at 21 —
   see the design spec's §6 and `environments/homelab/apps/vso-operator.yaml`
   for why a component whose own health says nothing about Vault should
   still not sit in front of every tailnet URL.
10. Seed the GHCR pull token into Vault at `homelab/ghcr`. This is a
    classic GitHub PAT with `read:packages` and nothing else. It is **not**
    created by `configure-vault.sh` like every other credential here —
    GitHub issues it, so it must be pasted in, and the script is fed to the
    pod on stdin where an interactive prompt would consume its own
    remaining lines. This step needs `vault-0` running and unsealed, which
    step 9's unseal half provides — that is why it is listed after step 9
    rather than nearer step 6, even though nothing else forces that
    ordering; the configure half of step 9 can still trail behind it, since
    it only gates `registry`'s credential path, not this seeding. Until the
    token exists, every pod pulling a private image sits in
    `ImagePullBackOff` with a 401 — but the `registry` Application itself
    keeps reporting `Synced`/`Healthy` throughout, for the same structural
    reason `vso-config` does in step 9: every resource it manages is a CRD
    kind Argo CD does not assess. The real check is
    `kubectl -n apps get vaultstaticsecret ghcr-pull` (SYNCED/HEALTHY/READY
    columns), not the Application's own status. See
    `platform/registry/README.md`.
11. Install the k3s registry mirror: copy `platform/nexus/registries.yaml` to
    `/etc/rancher/k3s/registries.yaml` and `sudo systemctl restart k3s`. **Not
    reconciled by Argo CD** — k3s reads that path from the host at startup and
    nothing in the cluster can apply it, so a rebuild does not recreate it. The
    restart cycles every pod on this node, so do it deliberately and not as a
    side effect of something else; `sudo` needs a real terminal here, so this
    cannot be scripted from a non-interactive context. Without the file,
    everything still works — containerd pulls Docker Hub directly and the
    `docker-proxy` cache is simply unused. The escape hatch if the mirror ever
    misbehaves is to delete the file and restart k3s again. See
    `platform/nexus/README.md`.

    Nexus's admin password needs no step of its own: it is seeded by
    `platform/vault/configure-vault.sh` (step 9's configure half) and applied
    to the running server by the `nexus-bootstrap` Job, not by hand. Rotating
    it later has an ordering trap — `platform/nexus/README.md` has the
    procedure.

The order matters: step 5 must follow step 4, because the `argocd`
namespace does not exist until `bootstrap.sh` creates it. Step 6 has the
same requirement, for the same reason — it also creates a Secret in that
namespace. The credential from step 5 is needed only before step 7, which
is the first thing that clones this repository. Steps 1–3 have no ordering
dependency on the cluster steps or on each other — they are a tailnet
setting and two GitHub settings, not cluster objects — but step 1 must be
done before anyone relies on TLS working and steps 2–3 before the first
push that needs them, so do all three first and be done with them. Step 9's
unseal half itself has no fixed position among the cluster steps either —
it only needs Vault deployed, which happens automatically at wave 10 — but
until it runs, everything from wave 21 on stays blocked, so do it as soon
as `vault-0` exists. Step 10 (the GHCR token) is placed after step 9 in this
list specifically because it needs `vault-0` unsealed first; unlike the
other steps, doing it in numeric order requires having already done the
step before it, not just some step before it in the plan. The configure
half of step 9 can trail behind step 10 without urgency, precisely because
wave 22 already sits after every Ingress. Step 11 is genuinely last and
genuinely optional: the mirror it configures is a cache, so nothing in the
cluster waits on it. Install it **after** the cluster is up rather than before
— a mirror pointing at a Nexus that does not exist yet leans on containerd's
fallback to the upstream registry, which is documented k3s behaviour but has
not been measured on this host (see `platform/nexus/README.md`).

## Known gaps

Things this cluster is known to be missing. None of them is urgent today,
and each is here because the alternative is that it lives only in whoever
last thought about it.

- **No `NetworkPolicy` anywhere, including `databases`.** Every pod in the
  cluster can reach PostgreSQL on 5432; the password is the only thing in
  the way. That is acceptable precisely because nothing connects to the
  database yet — there is no traffic to permit and therefore nothing a
  policy could usefully deny. It stops being acceptable the moment the
  first consumer arrives, which is also the moment you learn what the
  policy should say. Pick it up in that phase, not before.
- **Two of three `VaultStaticSecret` destinations still keep VSO's `_raw`
  key**, so each derived Secret carries its credential twice: once parsed,
  once in the verbatim KV JSON. `spec.destination.transformation.excludeRaw:
  true` removes the duplicate. This affects the phase-17 canary in `vault`
  and `postgres` in `databases`; `redis`, added in phase 19, ships with
  `excludeRaw: true` from the start and does not carry the duplicate. The
  gap is therefore two Secrets, not three, and the fix for those two
  remains outstanding — one small cross-cutting change rather than a
  component fix. Every extra copy widens what a `kubectl get secret -o
  yaml`, an Argo CD resource view, or an etcd backup exposes.
- **PostgreSQL has no `startupProbe`.** `pg_isready` reports "rejecting"
  during crash recovery, and the liveness probe allows 30s plus six
  20s-spaced failures — so 150 seconds is the longest WAL replay the pod
  may perform before liveness kills it and recovery restarts from the
  beginning. At the few kilobytes this database currently holds that is
  irrelevant. It becomes a restart loop the first time it comes back from
  an unclean shutdown holding real data.
- **`/backups` is mode 0777**, and `pg_dumpall` output contains a
  SCRAM-SHA-256 verifier for the `postgres` superuser, so any local
  account on this host can read a credential artifact out of a backup
  file. The password's entropy makes offline recovery infeasible, which is
  why this is a handling-hygiene problem and not an emergency — see
  `platform/databases/postgres/README.md` for the full two-sided
  argument. Roadmap §33 asks for it independently.
- **There is no PostgreSQL major-version upgrade procedure.** This matters
  more than it sounds: because `PGDATA` is version-namespaced, a major
  image bump does not refuse to start — it silently initialises an empty
  database while the old data sits intact and invisible one directory
  away. Reverting the image tag recovers everything. The full explanation
  is in `platform/databases/postgres/README.md`; the procedure itself
  (`pg_upgrade`, or dump/restore) is unwritten.
- **CI validates schema, not semantics.** `.github/workflows/validate.yaml`
  runs `kubeconform -strict` on every push, which catches malformed manifests
  and unknown fields. It runs on a GitHub-hosted runner, which cannot reach
  this cluster, so there is no server-side dry-run: a `VaultAuth` naming a
  Vault role that does not exist is schema-perfect and still fails at runtime.
  The gate narrows the window between a bad commit and the cluster; it does
  not close it. Every `role:`/`serviceAccount:`/`audiences:` comment in this
  repository warning that a mismatch "names neither side" still applies
  exactly as before.
- **The four Helm `values.yaml` files are not validated by CI.** They are
  excluded from `kubeconform` by filename pattern because they are not
  Kubernetes manifests and have no `apiVersion`/`kind`. They configure Vault,
  VSO, the Tailscale operator and Argo CD itself — arguably the four most
  consequential files here. The gate covers the many low-risk files and misses
  the few high-risk ones. A green check on this repository means less than it
  looks like it means.
- **The GHCR token expires, and the failure is delayed and misleading.**
  Running pods are unaffected; only *new* pulls fail. An expired token
  therefore surfaces at the next rollout, node restart or eviction —
  arbitrarily far from the cause — as `ImagePullBackOff` with a 401 that names
  no expiry. Rotation is a documented manual step with no alarm on it, and the
  expiry date **must** be recorded in `platform/registry/README.md` — it
  currently is not: that table still reads `Issued: _fill in_` and
  `Expires: _fill in_`. Until someone fills it in, the expiry is known only
  to whoever issued the token, and nowhere the cluster can see it either way.
- **Argo CD reports `registry` and `vso-config` `Healthy` no matter what
  their credential path is actually doing.** Every resource either
  Application manages — `ServiceAccount`, `VaultConnection`, `VaultAuth`,
  `VaultStaticSecret` — is a CRD kind Argo CD has no built-in or custom
  health check for, so `.status.resources[*].health` is empty for all of
  them and the Application rolls up `Healthy` unconditionally. This is the
  repo's own "Synced/Healthy proves only what it applied" principle with a
  live instance behind it: during phase 20, `registry` read Synced/Healthy
  for roughly 40 minutes while its `VaultStaticSecret` reported
  `invalid role name "vso-ghcr"` and no `ghcr-pull` Secret existed anywhere.
  `kubectl -n argocd get applications` cannot tell these two Applications'
  real state apart from a fully working one. The real check is the
  `VaultStaticSecret`'s own SYNCED/HEALTHY/READY columns —
  `kubectl -n apps get vaultstaticsecret ghcr-pull` for `registry`,
  `kubectl -n vault get vaultstaticsecret vault-canary` for `vso-config`.

## Documentation

- `docs/workstation-plan.md` — overall roadmap
- `docs/troubleshooting.md` — faults hit on this cluster and how they were
  diagnosed. Read entry 1 before debugging any pod that fails to start.
- `docs/superpowers/specs/` — design documents
- `docs/superpowers/plans/` — implementation plans
