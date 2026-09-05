# Vault Secrets Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A value written into Vault appears in a Kubernetes Secret with no
human action, authenticated by ServiceAccount rather than a stored credential.

**Architecture:** VSO 1.5.1 watches a `VaultStaticSecret` and writes Secret
`vault-canary` in namespace `vault`. It authenticates by minting a token for
ServiceAccount `vault-canary`, which Vault validates via TokenReview and maps to
a role granting read on one KV v2 path. Vault's own configuration — mount, auth
method, policy, role — comes from an idempotent script a human runs, because
automating it would require a standing Vault superuser credential in-cluster.

**Tech Stack:** k3s v1.36.4+k3s1, Argo CD 3.5.2, Vault 2.0.4, VSO chart 1.5.1.

**Spec:** `docs/superpowers/specs/2026-09-05-vault-secrets-operator-design.md`

## Global Constraints

- **Never run `git push`.** The user reserves all pushes. Commit locally, print
  the exact push command, and wait. Argo CD cannot see an unpushed commit.
- **`kubectl` needs a group wrapper on this host:** `sg k3s-admin -c '...'`.
  Nested quoting inside `sg ... -c '...'` is fragile — for anything multi-line,
  write a script to the scratchpad and run that.
- **Commands the user must edit and run must be shell-safe as written.** Never
  use `<angle brackets>` as a placeholder — `<` is a shell redirection and has
  broken this user's shell twice. Use a bare `PASTE_TOKEN_HERE` style token, and
  keep such commands on one line so there is no continuation to mangle.
- **The root token never enters the cluster** beyond the pod-local file
  `vault login` writes, which is removed at the end of the ceremony.
- **Chart pinned to `1.5.1`.** Images `hashicorp/vault-secrets-operator:1.5.1`
  and `quay.io/brancz/kube-rbac-proxy:v0.18.1`.
- **Sync waves after this plan:** `argocd` -1, `namespaces` 0,
  `ingress-operator` 2, `vault` 10, `vso-operator` 11, `ingress-config` 21,
  `vso-config` 22.
- **A directory used as an Argo CD `path` may contain only valid manifests.**
  A Helm values file has no `kind` and breaks the whole Application.
- **Argo CD reporting Synced/Healthy proves only what it applied.** Every
  verification step must assert on running state — `status.resources`, a live
  object, an actual authentication — never on sync status alone.
- Argo CD polls every 3 minutes. Poll for existence before `kubectl wait`,
  which errors immediately on a missing object.

---

### Task 1: Install the operator at sync-wave 11

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Create: `platform/vault-secrets-operator/values.yaml`
- Create: `environments/homelab/apps/vso-operator.yaml`

**Interfaces:**
- Produces: 10 CRDs in group `secrets.hashicorp.com`, Deployment
  `vso-vault-secrets-operator-controller-manager` in namespace
  `vault-secrets-operator-system`. Task 3's custom resources require these CRDs
  to exist, which is why they sit at a later wave.

**Expected outcome:** the operator runs and is Healthy. It does **not** need
Vault to be configured, unsealed, or even running — its health depends only on
its own pods starting.

- [ ] **Step 1: Record memory before**

```bash
free -h
sg k3s-admin -c 'kubectl get pods -A --no-headers | wc -l'
```

- [ ] **Step 2: Add the namespace**

Add a third document to `bootstrap/namespaces/namespaces.yaml`, matching the
style of the existing entries:

```yaml
---
# Chart default namespace for the Vault Secrets Operator. Created here at
# wave 0 rather than by CreateNamespace=true, so that every namespace in this
# cluster has exactly one origin.
apiVersion: v1
kind: Namespace
metadata:
  name: vault-secrets-operator-system
```

- [ ] **Step 3: Create the Helm values**

Create `platform/vault-secrets-operator/values.yaml`:

```yaml
# Vault Secrets Operator -- the path by which a workload obtains a secret from
# Vault without anyone copying a value by hand.
# Chart: hashicorp/vault-secrets-operator 1.5.1 (app 1.5.1)
# Repo:  https://helm.releases.hashicorp.com
#
# The VaultConnection and VaultAuth objects are deliberately NOT created here.
# The chart can emit them, but they are committed as manifests in config/
# instead so they land at sync-wave 22, after ingress: their health depends on
# Vault configuration a human applies, and nothing that gates ingress may
# depend on that. See the design spec section 6.
defaultVaultConnection:
  enabled: false
defaultAuthMethod:
  enabled: false

controller:
  replicas: 1

  manager:
    # Reconciles one VaultStaticSecret today. The chart's default request of
    # 64Mi is more than Vault itself measured at (62Mi).
    resources:
      requests:
        cpu: 10m
        memory: 32Mi
      limits:
        memory: 96Mi

  kubeRbacProxy:
    # Mandatory in 1.5.1 -- no flag disables it. It fronts the metrics
    # endpoint, which nothing scrapes until phase 22.
    resources:
      requests:
        cpu: 5m
        memory: 16Mi
      limits:
        memory: 64Mi

hooks:
  upgradeCRDs:
    # Off: this is a Helm-specific workaround for Helm not upgrading files in
    # a chart's crds/ directory. Argo CD renders with --include-crds and
    # applies them as ordinary manifests, so the Job is redundant -- and as a
    # pre-upgrade hook it would run as a PreSync Job on every single sync.
    # If Argo CD's skipCrds is ever turned on for this Application, this must
    # be turned back on with it.
    enabled: false
```

- [ ] **Step 4: Create the Application**

Create `environments/homelab/apps/vso-operator.yaml`:

```yaml
# Vault Secrets Operator: the controller and its CRDs.
#
# Wave 11 -- immediately after Vault (10). Safe there because this
# Application's health depends only on its own pods starting, not on Vault
# being configured or unsealed. Its custom resources are a separate
# Application at wave 22; see environments/homelab/apps/vso-config.yaml for
# why they are not next to it.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: vso-operator
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "11"
spec:
  project: default
  sources:
    - repoURL: https://helm.releases.hashicorp.com
      chart: vault-secrets-operator
      targetRevision: 1.5.1
      helm:
        # Pinned deliberately: it prefixes every object name the chart
        # creates. Nothing in Vault's configuration depends on it (the
        # authenticating ServiceAccount is our own -- see the design spec
        # section 9), but a change here still renames live objects.
        releaseName: vso
        valueFiles:
          - $values/platform/vault-secrets-operator/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: vault-secrets-operator-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 5: Confirm the values file renders and emits no connection objects**

`$SCRATCH` is this session's scratchpad directory; export it first so nothing
lands in `/tmp`:

```bash
export SCRATCH=/tmp/claude-1000/-srv-projects-homelab/c141665b-8004-4cff-a023-6b3a5f744b42/scratchpad
helm repo add hashicorp https://helm.releases.hashicorp.com >/dev/null 2>&1
helm repo update hashicorp >/dev/null 2>&1
helm template vso hashicorp/vault-secrets-operator --version 1.5.1 \
  -n vault-secrets-operator-system \
  -f platform/vault-secrets-operator/values.yaml --include-crds > "$SCRATCH/vso-render.yaml"
python3 -c "
import yaml, os
from collections import Counter
docs=[d for d in yaml.safe_load_all(open(os.environ['SCRATCH']+'/vso-render.yaml')) if d]
print('kinds:', dict(Counter(d['kind'] for d in docs)))
print('CRDs:', sum(1 for d in docs if d['kind']=='CustomResourceDefinition'))
print('chart-emitted VaultConnection/VaultAuth:', [d['kind'] for d in docs if d['kind'] in ('VaultConnection','VaultAuth')] or 'none (correct)')
print('pre-upgrade Job:', [d['metadata']['name'] for d in docs if d['kind']=='Job' and (d['metadata'].get('annotations') or {}).get('helm.sh/hook')=='pre-upgrade'] or 'none (correct)')
"
```

Expected: 10 CRDs; no `VaultConnection` or `VaultAuth`; no pre-upgrade Job.

- [ ] **Step 6: Commit**

```bash
git add bootstrap/namespaces/namespaces.yaml platform/vault-secrets-operator/values.yaml environments/homelab/apps/vso-operator.yaml
git commit -m "Install the Vault Secrets Operator at sync-wave 11

