# k3s + Argo CD Bootstrap — Design

Date: 2026-09-01
Status: Approved, not yet implemented
Covers: phases 9–12 of `docs/workstation-plan.md` (k3s, kubectl/Helm, homelab repo layout, Argo CD)

## 1. Goal

Turn the already-running k3s node into a GitOps-driven platform: make the
cluster usable from an unprivileged shell, move persistent storage onto the
data SSD, install Argo CD, and hand Argo CD ownership of both itself and
everything added later.

After this work, the only manual cluster operation is applying a single root
Application. Every subsequent change is a git commit.

## 2. Starting state

Measured on the host, not assumed from the roadmap. The roadmap says "install
k3s"; k3s is in fact already running, so this design starts further along than
`workstation-plan.md` §37 implies.

| Item | State |
| --- | --- |
| k3s | v1.36.4+k3s1, active, ~2 days uptime |
| k3s data-dir | `/srv/kubernetes/data` (set in `/etc/rancher/k3s/config.yaml`) |
| k3s unit args | none inline; all configuration lives in `config.yaml` |
| kubeconfig | `/etc/rancher/k3s/k3s.yaml`, mode root-only |
| `KUBECONFIG` | exported globally to that path — but unreadable, so `kubectl` fails |
| `k3s-admin` group | exists, gid 1002, contains `slqzeer` |
| `~/.kube` | exists, owned `slqzeer:k3s-admin`, contains only `cache/` |
| `/srv/kubernetes/storage` | exists, empty, nothing references it |
| `/srv/kubernetes/manifests/argocd` | exists, empty |
| kubectl | `/usr/local/bin/kubectl` |
| Helm | `/snap/bin/helm`, v4.2.4 |
| argocd CLI | not installed |
| Repo | scaffolded per plan §14, `.gitkeep` only, no manifests |
| Remote | `git@github.com:Slqzeer/homelab.git` |
| GitHub SSH | key `~/.ssh/id_ed25519` exists, passphrase-protected, not in an agent; auth currently fails |
| sudo | requires a password; privileged steps are operator-run |

The pre-existing `k3s-admin` group and the `slqzeer:k3s-admin` ownership of
`~/.kube` indicate group-readable kubeconfig was already the intent. This design
completes that rather than choosing a new approach.

## 3. Decisions

| Decision | Choice | Rejected alternatives |
| --- | --- | --- |
| PVC storage location | Repoint k3s's default local-path provisioner to `/srv/kubernetes/storage` via the `default-local-storage-path` server flag | A second StorageClass (silently wrong default); deferring storage (a trap for Vault/PostgreSQL later) |
| Argo CD install | Upstream Helm chart, pinned, bootstrapped by an idempotent script | Upstream `install.yaml` (values-based config becomes patch files) |
| Argo CD lifecycle | Self-managed: an Application manages the Argo CD release itself | Manual `helm upgrade` forever (breaks "git is the source of truth") |
| App-of-apps shape | Explicit root Application over a directory of per-component Applications | ApplicationSet git generator (fights per-component values and sync ordering); flat manual applies (defeats GitOps) |
| UI access | `kubectl port-forward`, Argo CD server in insecure mode | Traefik ingress now (pulls phases 13–14 forward; self-signed TLS until cert-manager) |
| Repo credentials | Dedicated read-only deploy key for Argo CD | Reusing the operator's personal key (cluster would hold write access to every repo); making the repo public |
| App image updates | Argo CD `targetRevision` selects a branch/tag/commit of the app's own repo | Argo CD Image Updater; floating `:latest` tags |

All three k3s flags this design relies on — `write-kubeconfig-mode`,
`write-kubeconfig-group`, `default-local-storage-path` — were verified present in
`k3s server --help` on this host.

## 4. Host prerequisites

These require sudo and are run by the operator.

### 4.1 Read the existing config first

`/etc/rancher/k3s/config.yaml` already sets `data-dir` and is not readable by the
implementing user. It must be read before it is edited; the edits below are
additions to that file, not a replacement of it.

### 4.2 Group-readable kubeconfig

Add to `/etc/rancher/k3s/config.yaml`:

