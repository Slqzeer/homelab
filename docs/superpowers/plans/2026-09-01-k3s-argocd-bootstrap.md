# k3s + Argo CD Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-running k3s node usable from an unprivileged shell, move PVC storage to the data SSD, install Argo CD, and hand Argo CD ownership of itself and everything added later.

**Architecture:** Two k3s server flags fix kubeconfig permissions and the default storage path. Argo CD is installed once by an idempotent Helm script, then adopted by an Application that reads the same chart version and the same committed values file, making adoption a no-op in rendered output. A single root Application over `environments/homelab/apps/` is the only manifest ever applied by hand.

**Tech Stack:** k3s v1.36.4+k3s1, Helm v4.2.4, argo-cd Helm chart 10.5.0 (Argo CD v3.5.2), kubectl, git over SSH.

**Spec:** `docs/superpowers/specs/2026-09-01-k3s-argocd-bootstrap-design.md`

## Global Constraints

- Argo CD chart version is pinned to **`10.5.0`** everywhere it appears. `bootstrap/argocd/bootstrap.sh` and `environments/homelab/apps/argocd.yaml` must name the same version, or adoption changes the deployed manifests.
- Helm release name is **`argocd`** in namespace **`argocd`**. The self-management Application must set `helm.releaseName: argocd` to match, or Argo CD renders different resource names than the script installed.
- Values live in exactly one file: **`bootstrap/argocd/values.yaml`**. The script and the Application both read it. Never inline values into the Application.
- Repo URL is **`git@github.com:Slqzeer/homelab.git`**, branch **`main`**.
- k3s host config file is **`/etc/rancher/k3s/config.yaml`**. It already contains a `data-dir` key. Edits are **additions**; never rewrite the file.
- Every step requiring `sudo` is **operator-run**. The implementing agent cannot authenticate. Present the command and wait.
- Private keys are never committed. `~/.ssh/argocd_homelab_deploy` and the Secret built from it stay out of git.
- Storage path is **`/srv/kubernetes/storage`**.
- Argo CD's own CRDs exceed the `kubectl.kubernetes.io/last-applied-configuration` annotation limit, so any Application managing Argo CD must set `ServerSideApply=true`.

**Verification style:** this is infrastructure, not library code, so "write the failing test" means running the assertion command and confirming it fails for the expected reason before making the change. Do not skip the failing run — it is what distinguishes a fix from a coincidence.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `/etc/rancher/k3s/config.yaml` (host, not repo) | k3s server flags: data-dir, kubeconfig mode/group, default storage path |
| `infrastructure/storage/README.md` | Documents that the default StorageClass points at `/srv` and how, since the mechanism is a host flag with no in-cluster manifest |
| `bootstrap/argocd/values.yaml` | Sole source of Argo CD configuration; read by both the script and the Application |
| `bootstrap/argocd/bootstrap.sh` | Idempotent first install and recovery path |
| `bootstrap/namespaces/namespaces.yaml` | Namespaces for upcoming phases |
| `environments/homelab/apps/namespaces.yaml` | Application, wave 0, pointing at `bootstrap/namespaces` |
| `environments/homelab/apps/argocd.yaml` | Application, wave -1, multi-source, Argo CD self-management |
| `environments/homelab/root.yaml` | The single hand-applied Application over `environments/homelab/apps/` |

---

## Task 1: Host prerequisites — kubeconfig access and storage path

Fixes two things at once because both are keys in the same file and both need the same single k3s restart.

**Files:**
- Modify (host, operator-run): `/etc/rancher/k3s/config.yaml`
- Create: `infrastructure/storage/README.md`
- Delete: `infrastructure/storage/.gitkeep`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `kubectl` for user `slqzeer` with no sudo; a default StorageClass named `local-path` whose volumes land under `/srv/kubernetes/storage`. Every later task depends on both.

- [ ] **Step 1: Run the failing assertions**

```bash
kubectl get nodes
```
Expected: FAIL — `error loading config file "/etc/rancher/k3s/k3s.yaml": permission denied`.