Its health depends only on its own pods starting, so it is safe before
ingress at wave 21. Its custom resources are a separate Application at
wave 22 because theirs is not.

The chart's CRD-upgrade hook is disabled: Argo CD applies CRDs itself
with --include-crds, so the Job is redundant and would otherwise run as
a PreSync Job on every sync."
```

- [ ] **Step 7: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 8: Verify against running state, not sync status**

```bash
for i in $(seq 1 60); do
  sg k3s-admin -c 'kubectl -n vault-secrets-operator-system get deploy' >/dev/null 2>&1 && break
  sleep 5
done
sg k3s-admin -c 'kubectl -n vault-secrets-operator-system get pods,deploy'
sg k3s-admin -c 'kubectl get crd -o name' | grep secrets.hashicorp.com | wc -l
sg k3s-admin -c 'kubectl -n argocd get applications -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
```

Expected: the controller pod 2/2 Running; **10** CRDs in
`secrets.hashicorp.com`; all Applications Synced/Healthy.

- [ ] **Step 9: Record memory after**

```bash
free -h
sg k3s-admin -c 'kubectl get pods -A --no-headers | wc -l'
sg k3s-admin -c 'kubectl top pod -n vault-secrets-operator-system'
```

Report the measured delta against Step 1. These figures go into the README in
Task 5 — measured, never estimated.

---

### Task 2: Configure Vault

**Files:**
- Create: `platform/vault/configure-vault.sh`

**Interfaces:**
- Consumes: a running, unsealed Vault.
- Produces: KV v2 mounted at `homelab/`, secret `homelab/canary`, the
  `kubernetes` auth method, policy `vso-canary-read`, and role `vso-canary`
  bound to `system:serviceaccount:vault:vault-canary` with audience `vault`.
  Task 3's `VaultAuth` must name exactly that role and audience.

**Why this is a script and not a Job.** Automating it requires a privileged
Vault token living in the cluster — a standing superuser credential, strictly
worse than today, where the root token exists only in the user's password
manager. Argo CD does **not** reconcile this file, exactly as it does not
reconcile `infrastructure/networking/policy.hujson`.

- [ ] **Step 1: Confirm the ServiceAccount name this must match**

The script binds a Vault role to a ServiceAccount that Task 3 creates. Both
must agree on one string. Record it now so the comment in each file is right:

```bash
grep -rn "vault-canary" docs/superpowers/specs/2026-09-05-vault-secrets-operator-design.md | head -5
```

The name is `vault-canary`, in namespace `vault`.

- [ ] **Step 2: Write the script**

Create `platform/vault/configure-vault.sh`:

```sh
#!/bin/sh
# Vault configuration for the homelab. Run INSIDE the vault-0 pod.
#
# ARGO CD DOES NOT RECONCILE THIS FILE. Vault's mounts, auth methods,
# policies and roles are not Kubernetes objects; nothing reconciles them.
# This script is the versioned *intent*, applied by hand. It is the same
# arrangement as infrastructure/networking/policy.hujson.
#
# It is idempotent: every step either checks before acting or overwrites with
# the same value, so a rebuild can re-run it safely.
#
# How to run it -- see platform/vault-secrets-operator/README.md:
#   1. sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
#   2. sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
#   3. sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
#
# The token is entered at a hidden prompt in step 1 and removed in step 3. It
# is never an argument, never an environment variable, and never in history.
set -eu

echo "==> checking Vault is reachable and unsealed"
vault status >/dev/null

echo "==> KV v2 at homelab/"
if vault secrets list | grep -q '^homelab/'; then
  echo "    already mounted"
else
  vault secrets enable -path=homelab -version=2 kv
fi