```yaml
write-kubeconfig-mode: "0640"
write-kubeconfig-group: k3s-admin
```

Copying the kubeconfig to `~/.kube/config` is rejected: the copy goes stale when
k3s rotates certificates, and the failure is confusing when it happens.

### 4.3 Storage on the data SSD

Add to the same file:

```yaml
default-local-storage-path: /srv/kubernetes/storage
```

The local-path-provisioner ConfigMap must **not** be edited directly. k3s owns
`${data-dir}/server/manifests/local-storage.yaml` and reverts changes to it on
every upgrade; the server flag is the supported path.

### 4.4 Apply

`sudo systemctl restart k3s`, then confirm the node returns Ready and the
kubeconfig is group-readable.

### 4.5 GitHub SSH

`ssh-add ~/.ssh/id_ed25519` followed by `ssh -T git@github.com`. If auth still
fails, the public key is not registered on GitHub and must be added at
`github.com/settings/keys`.

This gates more than convenience. The repository currently has two local commits
and an unreachable remote, so nothing exists on GitHub for Argo CD to clone. All
manifests must be committed **and pushed** before the root Application of §6.1 is
applied; otherwise the first sync fails against an empty or missing repo. Ordering
is therefore: host fixes → write manifests → push → bootstrap Argo CD → apply root.

## 5. Argo CD

### 5.1 Bootstrap

Namespace `argocd`. Installed from the `argo-cd` chart pinned at **10.5.0**
(Argo CD v3.5.2), with values committed to `bootstrap/argocd/values.yaml`:

- `configs.params."server.insecure": true` — nothing terminates TLS in front of
  it during the port-forward phase
- Dex disabled — no SSO in scope
- modest resource requests suited to a single-node homelab

`bootstrap/argocd/bootstrap.sh` performs the first install: create the namespace,
add the chart repo, `helm upgrade --install` against the committed values file,
wait for rollout. It is idempotent.

The script is a permanent part of the repository, not a one-shot. Because Argo CD
manages itself (§5.2), the script is the recovery path when a bad commit breaks
the controller that would otherwise reconcile the fix.

### 5.2 Self-management

`environments/homelab/apps/argocd.yaml` is a multi-source Application:

- source 1: `https://argoproj.github.io/argo-helm`, chart `argo-cd`,
  `targetRevision: 10.5.0`
- source 2: this git repo, `ref: values`
- the chart reads `$values/bootstrap/argocd/values.yaml`

Because this is the same chart version and the same values file the bootstrap
script used, adoption is a no-op in terms of rendered manifests — it transfers
ownership without changing state. Thereafter, upgrading Argo CD means editing
`targetRevision` and committing.

**Accepted risk:** self-management means a bad values commit can break the
controller that would fix it. Mitigated by §5.1's script, which restores a working
Argo CD from the last good commit. This is inherent to self-management and is
accepted deliberately.

### 5.3 Access

`kubectl port-forward svc/argocd-server -n argocd 8080:80`. The initial admin
password is read from the `argocd-initial-admin-secret` Secret and should be
changed, after which that Secret is deleted. Ingress, DNS, and TLS are phases
13–15 and out of scope here.

## 6. Repository structure

```
bootstrap/
  argocd/
    values.yaml              Argo CD Helm values — single source of truth
    bootstrap.sh             idempotent first-install and recovery
  namespaces/
    namespaces.yaml
environments/homelab/
  root.yaml                  the only manifest ever applied by hand
  apps/
    argocd.yaml              sync-wave -1, self-management
    namespaces.yaml          sync-wave 0
infrastructure/              values and manifests for in-cluster infra
platform/
observability/
apps/                        Applications pointing at external app repos
```

### 6.1 Root Application

`environments/homelab/root.yaml` is one Application: source is this repo, path
`environments/homelab/apps`, directory recursion enabled, destination the local
cluster, sync policy automated with `prune` and `selfHeal`.

It is applied by hand exactly once. Adding a component afterwards is committing
one file to `environments/homelab/apps/`.

### 6.2 Sync ordering

Ordering is expressed with `argocd.argoproj.io/sync-wave`:

| Wave | Contents |
| --- | --- |
| -1 | argocd (self-management) |
| 0 | namespaces |
| 1–5 | infrastructure (storage, ingress, cert-manager, networking) |
| 10 | platform (vault, databases, registry) |
| 20 | apps |

Waves are established now, while there are two components, because cert-manager
must precede anything requesting a certificate and Vault must precede the Vault
Secrets Operator. Retrofitting ordering after those exist is materially harder
than declaring it up front.

### 6.3 Where configuration lives

Two categories, deliberately treated differently:

**Third-party infrastructure** (Argo CD, cert-manager, Vault, Prometheus). These
are upstream charts with no repo of ours to own their settings, so this repo holds
both the Application and the values.

**Our own applications.** The app's own repo owns its Dockerfile, CI, and deploy
manifests. This repo holds only an Application pointing at that repo. No app
setting is duplicated here.

### 6.4 App release convention

For our own applications, the app repo's git revision is the release pointer:
`targetRevision` in `apps/*.yaml` names a branch, tag, or commit of the app repo,
and Argo CD deploys whatever that revision's manifests declare.

This imposes a contract on every app repo: **the concrete image tag must be pinned
inside the app repo's own manifests at that revision.** If `deploy/` at tag
`v1.4.2` still references `:latest`, then selecting `v1.4.2` pins the manifests but
not the image, and the deployment drifts. The app repo's release step writes the
tag into its manifests.

Images go to GHCR initially (plan phase 20) and possibly Artifactory later (phase
21). No registry hostname is hardcoded in this repo, since Applications reference
app repos rather than images.

## 7. Repository credentials

A dedicated deploy key, separate from the operator's personal key:

- generated passphrase-less at `~/.ssh/argocd_homelab_deploy`
- public half registered on the GitHub repo as a deploy key with **write access
  disabled**
- private half loaded into a Secret in the `argocd` namespace labelled
  `argocd.argoproj.io/secret-type: repository`

The private key is never committed. The operator's personal key is deliberately
not reused: it can push to every repository they own, and a controller that only
reads should not hold that.

**Known gap:** this Secret is created out-of-band, so the cluster holds a
credential that is not reproducible from git. This is accepted for now and is the
first thing to move into Vault in phase 17.

## 8. Verification

Completion requires all five to pass. "It applied without errors" is not
sufficient — three of these test the reconciliation loop rather than the install.

1. `kubectl get nodes` succeeds as `slqzeer` with no sudo and reports Ready.
2. A throwaway PVC binds, and its directory appears under
   `/srv/kubernetes/storage`. Delete afterwards.
3. `kubectl -n argocd get applications` shows `root` and `argocd`, both `Synced`
   and `Healthy`.
4. Deleting a resource managed by Argo CD results in it being restored by
   `selfHeal` — proves the loop runs, not merely that the install succeeded.
5. Argo CD reports the `argocd` Application `Synced` while `helm list -n argocd`
   still shows the release — proves self-management adopted the release rather
   than silently diverging from it.

## 9. Risks

**Helm 4.2.4 is very new and the argo-cd chart is authored against Helm 3.**
Chart compatibility is expected to hold, but it is unverified on this host. The
first implementation step after §4 is `helm template` against the chart to
confirm it renders. If it does not, the fallback is installing from the upstream
`install.yaml` and dropping the Helm indirection; §5.2 then uses a plain
directory source instead of a multi-source chart reference.

**Restarting k3s briefly interrupts the cluster.** No workloads of consequence
run yet, so the window is harmless now — but this is the last easy moment to
change these flags.

**`/srv/kubernetes/manifests/argocd` exists and is empty.** Its purpose predates
this design; nothing here writes to it. It should be removed unless a reason to
keep it emerges, to avoid a second apparent home for Argo CD configuration.

## 10. Out of scope

Ingress, DNS, TLS and cert-manager, Vault and the Vault Secrets Operator,
PostgreSQL, Redis, Artifactory, Prometheus, Grafana, logging, backup automation,
and the creation of any application repository. These are plan phases 13 onward
and each warrants its own design.