If it instead succeeds, the host was already fixed; verify `stat -c '%a %G' /etc/rancher/k3s/k3s.yaml` reports `640 k3s-admin` before skipping ahead.

- [ ] **Step 2: Read the existing host config (operator runs)**

Ask the operator to run and paste the output:

```
! sudo cat /etc/rancher/k3s/config.yaml
```

Do not proceed without seeing it. It already sets `data-dir: /srv/kubernetes/data`; the next step adds keys alongside that, and blindly writing a new file would drop it and relocate the entire cluster state.

- [ ] **Step 3: Add the three keys (operator runs)**

Ask the operator to append these to `/etc/rancher/k3s/config.yaml`, preserving every existing key:

```yaml
write-kubeconfig-mode: "0640"
write-kubeconfig-group: k3s-admin
default-local-storage-path: /srv/kubernetes/storage
```

`write-kubeconfig-mode` must be quoted — unquoted `0640` parses as the integer 640 and the mode is wrong.

- [ ] **Step 4: Restart k3s (operator runs)**

```
! sudo systemctl restart k3s
```

- [ ] **Step 5: Re-run the kubeconfig assertion**

```bash
stat -c '%a %G' /etc/rancher/k3s/k3s.yaml
kubectl get nodes
```
Expected: `640 k3s-admin`, then one node in `Ready` state. If `kubectl` still fails with a permission error, confirm `id -nG | tr ' ' '\n' | grep k3s-admin` returns a match — group membership granted after the current login session started does not apply until re-login.

- [ ] **Step 6: Assert storage lands on the data SSD**

k3s's `local-path` StorageClass uses `volumeBindingMode: WaitForFirstConsumer`, so a PVC with no consuming Pod stays `Pending` forever. The test needs a Pod.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: storage-probe
  namespace: default
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 64Mi
---
apiVersion: v1
kind: Pod
metadata:
  name: storage-probe
  namespace: default
spec:
  restartPolicy: Never
  containers:
    - name: probe
      image: busybox:1.36
      command: ["sh", "-c", "echo homelab-storage-probe > /data/probe.txt && sleep 5"]
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: storage-probe
EOF

kubectl wait --for=condition=Ready pod/storage-probe --timeout=120s
kubectl get pvc storage-probe -o jsonpath='{.status.phase}{"\n"}'
```
Expected: PVC phase `Bound`.

- [ ] **Step 7: Confirm the bytes are physically on /srv (operator runs)**

```
! sudo find /srv/kubernetes/storage -name probe.txt
```
Expected: one path printed under `/srv/kubernetes/storage/`. An empty result means the flag did not take effect — the volume went to `${data-dir}/storage` instead, and Step 3 or Step 4 did not apply.

- [ ] **Step 8: Clean up the probe**

```bash
kubectl delete pod/storage-probe pvc/storage-probe -n default --wait=true
```

- [ ] **Step 9: Record the mechanism in the repo**

The storage decision has no in-cluster manifest — it is a host flag — so without this file the reason `/srv` is used is invisible to anyone reading the repo.

Create `infrastructure/storage/README.md`:

```markdown
# Storage

PersistentVolumes are provisioned by k3s's bundled `local-path-provisioner`
onto the data SSD at `/srv/kubernetes/storage`.

This is configured by a **k3s server flag**, not by an in-cluster manifest:

```yaml
# /etc/rancher/k3s/config.yaml
default-local-storage-path: /srv/kubernetes/storage
```

Do not edit the `local-path-config` ConfigMap directly. k3s owns
`${data-dir}/server/manifests/local-storage.yaml` and reverts changes to it on
every upgrade.

