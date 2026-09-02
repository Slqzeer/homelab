# Tailscale Ingress and Tailnet DNS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reach Argo CD at `https://argocd.taildf6cd4.ts.net` over the
tailnet with a browser-trusted certificate, replacing `kubectl port-forward`
as the everyday route, with every Kubernetes object reconciled by Argo CD.

**Architecture:** The Tailscale Kubernetes operator installs into the
`tailscale` namespace and registers `IngressClass: tailscale`. A single
`ProxyGroup` of `type: ingress` with one replica serves every exposed
Ingress, each getting its own tailnet hostname and Let's Encrypt
certificate. Argo CD's own Ingress lives with the ingress component rather
than in Argo CD's Helm values, to avoid a sync-wave deadlock on rebuild.

**Tech Stack:** k3s v1.36.4+k3s1, Argo CD 3.5.2 (chart 10.5.0), Tailscale
operator chart 1.102.3, Tailscale 1.102.3 on the host.

**Spec:** `docs/superpowers/specs/2026-09-02-tailscale-ingress-design.md`

## Global Constraints

- **Never run `git push`.** The user reserves all pushes. Commit locally,
  then print the exact push command and wait for the user to confirm.
  Argo CD cannot see a commit until they push.
- **`kubectl` may need a group wrapper.** If `kubectl get nodes` fails with
  a permission error on `/etc/rancher/k3s/k3s.yaml`, prefix every command
  with `sg k3s-admin -c '...'`. Test once at task start. Nested quoting
  inside `sg ... -c '...'` is fragile — for multi-line commands, write a
  script to the scratchpad and run that.
- **No credentials in git, ever.** The OAuth client ID and secret go only
  into a hand-created Kubernetes Secret. Never ask the user to paste them
  into the conversation.
- **Chart version pinned to `1.102.3`** — matches host Tailscale 1.102.3.
- **`ProxyGroup.spec.replicas` MUST be `1` explicitly.** The CRD default
  is 2, which doubles memory on a host that has none to spare.
- **`ProxyClass` and `ProxyGroup` are cluster-scoped.** Their manifests
  carry no `namespace:` field.
- **The Argo CD Ingress must NOT be created via `server.ingress` in
  `bootstrap/argocd/values.yaml`.** See spec §7 — it deadlocks a rebuild.
- **`infrastructure/ingress/config/` must contain only valid Kubernetes
  manifests.** The `ingress-config` Application applies every YAML in that
  directory; a Helm values file there would be applied as an object.
- **Sync waves:** `argocd` -1, `namespaces` 0, `ingress-operator` 2,
  `ingress-config` 3.
- **Host memory is critical:** 1.1Gi available, swap fully exhausted.
  Measure with `free -h` before and after any install.

---

### Task 1: Add the `tailscale` namespace

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`

**Interfaces:**
- Produces: Namespace `tailscale`, required by Task 3 (the Secret) and
  Task 4 (the operator).

- [ ] **Step 1: Confirm kubectl works and record the starting state**

```bash
kubectl get ns tailscale 2>&1 || echo "ABSENT (expected)"
free -h
```

Expected: `Error from server (NotFound)` or `ABSENT (expected)`. Record the
`free -h` output — it is the baseline for Task 4.

- [ ] **Step 2: Replace the file header comment and add the namespace**

The existing header names only cert-manager and Vault. Replace the whole
file with:

```yaml
# Namespaces for components managed from this repository.
# Add a namespace here when the component that needs it is planned, not before.
#
# The `namespaces` Application has prune: true. Removing an entry from this
# file DELETES the Namespace and cascades to every resource inside it,
# including PVCs, once Vault lands. Retire the component first.
apiVersion: v1
kind: Namespace
metadata:
  name: cert-manager
---
apiVersion: v1
kind: Namespace
metadata:
  name: vault
---
# Tailscale Kubernetes operator and the proxy pods it creates.
# Also holds the hand-created `operator-oauth` Secret, which exists in no
# repository -- see infrastructure/ingress/README.md.
apiVersion: v1
kind: Namespace
metadata:
  name: tailscale
```

- [ ] **Step 3: Commit**

```bash
git add bootstrap/namespaces/namespaces.yaml
git commit -m "Add tailscale namespace for the Kubernetes operator"
```

- [ ] **Step 4: Hand the push to the user and wait**

Print exactly this and stop until they confirm:

```bash
git push origin main
```

- [ ] **Step 5: Verify the namespace appears**

Argo CD polls every 3 minutes. Poll for up to 4 minutes:

```bash
for i in $(seq 1 48); do
  kubectl get ns tailscale >/dev/null 2>&1 && { echo "PRESENT"; break; }
  sleep 5