echo "==> seeding homelab/canary"
if vault kv get homelab/canary >/dev/null 2>&1; then
  echo "    already present, leaving its value alone"
else
  vault kv put homelab/canary value=canary-ok
fi

echo "==> kubernetes auth method"
if vault auth list | grep -q '^kubernetes/'; then
  echo "    already enabled"
else
  vault auth enable kubernetes
fi

echo "==> kubernetes auth config"
# kubernetes_host is the only setting needed when Vault runs in-cluster: it
# uses its own ServiceAccount token as the reviewer JWT and the pod's CA
# bundle. Vault's ServiceAccount already holds system:auth-delegator, granted
# by the Vault chart, so it may call TokenReview.
vault write auth/kubernetes/config \
    kubernetes_host="https://kubernetes.default.svc.cluster.local"

echo "==> policy vso-canary-read"
# The data/ segment is REQUIRED and is not a typo. KV v2 stores values one
# level below the mount, so a policy on homelab/canary matches nothing and
# produces a permission denial that reads exactly like a wrong path.
vault policy write vso-canary-read - <<'POLICY'
path "homelab/data/canary" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-canary"
# bound_service_account_names must match the ServiceAccount created in
# platform/vault-secrets-operator/config/vault-secrets.yaml, and audience must
# match that file's VaultAuth spec.kubernetes.audiences. A mismatch in either
# is a permission denial naming neither side.
vault write auth/kubernetes/role/vso-canary \
    bound_service_account_names=vault-canary \
    bound_service_account_namespaces=vault \
    audience=vault \
    token_policies=vso-canary-read \
    ttl=1h

echo "==> done"
```

- [ ] **Step 3: Check the script parses and is executable**

```bash
sh -n platform/vault/configure-vault.sh && echo "syntax OK"
chmod +x platform/vault/configure-vault.sh
grep -c 'homelab/data/canary' platform/vault/configure-vault.sh
```

Expected: `syntax OK`, and exactly `1` occurrence of the `data/` path. A policy
written against `homelab/canary` is the single most common KV v2 mistake.

- [ ] **Step 4: Commit**

```bash
git add platform/vault/configure-vault.sh
git commit -m "Add the Vault configuration ceremony as an idempotent script

Vault's mounts, auth methods, policies and roles are not Kubernetes
objects, so Argo CD cannot reconcile them. Automating this would require
a privileged Vault token living in the cluster; the script keeps the root
token in the user's password manager and takes it at a hidden prompt.

Every step checks before acting so a rebuild can re-run it."
```

- [ ] **Step 5: Hand the ceremony to the user**

This is user-executed. Print these three commands, one line each, and explain
that the first prompts for the root token with hidden input:

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

The script is not yet pushed, and does not need to be — it is piped from the
working tree, not read from git by anything.

- [ ] **Step 6: Verify Vault's state directly**

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault secrets list' | grep -E '^homelab/|^Path'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault auth list' | grep -E '^kubernetes/|^Path'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault policy list'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault read auth/kubernetes/role/vso-canary'
```

Expected: `homelab/` is `kv` version 2; `kubernetes/` is present;
`vso-canary-read` is in the policy list; the role shows
`bound_service_account_names [vault-canary]`,
`bound_service_account_namespaces [vault]`, `audience vault`, and
`token_policies [vso-canary-read]`.

- [ ] **Step 7: Confirm the token file is gone**

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- ls -la /home/vault/.vault-token' 2>&1 | tail -1
```

Expected: `No such file or directory`. If the file is still there, the root
token is sitting in the pod and step 3 of the ceremony was missed.

---

### Task 3: The custom resources at sync-wave 22

**Files:**
- Create: `platform/vault-secrets-operator/config/vault-secrets.yaml`
- Create: `environments/homelab/apps/vso-config.yaml`

**Interfaces:**
- Consumes: the CRDs from Task 1; role `vso-canary` and audience `vault` from
  Task 2.
- Produces: Secret `vault-canary` in namespace `vault`, containing the value
  held at `homelab/canary`.

- [ ] **Step 1: Create the custom resources**

Create `platform/vault-secrets-operator/config/vault-secrets.yaml`. Note the
apiVersion is `secrets.hashicorp.com/v1beta1`, and `VaultConnection` requires
**both** `address` and `skipTLSVerify`:

```yaml
# The Vault -> Kubernetes Secret path, in four objects.
#
# All four live in namespace `vault`, and that is a requirement rather than a
# preference: the VaultAuth CRD states that its ServiceAccount "must reside in
# the consuming secret's namespace".
---
# The identity Vault trusts. This is NOT incidental scaffolding -- it is the
# thing authenticated. Its name must match bound_service_account_names in
# platform/vault/configure-vault.sh.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: vault-canary
  namespace: vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: vault
  namespace: vault