The `local-path` StorageClass is the cluster default and uses
`volumeBindingMode: WaitForFirstConsumer`, so a PVC stays `Pending` until a Pod
consumes it. That is expected, not a fault.
```

- [ ] **Step 10: Commit**

```bash
git rm -q --cached infrastructure/storage/.gitkeep && rm -f infrastructure/storage/.gitkeep
git add infrastructure/storage/README.md
git commit -m "docs(storage): record k3s default-local-storage-path on /srv"
```

---

## Task 2: Verify Helm 4 renders the argo-cd chart

The spec's one genuine unknown (§9). Resolve it before writing anything that assumes Helm, because a failure here changes Tasks 4 and 7.

**Files:** none created or modified. This task produces a decision.

**Interfaces:**
- Consumes: working `kubectl` from Task 1.
- Produces: a confirmed answer to "does Helm 4.2.4 render argo-cd 10.5.0". Tasks 4 and 7 assume yes.

- [ ] **Step 1: Add the chart repo**

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update argo
```

- [ ] **Step 2: Confirm the pinned version exists**

```bash
helm search repo argo/argo-cd --version 10.5.0 --output json | jq -r '.[0] | "\(.version) \(.app_version)"'
```
Expected: `10.5.0 v3.5.2`. A different app_version means the chart moved; stop and report rather than adjusting the pin silently.

- [ ] **Step 3: Render the chart**

```bash
helm template argocd argo/argo-cd \
  --version 10.5.0 \
  --namespace argocd \
  > /tmp/argocd-render.yaml
echo "exit=$?"
grep -c '^kind:' /tmp/argocd-render.yaml
```
Expected: exit 0 and a count well above 20.

- [ ] **Step 4: Confirm the render is valid against this cluster's API**

Rendering is not the same as being accepted. This catches API-version drift between chart 10.5.0 and k8s 1.36.

```bash
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --dry-run=server -f /tmp/argocd-render.yaml >/dev/null && echo "SERVER DRY-RUN OK"
```
Expected: `SERVER DRY-RUN OK`.

The namespace is created for real here (not dry-run) because a server-side dry-run of namespaced resources fails if the namespace does not exist. Task 4 expects it to exist and is written to tolerate that.

- [ ] **Step 5: Record the outcome**

If Steps 3 and 4 passed, continue to Task 3.

**If either failed:** stop and report. Do not work around it. The spec's documented fallback is to install from the upstream `install.yaml` and drop the Helm indirection, which changes Task 4's script and makes Task 7's Application a plain directory source instead of a multi-source chart reference. That is a spec revision, not an implementation detail.

- [ ] **Step 6: Clean up**

```bash
rm -f /tmp/argocd-render.yaml
```

---

## Task 3: GitHub SSH and first push

Argo CD clones the repo over SSH. Right now the remote is unreachable and nothing has ever been pushed, so there is no repo for Argo CD to read.

**Files:** none. This task publishes existing commits.

**Interfaces:**
- Consumes: nothing.
- Produces: `git@github.com:Slqzeer/homelab.git` reachable and `main` pushed. Tasks 6 and 7 fail without it.

- [ ] **Step 1: Run the failing assertion**

```bash
ssh -o BatchMode=yes -T git@github.com 2>&1 | head -2
```
Expected: FAIL — `git@github.com: Permission denied (publickey).`

- [ ] **Step 2: Load the key (operator runs)**

The key at `~/.ssh/id_ed25519` is passphrase-protected and not loaded in an agent, which is why a non-interactive shell cannot use it.

```
! ssh-add ~/.ssh/id_ed25519 && ssh -T git@github.com
```
Expected: `Hi Slqzeer! You've successfully authenticated...`

- [ ] **Step 3: If it still fails, register the key**