done
kubectl get ns tailscale
kubectl -n argocd get application namespaces \
  -o custom-columns=SYNC:.status.sync.status,HEALTH:.status.health.status
```

Expected: namespace `Active`; Application `Synced` / `Healthy`.

If it has not appeared after 4 minutes, force a refresh rather than
applying anything by hand:

```bash
kubectl -n argocd patch application namespaces --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
```

---

### Task 2: Record the tailnet policy in the repository

**Files:**
- Create: `infrastructure/networking/policy.hujson`
- Create: `infrastructure/networking/README.md`
- Delete: `infrastructure/networking/.gitkeep`

**Interfaces:**
- Produces: nothing consumed by later tasks. This task has **no effect on
  the cluster** and is documentation-grade only.

**Context for the implementer:** the tailnet policy already contains the
`tag:k8s-operator` and `tag:k8s` tag owners — the user added them before
creating the OAuth client, because the console will not attach a tag that
does not exist. This task records what is already live; it does not change
Tailscale's configuration.

- [ ] **Step 1: Ask the user for the live policy, verbatim**

The file must be a byte-accurate copy. Applying a policy file **replaces
the live policy wholesale** — it is not a merge — so a reconstructed or
partial file would delete the tag owners the operator depends on.

Ask the user to copy the **entire** contents of
`https://login.tailscale.com/admin/acls`, including comments and any
commented-out sections, and paste it. Do not write this file from a
fragment, and do not infer the parts you were not shown.

- [ ] **Step 2: Write the file exactly as pasted**

Save the paste unmodified to `infrastructure/networking/policy.hujson`.
Change nothing — not formatting, not comments, not trailing commas.
HuJSON permits comments and trailing commas that strict JSON does not.

- [ ] **Step 3: Verify the required tag owners are present**

```bash
grep -n "tag:k8s-operator" infrastructure/networking/policy.hujson
grep -n "tag:k8s\"" infrastructure/networking/policy.hujson
grep -n "autoApprovers" infrastructure/networking/policy.hujson
```

All three must match. Without `autoApprovers.services`, a ProxyGroup
Ingress resolves in MagicDNS but is unreachable — the Tailscale Service it
publishes is advertised but never auto-approved. The policy must contain
a `tagOwners` block equivalent to:

```hujson
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s":          ["tag:k8s-operator"],
},
```

If either tag is missing, **stop and tell the user** — their OAuth client
could not have been created against a tag that is absent, so either the
paste is incomplete or something differs from the assumption above.

- [ ] **Step 4: Write the README that prevents a dangerous mistake**

Create `infrastructure/networking/README.md`:

```markdown
# Tailnet networking

## `policy.hujson` is NOT reconciled by Argo CD

This is the only file in this repository that Argo CD does not apply and
cannot apply. It targets Tailscale's control plane, not the Kubernetes
API. **Committing and pushing it changes nothing.**

To apply a change:

1. Open <https://login.tailscale.com/admin/acls>
2. Paste the file contents
3. Save in the console

Until phase 20 wires up `tailscale/gitops-acl-action` in GitHub Actions,
this file is a record of what should be live, kept under review, and
applied by hand. It can drift. Treat the console as authoritative and
this file as the reviewed copy.

## Applying replaces everything

The policy file is not merged into the live policy — it **replaces** it.
Before editing, copy the live policy into this file first, so an apply
cannot delete rules that were added in the console.

## What the cluster depends on

The Tailscale Kubernetes operator will not work without these tag owners:

    "tagOwners": {
      "tag:k8s-operator": [],
      "tag:k8s":          ["tag:k8s-operator"],
    },

The operator identifies as `tag:k8s-operator`. Proxy pods it creates are
tagged `tag:k8s`, owned by the operator so it can create them unattended.
Removing either breaks all tailnet ingress.

Automating this in phase 20 needs a second OAuth client with `policy_file`
write scope, separate from the operator's.
```

- [ ] **Step 5: Remove the placeholder and commit**

```bash
git rm -q infrastructure/networking/.gitkeep
git add infrastructure/networking/policy.hujson infrastructure/networking/README.md
git commit -m "Record tailnet policy and document that Argo CD cannot apply it"
```

- [ ] **Step 6: Hand the push to the user**

```bash
git push origin main
```

No cluster verification applies — this task deliberately changes nothing
in Kubernetes.

---

### Task 3: Create the `operator-oauth` Secret

**Files:** none. This task creates cluster-only state by hand.