spec:
  # Plain HTTP in-cluster, matching the posture used for Argo CD and Vault's
  # own UI: single node, so pod traffic never leaves the host. TLS terminates
  # at the tailnet proxy. skipTLSVerify is required by the CRD even when the
  # address is http, where it has no effect.
  address: http://vault.vault.svc:8200
  skipTLSVerify: false
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultAuth
metadata:
  name: vault-canary
  namespace: vault
spec:
  vaultConnectionRef: vault
  method: kubernetes
  mount: kubernetes
  kubernetes:
    # role and audiences must both match platform/vault/configure-vault.sh.
    # A mismatch in either is a permission denial that names neither side.
    role: vso-canary
    serviceAccount: vault-canary
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: vault-canary
  namespace: vault
spec:
  vaultAuthRef: vault-canary
  mount: homelab
  type: kv-v2
  path: canary
  refreshAfter: 60s
  destination:
    name: vault-canary
    create: true
```

- [ ] **Step 2: Create the Application**

Create `environments/homelab/apps/vso-config.yaml`:

```yaml
# The Vault -> Kubernetes Secret path: ServiceAccount, VaultConnection,
# VaultAuth, VaultStaticSecret.
#
# Wave 22 -- AFTER ingress-config (21), which looks backwards next to its
# operator at wave 11 and is deliberate. A VaultStaticSecret only becomes
# Healthy once Vault holds the KV mount, the policy and the role, and those
# come from a script a human runs (platform/vault/configure-vault.sh). At an
# earlier wave this Application would gate wave 21, so a rebuild where someone
# unsealed Vault but forgot the script would silently kill every tailnet URL,
# with nothing naming the script as the cause.
#
# That is the failure phase 16 removed by moving ingress to wave 21.
# Re-introducing it one phase later would be an unforced error, and nothing
# consumes the canary, so the late placement costs nothing.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: vso-config
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "22"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: platform/vault-secrets-operator/config
  destination:
    server: https://kubernetes.default.svc
    namespace: vault
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 3: Confirm the config directory holds only manifests**

```bash
for f in platform/vault-secrets-operator/config/*; do
  printf "%s -> " "$f"
  python3 -c "import yaml;print([d['kind'] for d in yaml.safe_load_all(open('$f')) if d])"
done
```

Expected: one file resolving to
`['ServiceAccount', 'VaultConnection', 'VaultAuth', 'VaultStaticSecret']`.

- [ ] **Step 4: Confirm both sides agree on role, ServiceAccount and audience**

The two files that must agree are a shell script and a manifest, so compare
them mechanically rather than by eye:

```bash
echo "--- from the script ---"
grep -E 'bound_service_account_names|bound_service_account_namespaces|audience=|auth/kubernetes/role/' platform/vault/configure-vault.sh
echo "--- from the manifests ---"
grep -E 'role:|serviceAccount:|- vault$|name: vault-canary' platform/vault-secrets-operator/config/vault-secrets.yaml
```

Expected: role `vso-canary` on both sides; ServiceAccount `vault-canary`;
namespace `vault`; audience `vault`.

- [ ] **Step 5: Commit**