`Permission denied` after a successful `ssh-add` means the public key is not on the account. The operator adds this at `github.com/settings/keys`:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ8mRiDkUVz4/hFo3+EgSBqCPnlym5VJtb2IzsepyQ1G github
```

Then re-run Step 2.

- [ ] **Step 4: Confirm the remote repo exists and push**

```bash
git ls-remote origin
```

If this errors with `Repository not found`, the GitHub repo has not been created — the operator creates an **empty private** repo named `homelab` under their account first (no README, no .gitignore, or the histories diverge).

```bash
git push -u origin main
```

- [ ] **Step 5: Verify the push landed**

```bash
git ls-remote origin refs/heads/main
git rev-parse HEAD
```
Expected: the same SHA on both lines.

---

## Task 4: Argo CD values and bootstrap script

**Files:**
- Create: `bootstrap/argocd/values.yaml`
- Create: `bootstrap/argocd/bootstrap.sh`

**Interfaces:**
- Consumes: Helm confirmed working (Task 2).
- Produces: Helm release `argocd` in namespace `argocd`; the values file path `bootstrap/argocd/values.yaml`, which Task 7's Application references verbatim as `$values/bootstrap/argocd/values.yaml`.

- [ ] **Step 1: Run the failing assertion**

```bash
kubectl -n argocd get deploy argocd-server
```
Expected: FAIL — `Error from server (NotFound): deployments.apps "argocd-server" not found`.

- [ ] **Step 2: Write the values file**

Create `bootstrap/argocd/values.yaml`:

```yaml
# Single source of truth for Argo CD configuration.
# Read by bootstrap/argocd/bootstrap.sh AND by
# environments/homelab/apps/argocd.yaml (self-management).
# Chart: argo-cd 10.5.0 (Argo CD v3.5.2)

configs:
  params:
    # Nothing terminates TLS in front of Argo CD during the port-forward
    # phase. Revisit when ingress + cert-manager land (plan phases 13-15).
    server.insecure: true

# No SSO in scope; local admin only.
dex:
  enabled: false

notifications:
  enabled: false

controller:
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      memory: 1Gi

server:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 512Mi

repoServer:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 512Mi

applicationSet:
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      memory: 256Mi

redis:
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      memory: 256Mi
```

CPU limits are deliberately omitted. CPU is compressible, and limits on a single-node homelab cause throttling under load with no benefit.

- [ ] **Step 3: Write the bootstrap script**

Create `bootstrap/argocd/bootstrap.sh`:

```bash
#!/usr/bin/env bash
#
# First install and recovery path for Argo CD.
#
# This script is NOT a routine tool. After the initial bootstrap, Argo CD
# manages itself via environments/homelab/apps/argocd.yaml and upgrades happen
# by editing targetRevision and committing.
#
# It stays in the repository because self-management has one failure mode it
# cannot recover from on its own: a bad commit to values.yaml that breaks the
# controller which would otherwise reconcile the fix. Re-running this script
# restores a working Argo CD from the last good commit.
#
# Idempotent: safe to re-run at any time.

set -euo pipefail

CHART_VERSION="10.5.0"
NAMESPACE="argocd"
RELEASE="argocd"
CHART_REPO_NAME="argo"
CHART_REPO_URL="https://argoproj.github.io/argo-helm"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALUES_FILE="${SCRIPT_DIR}/values.yaml"

if [[ ! -f "${VALUES_FILE}" ]]; then
  echo "error: values file not found at ${VALUES_FILE}" >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE}"
kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 \
  || kubectl create namespace "${NAMESPACE}"

echo "==> Ensuring chart repo ${CHART_REPO_NAME}"
helm repo add "${CHART_REPO_NAME}" "${CHART_REPO_URL}" --force-update >/dev/null
helm repo update "${CHART_REPO_NAME}" >/dev/null

echo "==> Installing ${RELEASE} (chart ${CHART_VERSION}) into ${NAMESPACE}"
helm upgrade --install "${RELEASE}" "${CHART_REPO_NAME}/argo-cd" \
  --namespace "${NAMESPACE}" \
  --version "${CHART_VERSION}" \
  --values "${VALUES_FILE}" \
  --wait \
  --timeout 10m

echo "==> Waiting for rollout"
kubectl -n "${NAMESPACE}" rollout status deploy/argocd-server --timeout=5m