**Interfaces:**
- Consumes: Namespace `tailscale` from Task 1.
- Produces: Secret `operator-oauth` in namespace `tailscale`, with keys
  `client_id` and `client_secret`. Task 4's operator Deployment mounts it
  at `/oauth` and will not start without it.

- [ ] **Step 1: Confirm the namespace exists**

```bash
kubectl get ns tailscale
```

Expected: `Active`. If absent, Task 1 is not finished — stop.

- [ ] **Step 2: Have the USER create the Secret**

Do not ask for the credential values and do not run this yourself with
real values. Print this for the user to run, with their own OAuth client
ID and secret substituted:

```bash
kubectl create secret generic operator-oauth -n tailscale \
  --from-literal=client_id=<their client id> \
  --from-literal=client_secret=<their client secret>
```

The key names `client_id` and `client_secret` are fixed by the chart —
the Deployment reads `/oauth/client_id` and `/oauth/client_secret` via the
`CLIENT_ID_FILE` and `CLIENT_SECRET_FILE` environment variables. Any other
key name produces a pod that starts and then fails to authenticate.

- [ ] **Step 3: Verify shape without revealing values**

```bash
kubectl -n tailscale get secret operator-oauth \
  -o jsonpath='{range .data.*}{"\n"}{end}' >/dev/null 2>&1
kubectl -n tailscale get secret operator-oauth -o jsonpath='{.data}' \
  | tr ',' '\n' | grep -oE '"[a-z_]+"' | sort -u
```

Expected output: exactly `"client_id"` and `"client_secret"`. Never print
the values.

- [ ] **Step 4: Confirm both values are non-empty**

```bash
kubectl -n tailscale get secret operator-oauth \
  -o jsonpath='{.data.client_id}' | base64 -d | wc -c
kubectl -n tailscale get secret operator-oauth \
  -o jsonpath='{.data.client_secret}' | base64 -d | wc -c
```

Both counts must be greater than 0. Report the counts, not the contents.

---

### Task 4: Install the Tailscale operator

**Files:**
- Create: `infrastructure/ingress/values.yaml`
- Create: `environments/homelab/apps/ingress-operator.yaml`
- Delete: `infrastructure/ingress/.gitkeep`

**Interfaces:**
- Consumes: Namespace `tailscale` (Task 1), Secret `operator-oauth` (Task 3).
- Produces: `IngressClass tailscale` (controller `tailscale.com/ts-ingress`),
  the `tailscale.com/v1alpha1` CRDs including `ProxyClass` and `ProxyGroup`
  used by Task 5, and Deployment `operator` in namespace `tailscale`.

- [ ] **Step 1: Record memory before installing**

```bash
free -h
kubectl get pods -A --no-headers | wc -l
```

Write both numbers into the task report. They are compared in Step 8.

- [ ] **Step 2: Create the operator values file**

Create `infrastructure/ingress/values.yaml`:

```yaml
# Tailscale Kubernetes operator -- provides IngressClass "tailscale".
# Chart: tailscale-operator 1.102.3 (matches host Tailscale 1.102.3;
# keep them aligned on upgrade).
#
# oauth.clientId and oauth.clientSecret are deliberately left empty. With
# them empty the chart templates NO Secret, and the operator instead reads
# the hand-created `operator-oauth` Secret in this namespace. Putting real
# values here would commit a tailnet credential to git. Never do that.
#
# The OAuth client behind that Secret holds exactly three scopes, all
# read+write, all tagged tag:k8s-operator:
#   General/Services, Devices/Core, Keys/Auth Keys.

oauth:
  clientId: ""
  clientSecret: ""

operatorConfig:
  hostname: tailscale-operator
  # This host runs a workstation, a homelab and a game server at once and
  # is already swapping. Unbounded is not acceptable here.
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      memory: 256Mi

# No defaultProxyClass is set here on purpose. It would name a ProxyClass
# created later, at sync-wave 3, leaving a dangling reference at wave 2.
# The ProxyGroup references its ProxyClass directly instead.

# The Kubernetes API server proxy is out of scope; leave it off.
apiServerProxyConfig:
  mode: "false"
```

- [ ] **Step 3: Create the Application**

Create `environments/homelab/apps/ingress-operator.yaml`:

```yaml
# Tailscale Kubernetes operator. Provides IngressClass "tailscale" and the
# tailscale.com CRDs.
#
# Wave 2: after namespaces (0), before ingress-config (3), which creates
# ProxyClass and ProxyGroup instances of the CRDs this Application installs.
#
# Requires the `operator-oauth` Secret in the tailscale namespace. That
# Secret exists only in the cluster and in no repository -- the operator
# pod stays in ContainerCreating without it.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ingress-operator
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  project: default
  sources:
    - repoURL: https://pkgs.tailscale.com/helmcharts
      chart: tailscale-operator
      targetRevision: 1.102.3
      helm:
        releaseName: tailscale-operator
        valueFiles:
          - $values/infrastructure/ingress/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: tailscale
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      # Consistent with the argocd Application. The largest CRD in this
      # chart is ~179KB, close enough to the 262144-byte
      # last-applied-configuration limit that a chart upgrade could cross it.
      - ServerSideApply=true
```

- [ ] **Step 4: Remove the placeholder and commit**

```bash
git rm -q infrastructure/ingress/.gitkeep
git add infrastructure/ingress/values.yaml environments/homelab/apps/ingress-operator.yaml
git commit -m "Install the Tailscale Kubernetes operator at sync-wave 2"
```

- [ ] **Step 5: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 6: Wait for the Application, then for the rollout**

Poll for existence first — `kubectl wait` errors immediately if the object
is absent:

```bash
for i in $(seq 1 60); do
  kubectl -n tailscale get deploy operator >/dev/null 2>&1 && break
  sleep 5
done
kubectl -n tailscale rollout status deploy/operator --timeout=300s
```

Expected: `deployment "operator" successfully rolled out`.

If the pod sits in `ContainerCreating`, inspect the events — the usual
cause is a missing or misnamed `operator-oauth` Secret:

```bash
kubectl -n tailscale describe deploy/operator | tail -20
kubectl -n tailscale get events --sort-by=.lastTimestamp | tail -20
```

- [ ] **Step 7: Verify the operator registered with the tailnet**

```bash
kubectl get ingressclass tailscale
kubectl get crd | grep tailscale.com
tailscale status | grep -i tailscale-operator
```

Expected: IngressClass `tailscale` with controller `tailscale.com/ts-ingress`;
8 CRDs; a `tailscale-operator` device present in `tailscale status`.

The device appearing is the proof that the OAuth credential works. If it
is absent, read the operator log:

```bash
kubectl -n tailscale logs deploy/operator --tail=50
```

An authentication failure here means the OAuth client is missing one of
its three scopes, or the tag was not attached to it.

- [ ] **Step 8: Record memory after, and the Application state**

```bash
free -h
kubectl get pods -A --no-headers | wc -l
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```

Report the measured delta against Step 1. The spec estimated 150–250Mi for
operator plus one proxy; **replace that estimate with the real number** in
the task report. If available memory has dropped below ~400Mi, say so
prominently — Task 5 adds a proxy pod on top.

---

### Task 5: Create the ProxyClass and ProxyGroup

> **SUPERSEDED during execution.** The shared `ProxyGroup` approach below
> was tried and abandoned — Tailscale Services proved unroutable on this
> tailnet. Do **not** create `proxygroup.yaml`. See the design spec §10
> for what actually shipped (a dedicated proxy per Ingress via
> `tailscale.com/proxy-class`). This task is kept as a historical record
> only.

**Files:**
- Create: `infrastructure/ingress/config/proxyclass.yaml`
- Create: `infrastructure/ingress/config/proxygroup.yaml`
- Create: `environments/homelab/apps/ingress-config.yaml`

**Interfaces:**
- Consumes: `tailscale.com/v1alpha1` CRDs from Task 4.
- Produces: `ProxyGroup` named `ingress` and `ProxyClass` named `homelab`.
  Task 6's Ingress attaches to the ProxyGroup via the annotation
  `tailscale.com/proxy-group: ingress`.

- [ ] **Step 1: Confirm the CRDs are established**

```bash
kubectl get crd proxyclasses.tailscale.com proxygroups.tailscale.com \
  -o custom-columns=NAME:.metadata.name,SCOPE:.spec.scope
```

Expected: both present, both `Cluster`. If absent, Task 4 is incomplete.

- [ ] **Step 2: Create the ProxyClass**

Cluster-scoped — no `namespace` field. Create
`infrastructure/ingress/config/proxyclass.yaml`:

```yaml
# Resource bounds for every Tailscale proxy pod.
#
# The chart's default is `resources: {}` -- unbounded. This host runs at
# ~1.1Gi available with swap fully exhausted, so an unbounded proxy can
# push it into the swap death spiral that already cost us a CoreDNS
# outage. Referenced explicitly by the ProxyGroup, not set as
# proxyConfig.defaultProxyClass, to avoid a cross-wave dangling reference.
#
# Cluster-scoped resource: no namespace.
apiVersion: tailscale.com/v1alpha1
kind: ProxyClass
metadata:
  name: homelab
spec:
  statefulSet:
    pod:
      tailscaleContainer:
        resources:
          requests:
            cpu: 25m
            memory: 64Mi
          limits:
            memory: 128Mi
```