```bash
git add platform/vault-secrets-operator/config/vault-secrets.yaml environments/homelab/apps/vso-config.yaml
git commit -m "Add the Vault to Kubernetes Secret path at sync-wave 22

Wave 22 is after ingress deliberately: a VaultStaticSecret cannot go
Healthy until a human has configured Vault, and an Application that can
be blocked by a forgotten manual step must never gate ingress.

The authenticating ServiceAccount lives in the vault namespace because
the VaultAuth CRD requires it to reside in the consuming secret's
namespace."
```

- [ ] **Step 6: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 7: Verify the Application manages what it should**

Synced is not enough — check the resource list, which is what caught a manifest
applied by nothing in phase 16:

```bash
for i in $(seq 1 60); do
  sg k3s-admin -c 'kubectl -n argocd get application vso-config' >/dev/null 2>&1 && break
  sleep 5
done
export SCRATCH=/tmp/claude-1000/-srv-projects-homelab/c141665b-8004-4cff-a023-6b3a5f744b42/scratchpad
sg k3s-admin -c 'kubectl -n argocd get application vso-config -o json' > "$SCRATCH/vso-config.json"
python3 -c "
import json, os
d=json.load(open(os.environ['SCRATCH']+'/vso-config.json'))
st=d.get('status',{})
print('sync:',st.get('sync',{}).get('status'),'health:',st.get('health',{}).get('status'))
names=[f\"{r.get('kind')}/{r.get('name')}\" for r in st.get('resources',[])]
for n in names: print('  ',n)
for want in ['ServiceAccount/vault-canary','VaultConnection/vault','VaultAuth/vault-canary','VaultStaticSecret/vault-canary']:
    print(('PRESENT     ' if want in names else '*** MISSING *** ')+want)
"
```

Expected: all four present.

- [ ] **Step 8: Verify the Secret actually exists**

```bash
for i in $(seq 1 24); do
  sg k3s-admin -c 'kubectl -n vault get secret vault-canary' >/dev/null 2>&1 && break
  sleep 5
done
sg k3s-admin -c 'kubectl -n vault get secret vault-canary'
sg k3s-admin -c 'kubectl -n vault get vaultstaticsecret vault-canary -o jsonpath={.status}'; echo
```

Expected: the Secret exists; the `VaultStaticSecret` status shows it is valid.

**If the Secret does not appear**, read VSO's logs before changing anything —
they name the cause, and the two most likely are a policy missing the `data/`
segment and an audience mismatch:

```bash
sg k3s-admin -c 'kubectl -n vault-secrets-operator-system logs deploy/vso-vault-secrets-operator-controller-manager --tail=40'
```

---

### Task 4: Prove it is a live path, not a one-time copy

**Files:** none. This task only observes.

**Interfaces:**
- Consumes: everything from Tasks 1-3.

**Why this task exists.** A Secret containing the right value proves only that
something wrote it once. The spec's §14 asks for evidence that VSO is
maintaining it, that the identity is a ServiceAccount rather than a stored
credential, and that the ceremony can be re-run.

- [ ] **Step 1: Confirm the value matches what is in Vault**

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault kv get -field=value homelab/canary'
sg k3s-admin -c 'kubectl -n vault get secret vault-canary -o jsonpath={.data.value}' | base64 -d; echo
```

Expected: both print `canary-ok`. This value is deliberately not sensitive, so
printing it is safe — unlike the unseal keys, which are never printed.

- [ ] **Step 2: Change the value in Vault and watch the Secret follow**

This is the check that distinguishes a managed path from a copy:

```bash
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' <<'EOF'
vault kv put homelab/canary value=canary-updated
EOF
for i in $(seq 1 30); do
  V=$(sg k3s-admin -c 'kubectl -n vault get secret vault-canary -o jsonpath={.data.value}' | base64 -d)
  [ "$V" = "canary-updated" ] && { echo "Secret followed after ~$((i*5))s"; break; }
  sleep 5