echo "==> Done. Reach the UI with:"
echo "    kubectl port-forward -n ${NAMESPACE} svc/argocd-server 8080:80"
```

- [ ] **Step 4: Make it executable and run it**

```bash
chmod +x bootstrap/argocd/bootstrap.sh
./bootstrap/argocd/bootstrap.sh
```

- [ ] **Step 5: Re-run the assertion**

```bash
kubectl -n argocd get pods
helm list -n argocd --output json | jq -r '.[] | "\(.name) \(.chart) \(.status)"'
```
Expected: all pods `Running`; one release line reading `argocd argo-cd-10.5.0 deployed`.

- [ ] **Step 6: Prove idempotency**

The script's whole purpose is being safely re-runnable. Verify that claim rather than asserting it.

```bash
./bootstrap/argocd/bootstrap.sh
helm list -n argocd --output json | jq -r '.[0].status'
```
Expected: exits 0, status `deployed`.

- [ ] **Step 7: Commit**

```bash
git rm -q --cached bootstrap/.gitkeep 2>/dev/null || true
rm -f bootstrap/.gitkeep
git add bootstrap/argocd/values.yaml bootstrap/argocd/bootstrap.sh
git commit -m "feat(argocd): add pinned Helm values and idempotent bootstrap script"
```

---

## Task 5: Read-only deploy key for Argo CD

Argo CD must clone the repo. It gets its own credential rather than the operator's personal key, which can push to every repo they own.

**Files:** none committed. This task creates a host key pair and an in-cluster Secret, both deliberately outside git.

**Interfaces:**
- Consumes: running Argo CD (Task 4), reachable GitHub repo (Task 3).
- Produces: a Secret named `repo-homelab` in namespace `argocd` that lets Argo CD read `git@github.com:Slqzeer/homelab.git`. Tasks 6 and 7 fail without it.

- [ ] **Step 1: Generate the key pair**

```bash
ssh-keygen -t ed25519 -N "" -C "argocd-homelab-deploy" -f ~/.ssh/argocd_homelab_deploy
cat ~/.ssh/argocd_homelab_deploy.pub
```

No passphrase: Argo CD reads this non-interactively and cannot be prompted. The mitigation is scope, not encryption — the key is read-only and repo-scoped.

- [ ] **Step 2: Register it as a deploy key (operator runs)**

The operator adds the printed public key at
`https://github.com/Slqzeer/homelab/settings/keys` with title `argocd` and
**"Allow write access" left unchecked**.

Write access must stay off. Argo CD only ever reads; a controller with push rights to its own source of truth can rewrite the history that governs it.

- [ ] **Step 3: Run the failing assertion**

```bash
kubectl -n argocd get secret repo-homelab
```
Expected: FAIL — `secrets "repo-homelab" not found`.

- [ ] **Step 4: Create the Secret**

Built from the file on disk so the private key never passes through a shell argument or a committed manifest.

```bash
kubectl -n argocd create secret generic repo-homelab \
  --from-literal=type=git \
  --from-literal=url=git@github.com:Slqzeer/homelab.git \
  --from-file=sshPrivateKey=$HOME/.ssh/argocd_homelab_deploy

kubectl -n argocd label secret repo-homelab \
  argocd.argoproj.io/secret-type=repository
```

The label is what makes Argo CD treat the Secret as a repository credential. Without it the Secret is inert and clones fail with a confusing auth error.

- [ ] **Step 5: Verify Argo CD can actually reach the repo**

Secret existence proves nothing. Check that the repo-server can clone.

```bash
kubectl -n argocd rollout restart deploy/argocd-repo-server
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=3m

kubectl -n argocd exec deploy/argocd-repo-server -- \
  sh -c 'GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no" git ls-remote git@github.com:Slqzeer/homelab.git refs/heads/main' \
  2>&1 | tail -2
```
Expected: a SHA and `refs/heads/main`.

If this fails with `Permission denied (publickey)`, the deploy key was not registered (Step 2) or was registered on the wrong repo.

- [ ] **Step 6: Confirm nothing secret is stageable**

```bash
git status --porcelain
```
Expected: empty. The key lives in `~/.ssh`, outside the repo. If anything key-shaped appears here, stop and remove it before any later commit.

---