- [ ] **Step 3: Create the ProxyGroup**

Create `infrastructure/ingress/config/proxygroup.yaml`:

```yaml
# One shared pool of proxy pods serving every Tailscale Ingress.
#
# replicas: 1 is REQUIRED and deliberate. The CRD default is 2, which
# doubles memory on a host that has none spare. This trades HA for memory
# -- an acceptable trade for a single-node homelab, where the node failing
# takes the workloads with it anyway.
#
# The -0 replica is the one that obtains Let's Encrypt certificates, so a
# single replica is sufficient for TLS.
#
# spec.type is immutable once created. Changing it needs a delete and
# recreate, which drops every tailnet hostname served by this group.
#
# Cluster-scoped resource: no namespace.
apiVersion: tailscale.com/v1alpha1
kind: ProxyGroup
metadata:
  name: ingress
spec:
  type: ingress
  replicas: 1
  proxyClass: homelab
```

- [ ] **Step 4: Create the Application**

Create `environments/homelab/apps/ingress-config.yaml`:

```yaml
# ProxyClass, ProxyGroup and the Ingress objects served by the Tailscale
# operator.
#
# Wave 3: strictly after ingress-operator (2), because these are instances
# of CRDs that Application installs. Splitting them across waves makes a
# rebuild deterministic instead of relying on retry to converge.
#
# path is infrastructure/ingress/config -- NOT infrastructure/ingress,
# which also holds values.yaml. Every YAML under `path` is applied as a
# Kubernetes manifest, and a Helm values file has no kind.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ingress-config
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: infrastructure/ingress/config
  destination:
    server: https://kubernetes.default.svc
    namespace: tailscale
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

- [ ] **Step 5: Commit**

```bash
git add infrastructure/ingress/config environments/homelab/apps/ingress-config.yaml
git commit -m "Add shared ProxyGroup and resource-bounded ProxyClass at wave 3"
```

- [ ] **Step 6: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 7: Verify the ProxyGroup comes up with exactly one replica**

```bash
for i in $(seq 1 60); do
  kubectl get proxygroup ingress >/dev/null 2>&1 && break
  sleep 5
done
kubectl get proxygroup ingress -o yaml | sed -n '/status:/,$p'
kubectl -n tailscale get statefulset
kubectl -n tailscale get pods
```

Do not filter by label — the operator's label scheme on ProxyGroup pods is
not verified here, and a selector that matches nothing looks identical to
a component that did not start. The `tailscale` namespace holds only the
operator and its proxies, so listing everything is unambiguous.

Expected: the ProxyGroup reports a ready condition; the StatefulSet shows
`1/1`; and besides the `operator` pod there is **exactly one** proxy pod
Running. If two proxy pods appear, `replicas: 1` did not take — stop and
investigate before continuing, because Task 6 adds load on top.

- [ ] **Step 8: Confirm the resource limits actually applied**

```bash
kubectl -n tailscale get statefulset \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[*].resources}{"\n"}{end}'
```

Expected: the memory limit `128Mi` from the ProxyClass is present. An
empty `{}` means the ProxyGroup is not using the ProxyClass — check
`spec.proxyClass` on the ProxyGroup.

- [ ] **Step 9: Record memory and Application health**

```bash
free -h
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```

All five Applications must be `Synced` / `Healthy`.

---

### Task 6: Expose Argo CD on the tailnet

> **SUPERSEDED during execution.** The `tailscale.com/proxy-group`
> annotation below reflects the abandoned shared-`ProxyGroup` approach.
> The Ingress that actually shipped uses `tailscale.com/proxy-class:
> homelab` instead, giving it its own dedicated proxy. See the design
> spec §10. This task is kept as a historical record only.

**Files:**
- Create: `infrastructure/ingress/config/argocd-ingress.yaml`
- Modify: `bootstrap/argocd/values.yaml`

**Interfaces:**
- Consumes: `ProxyGroup` named `ingress` (Task 5), `IngressClass tailscale`
  (Task 4), and the existing Service `argocd-server` in namespace `argocd`,
  which already listens on port 80 as plain HTTP because
  `configs.params."server.insecure"` is `true`.
- Produces: `https://argocd.taildf6cd4.ts.net`.

- [ ] **Step 1: Create the Ingress**

The hostname comes from `spec.tls[0].hosts[0]` as a **short** name.
`argocd` yields `argocd.taildf6cd4.ts.net`; writing the full FQDN there is
wrong. Create `infrastructure/ingress/config/argocd-ingress.yaml`:

```yaml
# Argo CD on the tailnet at https://argocd.taildf6cd4.ts.net
#
# This Ingress lives here, NOT in bootstrap/argocd/values.yaml, and that is
# deliberate. Argo CD self-manages at sync-wave -1, and Argo CD reports an
# Ingress as Progressing until status.loadBalancer.ingress is populated.
# Only this operator populates it, and it installs at wave 2 -- which root
# would never reach while still waiting on wave -1. The Ingress would block
# installation of the controller that unblocks the Ingress. Keeping it out
# of Argo CD's own release lets wave -1 go Healthy unconditionally.
#
# Services at wave 10+ are past this problem and may own their Ingress
# normally. Argo CD is special-cased only because it lives at wave -1.
#
# TLS terminates at the Tailscale proxy with a real Let's Encrypt
# certificate. The hop from proxy to argocd-server is plain HTTP inside the
# cluster, which is why configs.params."server.insecure" stays true.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd
  namespace: argocd
  annotations:
    # Share the pool from Task 5 instead of creating a dedicated proxy pod.
    tailscale.com/proxy-group: ingress
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: argocd-server
      port:
        number: 80
  tls:
    # Short name only. Becomes argocd.taildf6cd4.ts.net via MagicDNS.
    - hosts:
        - argocd
```

- [ ] **Step 2: Set Argo CD's external URL**

Argo CD needs its own external URL for correct redirects and links. In
`bootstrap/argocd/values.yaml`, replace the `configs:` block at the top —
lines 6 to 10 — with:

```yaml
configs:
  cm:
    # Argo CD's own external URL, used to build redirects and links.
    # Must match the tailnet hostname from
    # infrastructure/ingress/config/argocd-ingress.yaml.
    url: https://argocd.taildf6cd4.ts.net
  params:
    # TLS terminates at the Tailscale proxy, which forwards plain HTTP to
    # this Service inside the cluster. This is the intended steady state,
    # not a temporary port-forward workaround.
    server.insecure: true
```