done
sg k3s-admin -c 'kubectl -n vault get secret vault-canary -o jsonpath={.data.value}' | base64 -d; echo
```

Expected: `canary-updated` within roughly `refreshAfter` (60s).

**This step needs a Vault token**, since writing requires authentication. Hand
the `vault login` to the user as in Task 2, or ask them to run the whole step.
Do not put a token on a command line.

- [ ] **Step 3: Delete the Secret and confirm VSO recreates it**

```bash
sg k3s-admin -c 'kubectl -n vault delete secret vault-canary'
for i in $(seq 1 30); do
  sg k3s-admin -c 'kubectl -n vault get secret vault-canary' >/dev/null 2>&1 && { echo "recreated after ~$((i*5))s"; break; }
  sleep 5
done
sg k3s-admin -c 'kubectl -n vault get secret vault-canary'
```

Expected: recreated with no human action.

- [ ] **Step 4: Confirm the identity is a ServiceAccount, not a stored token**

```bash
sg k3s-admin -c 'kubectl -n vault-secrets-operator-system logs deploy/vso-vault-secrets-operator-controller-manager --tail=60' | grep -iE "auth|login|kubernetes" | tail -10
sg k3s-admin -c 'kubectl get secrets -A -o json' | grep -ciE '"vault[_-]?token"|hvs\.' || echo "0 Vault tokens in any Secret (correct)"
```

Expected: the logs show `kubernetes` authentication; **zero** Vault tokens
stored in any Kubernetes Secret. No audit device is enabled, so VSO's own logs
are the evidence.

- [ ] **Step 5: Prove the ceremony is idempotent**

Ask the user to re-run the three ceremony commands from Task 2 Step 5. Then:

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault kv get -field=value homelab/canary'
sg k3s-admin -c 'kubectl -n vault get secret vault-canary -o jsonpath={.data.value}' | base64 -d; echo
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault read auth/kubernetes/role/vso-canary' | grep -E 'bound_service_account|audience|token_policies'
```

Expected: the script exits cleanly, the canary keeps the value from Step 2
(`canary-updated`, since the script only seeds when absent), and the role is
unchanged. A script that reset the canary would not be idempotent.

- [ ] **Step 6: Confirm nothing else regressed**

```bash
sg k3s-admin -c 'kubectl -n argocd get applications -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
printf "argocd ingress uid: "; sg k3s-admin -c 'kubectl -n argocd get ingress argocd -o jsonpath={.metadata.uid}'; echo
curl -sS  --max-time 45 -o /dev/null -w 'argocd -> %{http_code} tls_verify=%{ssl_verify_result}\n' https://argocd.taildf6cd4.ts.net
curl -sSL --max-time 45 -o /dev/null -w 'vault  -> %{http_code} tls_verify=%{ssl_verify_result}\n' https://vault.taildf6cd4.ts.net
free -h | head -2
```

Expected: all 8 Applications Synced/Healthy; the Argo CD Ingress UID unchanged
at `8b33ddee-4c43-4dda-a1da-c5c28d7106ca`; both URLs 200 with `tls_verify=0`.

---

### Task 5: Documentation

**Files:**
- Create: `platform/vault-secrets-operator/README.md`
- Modify: `platform/vault/README.md`
- Modify: `README.md`
- Modify: `docs/troubleshooting.md`
- Modify: `docs/superpowers/specs/2026-09-03-vault-design.md`

**Interfaces:**
- Consumes: the measured figures from Tasks 1 and 4.

- [ ] **Step 1: Write the component README**

Create `platform/vault-secrets-operator/README.md` covering:

- What the path is, in one diagram: Vault KV → VSO → Secret → workload.
- **The ceremony**, as the three one-line commands from Task 2 Step 5, with the
  note that the first prompts for the root token with hidden input and the
  third removes the pod-local copy.
- The four objects and why the ServiceAccount is among them rather than being
  scaffolding — the VaultAuth CRD requires it in the consuming secret's
  namespace.
- **The three strings that must agree** across a script and a manifest: role
  `vso-canary`, ServiceAccount `vault-canary`, audience `vault`. State that a
  mismatch in any of them is a permission denial naming neither side, and that
  Argo CD will report everything Synced while it happens.