## Task 6: App-of-apps skeleton, proven with namespaces

Establishes the root Application and proves the GitOps loop end-to-end using a harmless component. Argo CD self-management comes only after the loop is known to work — debugging a broken loop while it manages Argo CD is much harder.

**Files:**
- Create: `bootstrap/namespaces/namespaces.yaml`
- Create: `environments/homelab/apps/namespaces.yaml`
- Create: `environments/homelab/root.yaml`
- Delete: `environments/homelab/.gitkeep`

**Interfaces:**
- Consumes: Argo CD running (Task 4), repo credential (Task 5).
- Produces: Application `root` watching `environments/homelab/apps/` recursively with automated prune and selfHeal. Task 7 adds a file to that directory and relies on root picking it up with no manual apply.

- [ ] **Step 1: Write the namespaces manifest**

Create `bootstrap/namespaces/namespaces.yaml`:

```yaml
# Namespaces for components arriving in plan phases 15-16.
# Add a namespace here when the component that needs it is planned, not before.
apiVersion: v1
kind: Namespace
metadata:
  name: cert-manager
---
apiVersion: v1
kind: Namespace
metadata:
  name: vault
```

The spec calls for `bootstrap/namespaces/namespaces.yaml` at wave 0 but does not enumerate contents. These two are the next confirmed consumers (plan phases 15 and 16). Empty namespaces are free; speculative ones are clutter, so nothing further is added here.

- [ ] **Step 2: Write the namespaces Application**

Create `environments/homelab/apps/namespaces.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: namespaces
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: bootstrap/namespaces
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
```

`CreateNamespace=false` because this Application creates namespaces as resources; letting Argo CD also auto-create the destination namespace would be circular.

- [ ] **Step 3: Write the root Application**

Create `environments/homelab/root.yaml`:

```yaml
# The only manifest ever applied by hand.
#
# Everything else reaches the cluster by being committed into
# environments/homelab/apps/ and picked up from here.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: environments/homelab/apps
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

No `resources-finalizer.argocd.argoproj.io` finalizer. With one, deleting `root` cascades into deleting every component it manages — an outcome nobody wants from a single `kubectl delete`.

- [ ] **Step 4: Commit and push**

The push must happen before the apply. Argo CD reads GitHub, not the working tree, so applying first produces a sync failure against a revision that does not exist yet.

```bash
git rm -q --cached environments/homelab/.gitkeep && rm -f environments/homelab/.gitkeep
git add bootstrap/namespaces/namespaces.yaml \
        environments/homelab/apps/namespaces.yaml \
        environments/homelab/root.yaml
git commit -m "feat(gitops): add root app-of-apps and namespaces component"
git push
```

- [ ] **Step 5: Run the failing assertion**

```bash
kubectl -n argocd get applications
```
Expected: FAIL or empty — `No resources found in argocd namespace.`

- [ ] **Step 6: Apply root — the one manual apply**

```bash
kubectl apply -f environments/homelab/root.yaml
```

- [ ] **Step 7: Verify the loop reached the cluster**

```bash
kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced \
  application/root --timeout=180s
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
kubectl get namespace cert-manager vault
```
Expected: `root` and `namespaces` both `Synced`/`Healthy`, and both namespaces existing.

`namespaces` appearing without being applied by hand is the actual result here — it proves root is reading the directory, not just that a manifest was accepted.

- [ ] **Step 8: Prove selfHeal reconciles drift**

An install that succeeded once is not a working control loop. Delete a managed resource and confirm it returns.

```bash
kubectl delete namespace vault --wait=true

# selfHeal is not instant -- Argo CD's default reconciliation interval is
# 3 minutes. Poll rather than sleeping a fixed amount.
for i in $(seq 1 30); do
  if kubectl get namespace vault >/dev/null 2>&1; then
    echo "SELFHEAL OK after ~$((i * 10))s"
    break
  fi
  sleep 10