This changes only a ConfigMap value and adds no Ingress to Argo CD's own
release, so wave -1 still reaches Healthy unconditionally.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/ingress/config/argocd-ingress.yaml bootstrap/argocd/values.yaml
git commit -m "Expose Argo CD at argocd.taildf6cd4.ts.net over the tailnet"
```

- [ ] **Step 4: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 5: Wait for the Ingress to be assigned a hostname**

Certificate issuance takes a minute or two on first request:

```bash
for i in $(seq 1 72); do
  H=$(kubectl -n argocd get ingress argocd \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
  [ -n "$H" ] && { echo "HOSTNAME: $H"; break; }
  sleep 5
done
kubectl -n argocd get ingress argocd
```

Expected: `argocd.taildf6cd4.ts.net`.

If it stays empty after six minutes, check the operator log for the
Ingress reconcile:

```bash
kubectl -n tailscale logs deploy/operator --tail=80 | grep -i ingress
```

- [ ] **Step 6: Verify MagicDNS resolves the name**

```bash
resolvectl query argocd.taildf6cd4.ts.net
```

Expected: resolves to a `100.x.y.z` tailnet address.

- [ ] **Step 7: Verify the certificate is genuine, not just that the page loads**

```bash
curl -sS -o /dev/null -w 'http_code=%{http_code}\n' https://argocd.taildf6cd4.ts.net
echo | openssl s_client -connect argocd.taildf6cd4.ts.net:443 \
  -servername argocd.taildf6cd4.ts.net 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

Expected: `http_code=200`, subject CN `argocd.taildf6cd4.ts.net`, issuer
naming Let's Encrypt, and valid dates. **Do not pass `-k` to curl** — the
whole point is that the certificate validates on its own.

- [ ] **Step 8: Confirm the port-forward fallback still works**

```bash
kubectl port-forward -n argocd svc/argocd-server 18080:80 &
PF=$!
sleep 4
curl -sS -o /dev/null -w 'port_forward_http=%{http_code}\n' http://localhost:18080
kill $PF
```

Expected: `port_forward_http=200`. Tailscale must not become the only way
in — if Tailscale itself is what has broken, this is the route back.

- [ ] **Step 9: Ask the user to verify from `msi`**

This is the acceptance criterion and it cannot be checked from this host —
a browser on a second machine is what proves the certificate is trusted
without any local exception. Ask the user to open
`https://argocd.taildf6cd4.ts.net` on their `msi` machine and confirm:
the Argo CD UI loads, there is **no certificate warning**, and login works.

Wait for their confirmation before marking this task complete.

---

### Task 7: Documentation

**Files:**
- Create: `infrastructure/ingress/README.md`
- Modify: `README.md`
- Modify: `docs/troubleshooting.md`

**Interfaces:**
- Consumes: the measured memory figures from Tasks 4 and 5, and the
  verified hostname from Task 6.

- [ ] **Step 1: Write the component README**

Create `infrastructure/ingress/README.md`:

```markdown
# Ingress

Services are reached over the **tailnet**, not the LAN. The Tailscale
Kubernetes operator provides `IngressClass: tailscale`; TLS terminates at
a Tailscale proxy pod with a real Let's Encrypt certificate, and the hop
from there to the Service is plain HTTP inside the cluster.

There is no LAN ingress and no `.home.arpa`. cert-manager is deliberately
not installed — see the design spec, §11.

| File | Purpose |
| --- | --- |
| `values.yaml` | Operator Helm values. **Not** applied as a manifest |
| `config/` | Manifests applied by the `ingress-config` Application |

`config/` must contain only valid Kubernetes manifests. The Application
applies every YAML in it, and a Helm values file has no `kind`.

## Exposing a new service

Add an Ingress to `config/` and push:

    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: <service>
      namespace: <namespace>
      annotations:
        tailscale.com/proxy-group: ingress
    spec:
      ingressClassName: tailscale
      defaultBackend:
        service:
          name: <service>
          port:
            number: 80
      tls:
        - hosts:
            - <service>

`spec.tls[0].hosts[0]` is a **short** name. `<service>` becomes
`<service>.taildf6cd4.ts.net`. Writing the full FQDN there is wrong.

The `tailscale.com/proxy-group` annotation shares the existing pool
instead of creating another proxy pod. Omitting it gives that service a
dedicated pod — avoid it on this host unless there is a reason.

A service at sync-wave 10 or later may instead own its Ingress alongside
its own manifests. Argo CD's Ingress lives here only because Argo CD runs
at wave -1; see the comment in `config/argocd-ingress.yaml`.

## The `operator-oauth` Secret

The operator authenticates to the Tailscale API with an OAuth client
stored as the Secret `operator-oauth` in the `tailscale` namespace, keys
`client_id` and `client_secret`, mounted at `/oauth`.

**This Secret exists only in the cluster and in no repository.** If it is
lost or the client is revoked, all tailnet ingress stops. It is
backup-worthy state until Vault takes over in phase 16.

To recreate it, make an OAuth client at
<https://login.tailscale.com/admin/settings/oauth> with exactly these
scopes, each read and write, all tagged `tag:k8s-operator`:

- General → Services
- Devices → Core
- Keys → Auth Keys

Do not grant `all`; it confers every scope plus all device tags.

    kubectl create secret generic operator-oauth -n tailscale \
      --from-literal=client_id=<id> --from-literal=client_secret=<secret>

The tag must already exist in the tailnet policy before the console will
attach it — see `infrastructure/networking/README.md`.

## Memory

`ProxyGroup` runs `replicas: 1`, against a CRD default of 2, and
`ProxyClass` bounds each proxy to 128Mi. Both are deliberate: this host
runs with almost no free memory and no usable swap. Read
`docs/troubleshooting.md` before raising either.
```

- [ ] **Step 2: Replace the Access section in the root README**

In `README.md`, replace the whole `## Access` section — from the heading
through the line ending `until then.` — with:

```markdown
## Access

Argo CD is at **<https://argocd.taildf6cd4.ts.net>** from any device on
the tailnet. The certificate is a real Let's Encrypt certificate issued
by Tailscale, so no CA needs installing anywhere.

Log in as `admin`. The initial-password Secret was deleted after the
first password change; there is no recovery path from the cluster, so the
password must be kept in a password manager.

If Tailscale itself is unavailable, the port-forward still works:

    kubectl port-forward -n argocd svc/argocd-server 8080:80

then <http://localhost:8080> — plain HTTP, no TLS.

Ingress is Tailscale-only; there is no LAN hostname and no `.home.arpa`.
See `infrastructure/ingress/README.md` to expose another service.
```

- [ ] **Step 3: Add the new manual step to First install / rebuild**

The rebuild path now needs a second hand-made Secret. In the
`## First install / rebuild` section, insert this as a new step between
the existing step 3 (`kubectl apply -f environments/homelab/root.yaml`)
and the paragraph beginning `The order matters:`:

```markdown
4. Create the `operator-oauth` Secret in the `tailscale` namespace once
   root has created that namespace at sync-wave 0. Until it exists the
   Tailscale operator stays in `ContainerCreating` and no tailnet
   hostname resolves. See `infrastructure/ingress/README.md`. Argo CD
   itself is reachable by port-forward throughout, so this does not
   block recovery.
```

- [ ] **Step 4: Add the layout rows**

In the `## Layout` table in `README.md`, the `infrastructure/` row reads
`Storage, ingress, cert-manager, networking`. Replace that single row with:

```markdown
| `infrastructure/ingress/` | Tailscale operator values and the Ingress manifests it serves |
| `infrastructure/networking/` | Tailnet ACL policy — **not** reconciled by Argo CD |
| `infrastructure/storage/` | PVC storage notes |
| `infrastructure/cert-manager/` | Empty; deferred, see the 2026-09-02 spec |
```

- [ ] **Step 5: Extend troubleshooting entry 6 to cover the second secret**

In `docs/troubleshooting.md`, entry 6 is headed
`## 6. Every Application stops reconciling at once` and describes
`repo-homelab` as the single point of failure. Append this to the end of
that entry, before the `---` that closes it:

```markdown
### The second cluster-only secret

`repo-homelab` is no longer the only one. `operator-oauth` in the
`tailscale` namespace holds the OAuth client the Tailscale operator uses,
and it exists in no repository either.

| Secret | Namespace | Loss impact |
| --- | --- | --- |
| `repo-homelab` | `argocd` | All Argo CD reconciliation stops |
| `operator-oauth` | `tailscale` | All tailnet ingress stops; every `.ts.net` URL dies |

Losing `operator-oauth` does **not** stop reconciliation — Argo CD keeps
working, and `kubectl port-forward` still reaches it. Symptoms are
hostnames that stop resolving and proxy pods that fail to authenticate:

    kubectl -n tailscale logs deploy/operator --tail=50

Both are backup-worthy until Vault takes over in phase 16.
```

- [ ] **Step 6: Add a troubleshooting entry for the operator**

Append a new entry to the end of `docs/troubleshooting.md`:

```markdown
---

## 8. A tailnet hostname does not resolve

### Symptom

`https://<name>.taildf6cd4.ts.net` fails to resolve, or the Ingress shows
no hostname:

    kubectl -n <ns> get ingress <name>
    # ADDRESS column empty

### Work through these in order

**1. Is the operator running?**

    kubectl -n tailscale get pods
    kubectl -n tailscale logs deploy/operator --tail=50

`ContainerCreating` almost always means the `operator-oauth` Secret is
missing or has the wrong key names. They must be exactly `client_id` and
`client_secret`.

**2. Is the hostname a short name?**

`spec.tls[0].hosts[0]` must be `argocd`, not `argocd.taildf6cd4.ts.net`.
The operator appends the MagicDNS suffix itself.

**3. Does the ProxyGroup have a running pod?**

    kubectl get proxygroup ingress -o yaml | sed -n '/status:/,$p'
    kubectl -n tailscale get pods

The `-0` replica is the one that obtains Let's Encrypt certificates. If it
is not running, no certificate is issued for any hostname in the group.

**4. Is it an OAuth scope problem?**

The operator log reporting an API permission error means the client is
missing one of its three scopes. All three are required, each read and
write: General → Services, Devices → Core, Keys → Auth Keys. `services`
is the one most often missed, and it is what publishes per-Ingress
hostnames from a shared ProxyGroup.

**5. Are HTTPS certificates still enabled on the tailnet?**

Admin console → DNS → HTTPS Certificates. With it off, no certificate is
ever issued. Confirm from the host:

    tailscale status --json | jq .CertDomains

`null` means it is disabled.

### Remember the way back in

None of this blocks Argo CD itself:

    kubectl port-forward -n argocd svc/argocd-server 8080:80
```

- [ ] **Step 7: Record the measured memory cost**

In `infrastructure/ingress/README.md`, under `## Memory`, add one sentence
giving the **measured** before/after figures from Tasks 4 and 5 — not the
spec's 150–250Mi estimate. Write what was observed.

- [ ] **Step 8: Verify every documented command is real**

Run each command quoted in the new documentation and confirm it behaves as
described. Documentation asserting something untested is worse than none —
entry 6 already records a verification method that could never have worked.

- [ ] **Step 9: Commit**

```bash
git add infrastructure/ingress/README.md README.md docs/troubleshooting.md
git commit -m "Document tailnet ingress, the operator-oauth secret, and its failure modes"
```

- [ ] **Step 10: Hand the push to the user**

```bash
git push origin main
```

- [ ] **Step 11: Final state check**

```bash
git status --short
git log --oneline -8
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
free -h
```

Expected: clean tree, five Applications all `Synced` / `Healthy`.