- Measured resource usage from Task 1 Step 9. Observed, not estimated.
- That `configure-vault.sh` is not reconciled by Argo CD, as loudly as
  `infrastructure/networking/README.md` says it for the tailnet policy.

- [ ] **Step 2: Extend the Vault ceremony**

In `platform/vault/README.md`, the init ceremony currently ends when the
unsealer unseals Vault. Add a final step: run `configure-vault.sh`, pointing at
`platform/vault-secrets-operator/README.md`. A rebuild that unseals but does not
configure leaves `vso-config` unhealthy at wave 22.

- [ ] **Step 3: Update the root README**

- Add `platform/vault-secrets-operator/` to the Layout table.
- Add the wave to the ordering note: platform is 10-11, `ingress-config` 21,
  `vso-config` 22.
- In "First install / rebuild", extend the step added in phase 16 so the
  ceremony is unseal **and** configure. Say plainly that skipping the second
  half leaves `vso-config` unhealthy but — because it sits at wave 22 — does
  **not** cost any tailnet URL. That contrast is the whole reason for the wave.

- [ ] **Step 4: Add troubleshooting entry 10**

Append a new numbered entry, `Secret from Vault is missing or stale`, leading
with the symptom. It must cover, in this order:

1. **Is the Secret there at all?** `kubectl -n vault get secret vault-canary`.
2. **What does VSO say?** Its logs name the cause; check them before changing
   anything.
3. **The `data/` segment.** A policy on `homelab/canary` instead of
   `homelab/data/canary` denies access with a message that reads like a wrong
   path. State this first among causes — it is the most common KV v2 error.
4. **Audience and ServiceAccount mismatch.** The script and the manifest must
   agree on `vso-canary`, `vault-canary` and `vault`. Give the two `grep`
   commands from Task 3 Step 4.
5. **Was the ceremony run at all?** `vault auth list` showing no `kubernetes/`
   means `configure-vault.sh` never ran on this cluster.
6. Close with the general point already established in entry 9: a Synced
   Application says only what Argo CD applied. Here it will report all four
   objects healthy while authentication fails.

- [ ] **Step 5: Correct the phase-16 spec**

`docs/superpowers/specs/2026-09-03-vault-design.md` §11 says `operator-oauth`
"can migrate, but only once phase 17 provides the Vault Secrets Operator", and
predicts the cluster-only secret count falls from three to two.

Add a dated correction note — do not silently rewrite it — recording that it
cannot migrate: the Tailscale operator mounts it at wave 2, Vault does not exist
until wave 10, and wave 2 gates wave 10, so a rebuild would never converge.
Point at the phase-17 spec §10. The count stays at three.

- [ ] **Step 6: Verify every documented command**

Run each command quoted in the new documentation, or confirm its syntax where
running it would be destructive. Report which were executed and which were only
syntax-checked. Documentation asserting an untested command is worse than none.

Also confirm no `<angle bracket>` placeholder appears in any command a reader is
expected to type.

- [ ] **Step 7: Commit**

```bash
git add platform/vault-secrets-operator/README.md platform/vault/README.md README.md docs/troubleshooting.md docs/superpowers/specs/2026-09-03-vault-design.md
git commit -m "Document the Vault to Kubernetes Secret path

Records the three strings that must agree across a shell script and a
manifest, and that a mismatch in any of them fails while Argo CD reports
everything Synced.

Corrects the phase-16 spec's claim that operator-oauth could migrate into
Vault once VSO existed. It cannot: the Tailscale operator mounts it at
wave 2 and Vault does not exist until wave 10."
```

- [ ] **Step 8: Hand the push to the user**

```bash
git push origin main
```

- [ ] **Step 9: Final state check**

```bash
git status --short
sg k3s-admin -c 'kubectl -n argocd get applications -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
sg k3s-admin -c 'kubectl -n vault get secret vault-canary'
sg k3s-admin -c 'kubectl -n vault-secrets-operator-system get pods'
free -h | head -2
```

Expected: clean tree; 8 Applications Synced/Healthy; the Secret present; the
controller 2/2 Running.