done
kubectl get namespace vault
```
Expected: `SELFHEAL OK`, and the namespace exists again — recreated by Argo CD with no human action.

If the loop runs to completion without recovery, inspect
`kubectl -n argocd get application namespaces -o jsonpath='{.status.conditions}{"\n"}'`
before retrying. A hard refresh can be forced with
`kubectl -n argocd patch application namespaces --type merge -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'`.

---

## Task 7: Argo CD self-management

Hands Argo CD ownership of its own release. Done last because it is the only step that can break the thing fixing it.

**Files:**
- Create: `environments/homelab/apps/argocd.yaml`

**Interfaces:**
- Consumes: working root Application (Task 6), Helm release `argocd` (Task 4), values at `bootstrap/argocd/values.yaml`.
- Produces: Application `argocd` owning the release. After this, Argo CD upgrades are a `targetRevision` edit plus a commit.

- [ ] **Step 1: Write the self-management Application**

Create `environments/homelab/apps/argocd.yaml`:

```yaml
# Argo CD manages its own release.
#
# Chart version and values file are identical to what
# bootstrap/argocd/bootstrap.sh installed, so adoption changes no rendered
# manifest -- it only transfers ownership.
#
# Upgrades: edit targetRevision below, and the matching CHART_VERSION in
# bootstrap/argocd/bootstrap.sh, then commit. The two must never diverge, or
# re-running the recovery script would downgrade a self-managed upgrade.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argocd
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  project: default
  sources:
    - repoURL: https://argoproj.github.io/argo-helm
      chart: argo-cd
      targetRevision: 10.5.0
      helm:
        # MUST match the Helm release name from bootstrap.sh, or Argo CD
        # renders differently-named resources and duplicates the install.
        releaseName: argocd
        valueFiles:
          - $values/bootstrap/argocd/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      # Argo CD's own CRDs exceed the last-applied-configuration annotation
      # size limit. Without this, sync fails on CRD apply.
      - ServerSideApply=true
```

No finalizer, deliberately: deleting this Application must not uninstall Argo CD.

- [ ] **Step 2: Preview what adoption will change**

Adoption is supposed to be a no-op in rendered output. Confirm that before letting it run, rather than discovering a diff by watching Argo CD restart itself.

Compare rendered manifests, not values — rendered output is what actually reaches the cluster.

```bash
helm get manifest argocd -n argocd \
  | grep -v '^# Source:' | grep -v '^\s*$' > /tmp/live-manifest.yaml

helm template argocd argo/argo-cd \
  --version 10.5.0 \
  --namespace argocd \
  --values bootstrap/argocd/values.yaml \
  | grep -v '^# Source:' | grep -v '^\s*$' > /tmp/file-manifest.yaml

diff /tmp/live-manifest.yaml /tmp/file-manifest.yaml && echo "MANIFESTS IDENTICAL"
```

Expected: `MANIFESTS IDENTICAL`.

Do not treat a non-empty diff as automatic failure — **read it**. Differences confined to `helm.sh/chart` labels, checksum annotations, or resource ordering are benign rendering artifacts. Differences in container images, replica counts, arguments, or resource limits are real, mean the running release does not match the committed file, and must be resolved before adoption — otherwise the first sync silently redeploys Argo CD with different settings.

- [ ] **Step 2b: Clean up the comparison files**

```bash
rm -f /tmp/live-manifest.yaml /tmp/file-manifest.yaml
```

- [ ] **Step 3: Commit and push**

```bash
git add environments/homelab/apps/argocd.yaml
git commit -m "feat(argocd): manage Argo CD's own release via Argo CD"
git push
```

- [ ] **Step 4: Wait for root to pick it up**

No manual apply. If this file requires one, root is not doing its job and Task 6 was not actually complete.

`kubectl wait` errors immediately with `no matching resources found` if the object does not exist yet, so wait for creation before waiting for Synced.

```bash
# Wait for root to create the Application.
for i in $(seq 1 30); do
  kubectl -n argocd get application argocd >/dev/null 2>&1 && break
  sleep 10
done

kubectl -n argocd get application argocd \
  || kubectl -n argocd patch application root --type merge \
       -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'

kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced \
  application/argocd --timeout=300s
```
Expected: the Application appears without any manual apply, then reaches `Synced`.

- [ ] **Step 5: Verify adoption without divergence**

```bash
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
helm list -n argocd --output json | jq -r '.[] | "\(.name) \(.chart) \(.status)"'
kubectl -n argocd get pods
```
Expected: `argocd` Application `Synced`/`Healthy`; **exactly one** Helm release still reading `argocd argo-cd-10.5.0 deployed`; all pods `Running`.

Two releases, or a release plus separately-named `argocd-*` duplicates, means `helm.releaseName` did not match — stop and fix before continuing.

- [ ] **Step 6: Confirm Argo CD survives managing itself**

```bash
sleep 120
kubectl -n argocd get application argocd \
  -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
kubectl -n argocd get pods --field-selector=status.phase!=Running
```
Expected: `Synced Healthy`, and no non-Running pods. A self-managing Argo CD that oscillates shows up as repeated restarts a minute or two after adoption, not immediately.

---

## Task 8: Final verification and cleanup

Runs the spec's §8 acceptance checks as a set, and closes the loose ends the spec flagged.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a verified, documented platform ready for plan phase 13 (ingress).

- [ ] **Step 1: Run all five spec acceptance checks**

```bash
echo "--- 1. unprivileged kubectl ---"
kubectl get nodes

echo "--- 2. storage on /srv (verified in Task 1 step 7) ---"
kubectl get storageclass

echo "--- 3. applications healthy ---"
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status

echo "--- 4. selfHeal (verified in Task 6 step 8) ---"
kubectl -n argocd get application namespaces -o jsonpath='{.spec.syncPolicy.automated}{"\n"}'

echo "--- 5. self-management without divergence ---"
helm list -n argocd --output json | jq -r '.[] | "\(.name) \(.chart) \(.status)"'
```
Expected: node `Ready`; `local-path (default)`; `root`, `namespaces`, `argocd` all `Synced`/`Healthy`; selfHeal `true`; exactly one `argo-cd-10.5.0 deployed`.

- [ ] **Step 2: Retrieve the admin password and log in**

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
kubectl port-forward -n argocd svc/argocd-server 8080:80
```

Operator opens `http://localhost:8080`, logs in as `admin`, and changes the password via User Info → Update Password.

- [ ] **Step 3: Delete the initial password Secret**

Only after the password has actually been changed. This Secret holds a working credential in plaintext and is not regenerated.

```bash
kubectl -n argocd delete secret argocd-initial-admin-secret
```

- [ ] **Step 4: Remove the stale empty directory (operator runs)**

Spec §9 flags `/srv/kubernetes/manifests/argocd` as a second apparent home for Argo CD config that nothing writes to. Confirm it is empty, then remove it.

```
! sudo find /srv/kubernetes/manifests -type f
! sudo rmdir /srv/kubernetes/manifests/argocd /srv/kubernetes/manifests
```

If `find` prints any file, stop and report instead of deleting.

- [ ] **Step 5: Document the platform in the README**

`README.md` is currently the single line `# homelab`. Replace it:

```markdown
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
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: describe GitOps layout, app repo contract, and recovery"
git push
```

- [ ] **Step 7: Confirm the tree is clean and synced**

```bash
git status --porcelain
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```
Expected: no output from the first; all Applications `Synced`/`Healthy`.

---

## Deferred to later phases

Per spec §10, out of scope here and each needing its own design: ingress, DNS, TLS/cert-manager, Vault, Vault Secrets Operator, PostgreSQL, Redis, Artifactory, Prometheus, Grafana, logging, backup automation, and creating any application repository.

Two known gaps carried forward deliberately:

- The `repo-homelab` Secret is created out-of-band and is not reproducible from git. First candidate to move into Vault in plan phase 17.
- Argo CD runs with `server.insecure: true` and is reachable only by port-forward. Revisit in plan phases 13-15 when ingress and cert-manager land.
