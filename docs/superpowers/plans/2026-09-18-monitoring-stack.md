# Monitoring Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install kube-prometheus-stack on this k3s cluster so Prometheus scrapes the node, the workloads and five existing components, and a stateless Grafana published at `https://grafana.taildf6cd4.ts.net` draws it.

**Architecture:** One Helm release, two Argo CD Applications split on the CRD boundary — `monitoring` (wave 23) carries the chart and Grafana's Vault credential; `monitoring-config` (wave 24) carries every custom resource that needs the CRDs the chart installs. Nothing sits behind wave 24, so the monitoring stack gates nothing. Grafana holds no PVC: its datasource and dashboards come from the chart and from git, and a restart is a deliberate factory reset.

**Tech Stack:** kube-prometheus-stack 91.4.1 (prometheus-operator v0.94.0, Grafana subchart 13.2.5, kube-state-metrics 8.5.0, node-exporter 4.57.0), Argo CD 10.5.0, Vault chart 0.34.1 + Vault Secrets Operator, postgres-exporter v0.20.1, redis_exporter v1.91.1-alpine, k3s v1.36.4+k3s1.

**Spec:** `docs/superpowers/specs/2026-09-18-monitoring-stack-design.md`

## Global Constraints

- **Never `git push`.** Commit, then hand the push command to the operator. A push to `main` is a deploy: Argo CD reconciles `main` with `selfHeal: true`.
- **Work on `main`.** Nothing reconciles a feature branch into this cluster.
- **Validate with the real CI command**, never a bare local `kubeconform`. Local kubeconform without both `-schema-location` flags silently skips every CRD and has already hidden a Critical finding here. The exact commands are in Task 1.
- **Pin every version exactly.** Chart 91.4.1, images by tag. No `latest`, no version ranges.
- **Every manifest in `monitoring-config` carries an explicit `metadata.namespace`** — that Application writes into `argocd`, `vault`, `kube-system` and `databases`, not only its destination namespace.
- **`kubectl` needs the k3s group on this host:** prefix with `sg k3s-admin -c '…'` where the existing READMEs do.
- **Never add a `-ignore-filename-pattern` to `validate.yaml`.** All three new CRD kinds are in the datreeio catalog (spec §9, verified 2026-09-18).
- **Chart values files are not manifests.** They live at `observability/monitoring/values.yaml` and CI skips them by the existing `values\.yaml$` rule. Manifests go in `config/` and `targets/` subdirectories — the same split `platform/vault/` uses.
- **yamllint runs with `--strict` against `.yamllint.yaml`.** Warnings fail. Run it locally before every commit.
- Commit messages end with the two trailers used throughout this repository:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M`

---

## File Structure

**Created:**

| Path | Responsibility |
| --- | --- |
| `observability/monitoring/values.yaml` | Every chart value. The single place the stack is tuned |
| `observability/monitoring/config/vault-secrets.yaml` | ServiceAccount, VaultConnection, VaultAuth, VaultStaticSecret producing Secret `grafana-admin`. Applied by `monitoring`, so it needs no CRD the chart installs |
| `observability/monitoring/targets/argocd.yaml` | Three ServiceMonitors for Argo CD |
| `observability/monitoring/targets/vault.yaml` | One ServiceMonitor for Vault |
| `observability/monitoring/targets/redis-exporter.yaml` | Deployment, Service, ServiceMonitor |
| `observability/monitoring/targets/postgres-exporter.yaml` | VSO path, role-creation Job, Deployment, Service, ServiceMonitor |
| `observability/monitoring/targets/traefik.yaml` | HelmChartConfig and ServiceMonitor. Isolated so it reverts alone |
| `observability/monitoring/README.md` | Operational doc: measured memory, port-forward commands, the statelessness warning |
| `environments/homelab/apps/monitoring.yaml` | Argo CD Application, wave 23 |
| `environments/homelab/apps/monitoring-config.yaml` | Argo CD Application, wave 24 |
| `infrastructure/ingress/config/grafana-ingress.yaml` | Tailnet Ingress, wave 21 |

**Modified:** `bootstrap/namespaces/namespaces.yaml`, `bootstrap/argocd/values.yaml`, `platform/vault/values.yaml`, `platform/vault/configure-vault.sh`, `.github/workflows/validate.yaml`, `docs/workstation-plan.md`.

**Deleted:** `observability/prometheus/.gitkeep`, `observability/grafana/.gitkeep`.

---

## Task 1: Put `observability/` under CI

Nothing validates that tree today. This task runs first so every later task is checked.

**Files:**
- Create: `observability/monitoring/README.md` (stub, replaced in Task 10)
- Modify: `.github/workflows/validate.yaml`
- Delete: `observability/prometheus/.gitkeep`, `observability/grafana/.gitkeep`

**Interfaces:**
- Produces: the two validation commands every later task runs. Copy them from Step 3 verbatim.

- [ ] **Step 1: Prove the gap exists**

```bash
grep -n 'yamllint --strict' .github/workflows/validate.yaml
grep -n 'bootstrap environments infrastructure platform' .github/workflows/validate.yaml
```

Expected: three matches, none containing `observability`.

- [ ] **Step 2: Create the directory with a stub README so the tree is not empty**

```bash
mkdir -p observability/monitoring/config observability/monitoring/targets
git rm -q observability/prometheus/.gitkeep observability/grafana/.gitkeep
cat > observability/monitoring/README.md <<'EOF'
# Monitoring — Prometheus and Grafana

kube-prometheus-stack, covering workstation-plan phases 22 and 23. Design:
`docs/superpowers/specs/2026-09-18-monitoring-stack-design.md`.

Filled in by Task 10 of the implementation plan, with measured figures.
EOF
```

`observability/logging/.gitkeep` stays — that is phase 24.

- [ ] **Step 3: Add `observability` to both jobs**

In `.github/workflows/validate.yaml`, the yamllint step becomes:

```yaml
        run: yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure observability platform .github
```

and **both** kubeconform invocations (the `Validate manifests` step and the `Fail if any resource was skipped` step) get `observability` in the same position:

```yaml
            bootstrap environments infrastructure observability platform
```

Add this comment above the `-schema-location` flags in the `Validate manifests` step, replacing the sentence that says all five CRDs are in the catalog:

```yaml
        # The second -schema-location supplies CRD schemas. The CRDs this
        # repository uses are all in the datreeio catalog: Application,
        # VaultAuth, VaultConnection, VaultStaticSecret, ProxyClass, and as
        # of phase 22 ServiceMonitor, PrometheusRule (monitoring.coreos.com)
        # and HelmChartConfig (helm.cattle.io). All three of the phase-22
        # kinds were checked against the catalog before being used; none
        # needs an exemption, and none should be given one.
```

- [ ] **Step 4: Run the real CI commands locally**

```bash
pip install --quiet yamllint==1.38.0
yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure observability platform .github
```

Expected: no output, exit 0.

```bash
cd /tmp && curl -sL https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o kc.tgz \
  && echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  kc.tgz" | sha256sum -c - \
  && tar xzf kc.tgz kubeconform && cd -
/tmp/kubeconform -summary -kubernetes-version 1.36.0 \
  -ignore-filename-pattern 'values\.yaml$' \
  -ignore-filename-pattern 'platform/nexus/registries\.yaml$' \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  bootstrap environments infrastructure observability platform | tee /tmp/summary.txt
grep -q 'Skipped: 0' /tmp/summary.txt && echo "GATE PASSES"
```

Expected: `GATE PASSES`. **This exact command block is "the real CI command" the Global Constraints require. Every later task re-runs it.**

- [ ] **Step 5: Commit**

```bash
git add -A .github/workflows/validate.yaml observability
git commit -m "$(cat <<'EOF'
Put observability/ under CI before writing anything into it

validate.yaml lints four directories and observability was not one of
them, so phase 22's manifests would have landed unvalidated. Both jobs
now cover it -- the skipped-resource gate included, which is the half
that matters: kubeconform exits 0 when it skips, so a missing schema
turns the job green while checking nothing.

The two empty .gitkeep directories are replaced by observability/
monitoring/, because one Helm release produces both Prometheus and
Grafana and cannot live in two directories at once.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

---

## Task 2: Namespace, and both Vault credentials in one ceremony

Two credentials are needed across this phase: Grafana's admin password (Task 3) and the PostgreSQL exporter's login (Task 8). Both go into `configure-vault.sh` now so the operator runs that script **once**.

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`, `platform/vault/configure-vault.sh`

**Interfaces:**
- Produces: Vault KV `homelab/grafana` (keys `username`, `password`), policy `vso-grafana-read`, role `vso-grafana` bound to ServiceAccount `grafana` in namespace `monitoring`; Vault KV `homelab/postgres-exporter` (keys `username`, `password`), policy `vso-postgres-exporter-read`, role `vso-postgres-exporter` bound to ServiceAccount `postgres-exporter` in namespace `databases`.

- [ ] **Step 1: Add the namespace**

Append to `bootstrap/namespaces/namespaces.yaml`:

```yaml
---
# Prometheus, Grafana and their exporters -- workstation-plan phases 22 and
# 23, built as one release because Grafana is a subchart of
# kube-prometheus-stack. See docs/superpowers/specs/
# 2026-09-18-monitoring-stack-design.md.
#
# The exporters for PostgreSQL and Redis do NOT live here: they sit in
# `databases`, beside what they scrape, because each needs that namespace's
# credential Secret and a VaultAuth ServiceAccount must reside in the
# consuming Secret's namespace.
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
```

- [ ] **Step 2: Add both credentials to `configure-vault.sh`**

Follow the file's existing shape exactly — read the `nexus` block (around line 177) and mirror it. Insert after the last existing credential block:

```sh
echo "==> KV homelab/grafana"
if vault kv get homelab/grafana >/dev/null 2>&1; then
  echo "    already present -- not regenerating"
else
  PWFILE=$(mktemp)
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 > "$PWFILE"
  vault kv put homelab/grafana username=admin password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-grafana-read"
vault policy write vso-grafana-read - <<'POLICY'
path "homelab/data/grafana" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-grafana"
vault write auth/kubernetes/role/vso-grafana \
  bound_service_account_names=grafana \
  bound_service_account_namespaces=monitoring \
  token_policies=vso-grafana-read \
  audience=vault \
  ttl=24h >/dev/null

echo "==> KV homelab/postgres-exporter"
if vault kv get homelab/postgres-exporter >/dev/null 2>&1; then
  echo "    already present -- not regenerating"
else
  PWFILE=$(mktemp)
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 > "$PWFILE"
  vault kv put homelab/postgres-exporter username=exporter password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-postgres-exporter-read"
vault policy write vso-postgres-exporter-read - <<'POLICY'
path "homelab/data/postgres-exporter" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-postgres-exporter"
vault write auth/kubernetes/role/vso-postgres-exporter \
  bound_service_account_names=postgres-exporter \
  bound_service_account_namespaces=databases \
  token_policies=vso-postgres-exporter-read \
  audience=vault \
  ttl=24h >/dev/null
```

**Before writing this, read the existing `nexus` block and copy its exact
idiom** — the password generation, the `@"$PWFILE"` form and the `audience`
and `ttl` values must match what the file already does, not what this plan
guesses. The `@` form matters: a key passed as a command argument is visible
in `ps` to every user on this host.

- [ ] **Step 3: Verify the script is still valid shell and idempotent by inspection**

```bash
sh -n platform/vault/configure-vault.sh && echo "SYNTAX OK"
grep -c 'already present -- not regenerating' platform/vault/configure-vault.sh
```

Expected: `SYNTAX OK`, and a count two higher than before this task.

- [ ] **Step 4: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/namespaces/namespaces.yaml platform/vault/configure-vault.sh
git commit -m "$(cat <<'EOF'
Add the monitoring namespace and both of phase 22's Vault credentials

Grafana's admin password and the PostgreSQL exporter's login go in
together so the operator runs configure-vault.sh once rather than twice.
Argo CD does not reconcile that script -- it is versioned intent applied
by hand -- so every extra run is a hand-off.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 6: OPERATOR HAND-OFF — stop here and hand these to the user**

The namespace reaches the cluster only after a push, and the Vault script is
run by a human inside the pod. Hand over, verbatim, and wait:

```
git push origin main
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

- [ ] **Step 7: Verify the ceremony landed**

```bash
sg k3s-admin -c 'kubectl get ns monitoring'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault kv get -field=username homelab/grafana'
```

Expected: namespace `Active`; the second prints `admin`. If it prompts for a
login, the token was removed as intended — re-run step 6's first command.

---

## Task 3: The chart, Grafana's credential, and the `monitoring` Application

The largest task. It ends with Prometheus and Grafana running.

**Files:**
- Create: `observability/monitoring/values.yaml`, `observability/monitoring/config/vault-secrets.yaml`, `environments/homelab/apps/monitoring.yaml`

**Interfaces:**
- Consumes: Vault `homelab/grafana`, policy `vso-grafana-read`, role `vso-grafana` (Task 2).
- Produces: Secret `grafana-admin` in `monitoring` with keys **`admin-user`** and **`admin-password`** — these exact key names, because `grafana.admin.userKey`/`passwordKey` default to them. Produces Service `monitoring-grafana` on port 80, and Service `monitoring-kube-prometheus-prometheus` on port 9090, both consumed by Tasks 4 and 5.

- [ ] **Step 1: Write `observability/monitoring/values.yaml`**

```yaml
# kube-prometheus-stack -- workstation-plan phases 22 and 23 in one release.
# Chart: prometheus-community/kube-prometheus-stack 91.4.1
#        (prometheus-operator v0.94.0, Grafana subchart 13.2.5)
# Repo:  https://prometheus-community.github.io/helm-charts
#
# Design: docs/superpowers/specs/2026-09-18-monitoring-stack-design.md
#
# Two things here are not defaults and will look wrong to anyone who has run
# this chart elsewhere: Alertmanager is off, and four control-plane scrapers
# are off. Both are argued in the spec, sections 3.1 and 7. Do not "restore"
# them without reading it.

# Decision P3. No notification destination exists, and a router with nowhere
# to route is infrastructure with no consumer. The stack's ~30 default alert
# RULES still load and evaluate in Prometheus -- only delivery is absent.
alertmanager:
  enabled: false

# Decision P6. On k3s these four run as goroutines inside one `k3s server`
# process with their metrics bound to 127.0.0.1. Left enabled they are four
# permanently red targets, and a monitoring system whose own dashboard is
# broken teaches you to ignore it. Turning them on needs
# --kube-scheduler-arg=bind-address=0.0.0.0 and three siblings in the k3s
# systemd unit: a host-root edit, so an operator hand-off, deliberately not
# taken on a single-node cluster with one etcd member.
kubeControllerManager:
  enabled: false
kubeScheduler:
  enabled: false
kubeProxy:
  enabled: false
kubeEtcd:
  enabled: false

defaultRules:
  # Disabling the scrapers above without disabling their rules leaves alerts
  # that reference series nothing produces. These five keys are the exact
  # ones chart 91.4.1 exposes -- scheduler rules are split across two keys
  # and there is no kubernetesSystemScheduler key.
  rules:
    etcd: false
    kubeControllerManager: false
    kubeProxy: false
    kubeSchedulerAlerting: false
    kubeSchedulerRecording: false

prometheusOperator:
  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      memory: 128Mi

prometheus:
  prometheusSpec:
    # 30s rather than the chart's default (unset, which resolves to 30s in
    # the operator) -- stated explicitly so a future chart default cannot
    # silently halve the sample rate and double the memory.
    scrapeInterval: 30s

    # Spec section 5.1. The SIZE cap is the one that protects the node;
    # retention by time alone does not bound disk, and disk pressure here
    # evicts pods. 12GiB against a 20Gi volume leaves room for the WAL and
    # for compaction, which retentionSize does not govern.
    retention: 15d
    retentionSize: 12GiB

    resources:
      requests:
        cpu: 200m
        memory: 512Mi
      limits:
        # Exceeding this is an OOMKill that loses the write-ahead log, which
        # is why retentionSize exists above.
        memory: 2Gi

    # Discover ServiceMonitors in every namespace, not only this one. The
    # targets in observability/monitoring/targets/ live in argocd, vault,
    # kube-system and databases. Without these four lines the operator
    # applies a release-label selector and finds none of them -- Prometheus
    # comes up healthy and scrapes nothing, which is the failure mode this
    # chart is most often misconfigured into.
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorNamespaceSelector: {}
    ruleSelectorNilUsesHelmValues: false
    podMonitorSelectorNilUsesHelmValues: false

    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: local-path
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              # local-path does not enforce capacity -- a PV is a directory
              # on /srv, which has 850G free. Nominal, not a quota. The real
              # bound is retentionSize above.
              storage: 20Gi

grafana:
  # Decision P4. NO PVC. /var/lib/grafana is an emptyDir and the SQLite
  # database is discarded on every restart.
  #
  # THIS MEANS A DASHBOARD EDITED IN THE UI IS LOST ON RESTART. That is the
  # design, not a defect: plan section 26 asks that the configuration be
  # declarative and versioned in git, and a PVC would quietly make the
  # cluster authoritative over git instead. Anything that must survive is
  # provisioned from a file. See this directory's README.
  persistence:
    enabled: false

  # The admin credential comes from Vault via VSO -- see config/
  # vault-secrets.yaml. The two key names below are the chart's defaults and
  # are restated because the VaultStaticSecret must produce exactly these.
  admin:
    existingSecret: grafana-admin
    userKey: admin-user
    passwordKey: admin-password

  # Roughly 25 dashboards shipped as ConfigMaps: nodes, pods, namespaces,
  # workloads, kubelet, PV usage. They cover plan section 25's "CPU / RAM /
  # nodes / pods / Kubernetes" list outright, which is why this phase adds
  # no dashboards of its own (decision P7).
  defaultDashboardsEnabled: true

  sidecar:
    dashboards:
      # Label discovery stays on, so a future dashboard is one ConfigMap
      # labelled grafana_dashboard: "1" and nothing else.
      enabled: true
      label: grafana_dashboard
      searchNamespace: ALL
    datasources:
      # The stack provisions the Prometheus datasource itself. This phase
      # writes no datasource manifest.
      enabled: true
      defaultDatasourceEnabled: true

  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 256Mi

kube-state-metrics:
  resources:
    requests:
      cpu: 20m
      memory: 64Mi
    limits:
      memory: 128Mi

prometheus-node-exporter:
  resources:
    requests:
      cpu: 20m
      memory: 32Mi
    limits:
      memory: 64Mi
```

- [ ] **Step 2: Write `observability/monitoring/config/vault-secrets.yaml`**

Read `platform/nexus/config/vault-secrets.yaml` first and mirror it — the
`excludeRaw` placement trap documented there applies here too.

```yaml
# The Vault -> Kubernetes Secret path for Grafana's admin credential.
#
# This file is in config/ rather than targets/ because it needs NO CRD that
# the chart installs. It is a source of the `monitoring` Application at wave
# 23, so the Secret is created in the same sync as the Grafana Deployment
# that mounts it. VSO materialises it asynchronously, so Grafana may restart
# once or twice before the Secret exists; it recovers without intervention.
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: grafana
  namespace: monitoring
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: grafana
  namespace: monitoring
spec:
  address: http://vault.vault.svc.cluster.local:8200
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultAuth
metadata:
  name: grafana
  namespace: monitoring
spec:
  vaultConnectionRef: grafana
  method: kubernetes
  mount: kubernetes
  kubernetes:
    # Must match `vault write auth/kubernetes/role/vso-grafana` in
    # platform/vault/configure-vault.sh exactly. A mismatch in the role, the
    # ServiceAccount or the audience is a permission denial that names
    # neither side, while Argo CD reports everything Synced.
    role: vso-grafana
    serviceAccount: grafana
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: grafana-admin
  namespace: monitoring
spec:
  vaultAuthRef: grafana
  mount: homelab
  type: kv-v2
  path: grafana
  refreshAfter: 60s
  destination:
    name: grafana-admin
    create: true
    transformation:
      # Vault stores username/password; the Grafana chart reads admin-user
      # and admin-password. Templates RENAME, they do not replace: the
      # source keys remain in the Secret alongside these, which is why
      # excludeRaw below is about VSO's _raw JSON blob only.
      templates:
        admin-user:
          text: '{{ get .Secrets "username" }}'
        admin-password:
          text: '{{ get .Secrets "password" }}'
      # Under destination.transformation, NOT under spec. spec is a
      # structural schema, so an unknown field there is pruned silently --
      # the Secret would still be created, Argo CD would still report
      # Healthy, and it would carry a _raw key anyway.
      excludeRaw: true
```

- [ ] **Step 3: Write `environments/homelab/apps/monitoring.yaml`**

```yaml
# kube-prometheus-stack: Prometheus, Grafana, kube-state-metrics and
# node-exporter. Workstation-plan phases 22 and 23, merged -- Grafana is a
# subchart, so splitting them means installing the chart twice.
#
# Wave 23 -- the SAME wave as postgres, redis, registry and nexus, not 24.
# This depends on none of them, and both redis.yaml and nexus.yaml warn
# against stacking a wave "out of habit": sharing one means these reconcile
# in parallel and none gates another.
#
# It is at or above 22, the floor vso-config.yaml establishes for anything
# that depends on a human having run platform/vault/configure-vault.sh.
# Grafana's admin password does.
#
# The ServiceMonitors are NOT here. They are custom resources needing the
# CRDs this Application installs, so they live in monitoring-config at wave
# 24 -- the same split, for the same reason, as vso-operator/vso-config.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: monitoring
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  sources:
    - repoURL: https://prometheus-community.github.io/helm-charts
      chart: kube-prometheus-stack
      targetRevision: 91.4.1
      helm:
        # Pinned deliberately: it prefixes every object name the chart
        # creates, so changing it renames live objects -- including the
        # Services that grafana-ingress.yaml and the ServiceMonitors in
        # monitoring-config reference by name.
        releaseName: monitoring
        valueFiles:
          - $values/observability/monitoring/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
    # The Vault path for Grafana's password is not part of the chart, so it
    # needs a source of its own. path is .../config -- NOT the parent, which
    # also holds values.yaml, and a Helm values file has no kind. Same split,
    # and the same reason, as platform/vault/config.
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      path: observability/monitoring/config
  destination:
    server: https://kubernetes.default.svc
    namespace: monitoring
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 4: Render the chart locally before letting Argo CD near it**

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community
helm template monitoring prometheus-community/kube-prometheus-stack \
  --version 91.4.1 --namespace monitoring \
  -f observability/monitoring/values.yaml > /tmp/rendered.yaml
echo "exit=$?"
grep -c 'kind: Alertmanager' /tmp/rendered.yaml
python3 -c "
import yaml
for d in yaml.safe_load_all(open('/tmp/rendered.yaml')):
    if d and d.get('kind')=='Service':
        print(d['metadata']['name'], [(x.get('name'), x.get('port')) for x in d['spec'].get('ports',[])])
"
```

Expected: `exit=0`; the Alertmanager count is `0`; and among the Services,
these two exactly — rendered and verified from chart 91.4.1 on 2026-09-18:

```text
monitoring-grafana                      [('http-web', 80)]
monitoring-kube-prometheus-prometheus   [('http-web', 9090), ('reloader-web', 8080)]
```

**If either name differs, stop and use what actually rendered** — Tasks 4, 5,
7, 8 and 10 all reference these by name, and the release name prefixes every
object the chart creates.

- [ ] **Step 5: Confirm the ServiceMonitor selector is genuinely open**

```bash
grep -A6 'serviceMonitorNamespaceSelector' /tmp/rendered.yaml | head -20
```

Expected: an empty selector (`{}`) and no `release` matchLabels constraint.
This is the single most common way this chart is misconfigured into scraping
nothing, so verify it rather than trusting the values file.

- [ ] **Step 6: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 7: Commit**

```bash
git add observability/monitoring environments/homelab/apps/monitoring.yaml
git commit -m "$(cat <<'EOF'
Install kube-prometheus-stack at wave 23

Prometheus with a 20Gi volume and retention capped by size as well as by
time -- the size cap is the one that protects the node, because
retention by days alone does not bound disk and disk pressure here
evicts pods.

Grafana holds no PVC. Its datasource and dashboards come from the chart
and from git, so a restart is a factory reset by design: plan section 26
asks for declarative configuration, and a volume would quietly make the
cluster authoritative over git instead.

Alertmanager is off (no destination to route to) and the four k3s
control-plane scrapers are off with their rules (they bind to 127.0.0.1
inside one process and would be permanently red targets).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 8: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 9: Verify the stack is up**

```bash
sg k3s-admin -c 'kubectl -n argocd get application monitoring'
sg k3s-admin -c 'kubectl -n monitoring get pods'
sg k3s-admin -c 'kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data}"' | tr ',' '\n' | sed 's/:.*//'
sg k3s-admin -c 'kubectl -n monitoring get pvc'
```

Expected: Application `Synced`/`Healthy`; pods for prometheus, grafana,
operator, kube-state-metrics and node-exporter all Running; the Secret's keys
include `admin-user` and `admin-password` and **not** `_raw`; one Bound PVC of
20Gi. If Grafana is in `CrashLoopBackOff`, check the Secret first — it starts
before VSO materialises it and should recover on its own.

---

## Task 4: Publish Grafana on the tailnet

**Files:**
- Create: `infrastructure/ingress/config/grafana-ingress.yaml`

**Interfaces:**
- Consumes: Service `monitoring-grafana` port 80 (Task 3, Step 4).

- [ ] **Step 1: Write the Ingress**

Read `infrastructure/ingress/config/nexus-ingress.yaml` first and mirror its
structure and its ProxyClass reference.

```yaml
# Grafana on the tailnet at https://grafana.taildf6cd4.ts.net
#
# In infrastructure/ingress/config, at wave 21, AHEAD of the Grafana it
# points at (wave 23). Every Ingress in this repository lives here for the
# reason argocd-ingress.yaml sets out: nothing should ever wait on ingress
# health to sync. An Ingress whose backend does not exist yet is valid and
# harmless -- it simply has nothing to route to until wave 23 lands.
#
# A dedicated proxy rather than the shared ProxyGroup, for the reason
# argocd-ingress.yaml records at length: Tailscale Services proved
# unroutable on this tailnet. It still references the ProxyClass so the pod
# stays memory-bounded.
#
# There is deliberately NO Ingress for Prometheus. Prometheus has no
# authentication of any kind, and infrastructure/networking/policy.hujson is
# currently a single {"src": ["*"], "dst": ["*"], "ip": ["*"]} grant -- so
# publishing it would expose its admin API to every device on the tailnet.
# Reach it with a port-forward; the command is in
# observability/monitoring/README.md.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: grafana
  namespace: monitoring
  annotations:
    tailscale.com/proxy-class: homelab
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: monitoring-grafana
      port:
        number: 80
  tls:
    # Short name only. Becomes grafana.taildf6cd4.ts.net via MagicDNS.
    - hosts:
        - grafana
```

- [ ] **Step 2: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/ingress/config/grafana-ingress.yaml
git commit -m "$(cat <<'EOF'
Publish Grafana at grafana.taildf6cd4.ts.net

At wave 21 with every other Ingress, ahead of the Grafana it points at.
Prometheus deliberately gets no Ingress: it has no authentication and
the tailnet policy is currently a single open grant.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 4: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 5: Verify, including a real login**

```bash
sg k3s-admin -c 'kubectl -n monitoring get ingress grafana'
sg k3s-admin -c 'kubectl -n tailscale get pods | grep grafana'
curl -sS -o /dev/null -w '%{http_code}\n' https://grafana.taildf6cd4.ts.net/login
```

Expected: the Ingress shows an `ADDRESS`; a `ts-grafana-*` pod is Running;
the curl returns `200` with no TLS warning — a real Let's Encrypt certificate.

Then log in, which proves the whole Vault → VSO → chart → browser chain:

```bash
sg k3s-admin -c 'kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data.admin-password}"' | base64 -d; echo
```

Expected: a 32-character password. Prove the claim rather than asserting it:

```bash
PW=$(sg k3s-admin -c 'kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data.admin-password}"' | base64 -d)
git grep -q -- "$PW" && echo "LEAKED -- the password is in the repository" || echo "CLEAN -- not in any tracked file"
unset PW
```

Expected: `CLEAN`. Then log in with `admin` and that password, and confirm the
Prometheus datasource is listed and reports healthy under Connections → Data
sources. That login exercises the whole Vault → VSO → chart → tailnet chain in
one action.

---

## Task 5: Argo CD metrics, and the `monitoring-config` Application

This edits Argo CD's own self-managed release. Keep the change to three lines.

**Files:**
- Modify: `bootstrap/argocd/values.yaml`
- Create: `observability/monitoring/targets/argocd.yaml`, `environments/homelab/apps/monitoring-config.yaml`

**Interfaces:**
- Consumes: the ServiceMonitor CRD from Task 3.
- Produces: the `monitoring-config` Application that Tasks 6–9 add files to. They create no new Application.

- [ ] **Step 1: Prove the metrics Services are absent**

```bash
sg k3s-admin -c 'kubectl -n argocd get svc'
```

Expected: four Services, none ending in `-metrics`. This is the gap.

- [ ] **Step 2: Enable the three metrics Services**

In `bootstrap/argocd/values.yaml`, add to the existing `controller`, `server`
and `repoServer` blocks (do not create new top-level keys — these blocks
already exist at lines 25, 33 and 41):

```yaml
controller:
  # Phase 22. Creates Service argocd-application-controller-metrics on 8082.
  #
  # metrics.serviceMonitor.enabled is deliberately NOT set, here or in the
  # two blocks below. Argo CD self-manages at sync-wave -1 and the
  # ServiceMonitor CRD does not arrive until wave 23, so a ServiceMonitor
  # emitted by this chart would fail to apply on a REBUILT cluster and
  # break the very Application that installs everything else. The
  # ServiceMonitors live in observability/monitoring/targets/argocd.yaml,
  # applied at wave 24 where their CRD already exists.
  metrics:
    enabled: true

server:
  # Creates Service argocd-server-metrics on 8083. See the controller block
  # above for why serviceMonitor stays off.
  metrics:
    enabled: true

repoServer:
  # Creates Service argocd-repo-server-metrics on 8084. See the controller
  # block above for why serviceMonitor stays off.
  metrics:
    enabled: true
```

- [ ] **Step 3: Write `observability/monitoring/targets/argocd.yaml`**

The label selectors below were rendered from argo-cd chart 10.5.0 — note that
each Service's `app.kubernetes.io/name` differs from its object name.

```yaml
# Argo CD's own metrics. Phase 22.
#
# These are in monitoring-config at wave 24, NOT in the argo-cd chart's own
# serviceMonitor toggles -- see the comment in bootstrap/argocd/values.yaml.
#
# The selectors match app.kubernetes.io/name, whose value is NOT the Service
# name: the controller's metrics Service is named
# argocd-application-controller-metrics but labelled argocd-metrics. Rendered
# and verified against argo-cd 10.5.0.
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: argocd-application-controller
  namespace: argocd
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: argocd-metrics
      app.kubernetes.io/part-of: argocd
  endpoints:
    - port: http-metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: argocd-server
  namespace: argocd
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: argocd-server-metrics
      app.kubernetes.io/part-of: argocd
  endpoints:
    - port: http-metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: argocd-repo-server
  namespace: argocd
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: argocd-repo-server-metrics
      app.kubernetes.io/part-of: argocd
  endpoints:
    - port: http-metrics
```

- [ ] **Step 4: Write `environments/homelab/apps/monitoring-config.yaml`**

```yaml
# Everything that needs the CRDs the kube-prometheus-stack chart installs:
# the ServiceMonitors, and the two exporters they point at.
#
# Wave 24 -- after monitoring (23), which installs those CRDs. A custom
# resource applied before its CRD is registered is a failed sync, which is
# why this cannot share a wave with the chart. Same split, same reason, as
# vso-operator (21) -> vso-config (22).
#
# NOTHING sits behind wave 24. An unhealthy monitoring stack therefore gates
# nothing at all, which is the correct position for observability: it
# watches the cluster, and the cluster must never wait on it.
#
# The destination namespace is `monitoring`, but most manifests here do NOT
# land there -- they carry explicit namespaces and write into argocd, vault,
# kube-system and databases. destination.namespace is only the default for a
# manifest that omits one, and every manifest here states its own.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: monitoring-config
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "24"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: observability/monitoring/targets
  destination:
    server: https://kubernetes.default.svc
    namespace: monitoring
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 5: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`. The gate now genuinely exercises the
`ServiceMonitor` schema — if it reports a skip, stop and fix the schema
location rather than adding an exemption.

- [ ] **Step 6: Commit**

```bash
git add bootstrap/argocd/values.yaml observability/monitoring/targets/argocd.yaml environments/homelab/apps/monitoring-config.yaml
git commit -m "$(cat <<'EOF'
Scrape Argo CD, and add the wave-24 Application for custom resources

Argo CD served no metrics at all: the chart creates its three metrics
Services only when metrics.enabled is set, and this install never set
it. So this edits the wave -1 self-managing release -- three toggles,
nothing else.

Its own serviceMonitor toggles stay OFF deliberately. Argo CD syncs at
wave -1 and the ServiceMonitor CRD arrives at wave 23, so a
ServiceMonitor emitted from that chart would fail on a rebuilt cluster
and break the Application that installs everything else.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 7: OPERATOR HAND-OFF**

The Argo CD values file is applied by `bootstrap/argocd/bootstrap.sh`, not by
a push — read that script's header before running it. Hand over:

```
git push origin main
```

Then, if Argo CD does not pick up its own values change, the operator re-runs
the bootstrap script as documented in `bootstrap/argocd/bootstrap.sh`.

- [ ] **Step 8: Verify Argo CD is still healthy FIRST, then scraped**

```bash
sg k3s-admin -c 'kubectl -n argocd get application'
sg k3s-admin -c 'kubectl -n argocd get svc | grep metrics'
```

Expected: **every** Application still Synced/Healthy — check this before
anything else; this task touched the component that reconciles all the
others. Then three `-metrics` Services exist.

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=argocd_app_info' | head -c 400; echo
kill %1
```

Expected: a non-empty `result` array. An empty one with Prometheus healthy
means the ServiceMonitor selector did not match — re-check Task 3 Step 5.

---

## Task 6: Vault metrics

Overrides the chart's HCL and restarts Vault. Its own commit.

**Files:**
- Modify: `platform/vault/values.yaml`
- Create: `observability/monitoring/targets/vault.yaml`

**Interfaces:**
- Produces: `vault_core_unsealed` and the rest of Vault's telemetry at
  `vault.vault.svc:8200/v1/sys/metrics?format=prometheus`, unauthenticated.

- [ ] **Step 1: Read the chart's current default before overriding it**

```bash
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo update hashicorp
helm show values hashicorp/vault --version 0.34.1 | python3 -c "import sys,yaml; print(yaml.safe_load(sys)['server']['ha']['raft']['config'])" 2>/dev/null \
  || helm show values hashicorp/vault --version 0.34.1 > /tmp/vault-values.yaml && python3 -c "
import yaml; print(yaml.safe_load(open('/tmp/vault-values.yaml'))['server']['ha']['raft']['config'])"
```

Expected: the default HCL, which **already contains the telemetry stanza
commented out**. The override below is that exact block with two lines
uncommented — diff yours against this output rather than trusting the plan.

- [ ] **Step 2: Add the override to `platform/vault/values.yaml`**

Inside the existing `server.ha.raft` block, after `setNodeId: true`:

```yaml
      # Phase 22. Vault's /v1/sys/metrics requires a token unless the
      # listener says otherwise, and the alternative to this stanza is a
      # Prometheus token whose renewal is a new failure mode -- to protect
      # an endpoint that exposes operational counters and seal status, not
      # secrets, on a ClusterIP reachable only from inside this cluster.
      # Decision P16 in the phase-22 spec.
      #
      # THIS IS A VERBATIM COPY of chart 0.34.1's default config with the
      # telemetry stanza uncommented and a top-level telemetry block added.
      # Overriding this key means OWNING the whole HCL: when the chart is
      # bumped, diff this against the new default before assuming it still
      # matches. `helm show values hashicorp/vault --version <v>` prints it.
      #
      # Changing this restarts Vault, which reseals it. That is safe here
      # and only here, because platform/vault/config/unsealer.yaml unseals
      # it automatically from the in-cluster key Secret.
      config: |
        ui = true

        listener "tcp" {
          tls_disable = 1
          address = "[::]:8200"
          cluster_address = "[::]:8201"
          telemetry {
            unauthenticated_metrics_access = "true"
          }
        }

        storage "raft" {
          path = "/vault/data"
        }

        service_registration "kubernetes" {}

        telemetry {
          prometheus_retention_time = "30s"
          disable_hostname = true
        }
```

`prometheus_retention_time` must be **at least** the scrape interval
(30s in `observability/monitoring/values.yaml`) or samples expire between
scrapes and the target reports gaps rather than failing outright.

- [ ] **Step 3: Write `observability/monitoring/targets/vault.yaml`**

```yaml
# Vault's telemetry. Phase 22.
#
# Unauthenticated because platform/vault/values.yaml enables
# unauthenticated_metrics_access on the listener -- see the argument there.
# The path and params below are Vault's, not the operator's default /metrics.
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: vault
  namespace: vault
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: vault
      vault-active: "true"
  endpoints:
    - port: http
      path: /v1/sys/metrics
      params:
        format:
          - prometheus
```

**Verify the selector before committing** — the chart labels differ between
the headless, active and standby Services:

```bash
sg k3s-admin -c 'kubectl -n vault get svc --show-labels'
```

Use the labels of the Service that actually carries port `http` 8200 for the
active node, and adjust `matchLabels` and `port` to what you see.

- [ ] **Step 4: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 5: Commit**

```bash
git add platform/vault/values.yaml observability/monitoring/targets/vault.yaml
git commit -m "$(cat <<'EOF'
Scrape Vault, at the cost of owning its listener HCL

/v1/sys/metrics needs a token unless the listener opts out. The
alternative was a Prometheus token whose renewal is a new failure mode,
guarding an endpoint that exposes counters and seal status -- not
secrets -- on a cluster-internal ClusterIP.

The cost is real and recorded in the values file: overriding
server.ha.raft.config means maintaining a verbatim copy of the chart
default, which must be diffed against upstream on every chart bump.

This restarts Vault and therefore reseals it. The in-cluster unsealer
handles that automatically.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 6: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 7: Verify Vault came back unsealed, then that it is scraped**

```bash
sg k3s-admin -c 'kubectl -n vault get pods'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault status | head -5'
```

Expected: `vault-0` Running and **`Sealed: false`**. If it is sealed, check
the unsealer Deployment's logs before going further — nothing else in this
phase matters until Vault is unsealed.

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=vault_core_unsealed' | head -c 300; echo
kill %1
```

Expected: a non-empty result with value `1`.

---

## Task 7: Redis exporter

The cheapest integration: the credential it needs already exists.

**Files:**
- Create: `observability/monitoring/targets/redis-exporter.yaml`

**Interfaces:**
- Consumes: Secret `redis-credentials` in `databases`, key `password` — verified present 2026-09-18 alongside `requirepass.conf` and `username`.
- Produces: `redis_up`.

- [ ] **Step 1: Re-verify the Secret key exists before depending on it**

```bash
sg k3s-admin -c 'kubectl -n databases get secret redis-credentials -o jsonpath="{.data}"' | tr ',' '\n' | sed 's/:.*//'
```

Expected: includes `"password"`. If it does not, VSO's templating behaviour
has changed and this task needs a `password` template added to
`platform/databases/redis/config/vault-secrets.yaml` first.

- [ ] **Step 2: Write the manifest**

```yaml
# redis_exporter -- Redis has no Prometheus endpoint of its own. Phase 22.
#
# In `databases`, beside what it scrapes, because it mounts that namespace's
# redis-credentials Secret. No new Vault path: that Secret already carries a
# bare `password` key alongside the requirepass.conf template, because a VSO
# transformation template ADDS keys rather than replacing the source ones.
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis-exporter
  namespace: databases
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis-exporter
  template:
    metadata:
      labels:
        app: redis-exporter
    spec:
      containers:
        - name: redis-exporter
          image: oliver006/redis_exporter:v1.91.1-alpine
          env:
            - name: REDIS_ADDR
              value: redis://redis.databases.svc.cluster.local:6379
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: redis-credentials
                  key: password
          ports:
            - name: metrics
              containerPort: 9121
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 59000
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
            limits:
              memory: 32Mi
---
apiVersion: v1
kind: Service
metadata:
  name: redis-exporter
  namespace: databases
  labels:
    app: redis-exporter
spec:
  selector:
    app: redis-exporter
  ports:
    - name: metrics
      port: 9121
      targetPort: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: redis-exporter
  namespace: databases
spec:
  selector:
    matchLabels:
      app: redis-exporter
  endpoints:
    - port: metrics
```

- [ ] **Step 3: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 4: Commit**

```bash
git add observability/monitoring/targets/redis-exporter.yaml
git commit -m "$(cat <<'EOF'
Scrape Redis through redis_exporter

It lives in `databases` rather than `monitoring` because it mounts that
namespace's redis-credentials Secret, which already carries a bare
password key -- a VSO transformation template adds keys rather than
replacing the source ones, so no new Vault path is needed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 5: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 6: Verify**

```bash
sg k3s-admin -c 'kubectl -n databases get pods -l app=redis-exporter'
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=redis_up' | head -c 300; echo
kill %1
```

Expected: pod Running; `redis_up` returns value `1`. A `0` means the exporter
reached Redis but authentication failed — check `REDIS_PASSWORD`.

---

## Task 8: PostgreSQL exporter, with its own least-privilege login

**Files:**
- Create: `observability/monitoring/targets/postgres-exporter.yaml`

**Interfaces:**
- Consumes: Vault `homelab/postgres-exporter`, policy `vso-postgres-exporter-read`, role `vso-postgres-exporter` (Task 2); Secret `postgres-credentials` key `password` for the Job's superuser connection.
- Produces: `pg_up`.

- [ ] **Step 1: Read the Job pattern this follows**

```bash
sed -n '1,60p' platform/nexus/config/bootstrap-job.yaml
```

The important part is **why it is a Sync hook rather than a plain Job**: a Job
is immutable, so re-applying an unchanged one does nothing, and
`BeforeHookCreation` deletes the previous one so each sync genuinely re-runs.

- [ ] **Step 2: Write the manifest**

```yaml
# postgres_exporter, and the least-privilege login it uses. Phase 22.
#
# Reusing the `postgres` superuser already in Vault would have been one line
# and would have handed read-everything to a metrics sidecar. Instead a
# dedicated `exporter` login is granted pg_monitor -- the role PostgreSQL
# provides for exactly this -- and its password is generated in Vault by
# platform/vault/configure-vault.sh.
#
# The role is created by an Argo CD Sync HOOK, not a plain Job, for the
# reason platform/nexus/config/bootstrap-job.yaml sets out: a Job is
# immutable, so re-applying an unchanged one does nothing at all, and a
# changed script would never reach the cluster.
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: postgres-exporter
  namespace: databases
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultAuth
metadata:
  name: postgres-exporter
  namespace: databases
spec:
  # Reuses the VaultConnection PostgreSQL created in this namespace, the
  # way redis does.
  vaultConnectionRef: postgres
  method: kubernetes
  mount: kubernetes
  kubernetes:
    role: vso-postgres-exporter
    serviceAccount: postgres-exporter
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: postgres-exporter
  namespace: databases
spec:
  vaultAuthRef: postgres-exporter
  mount: homelab
  type: kv-v2
  path: postgres-exporter
  refreshAfter: 60s
  destination:
    name: postgres-exporter
    create: true
    transformation:
      excludeRaw: true
---
apiVersion: batch/v1
kind: Job
metadata:
  name: postgres-exporter-role
  namespace: databases
  annotations:
    argocd.argoproj.io/hook: Sync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
    # Above the Deployment's default 0 -- the exporter cannot authenticate
    # until the role exists.
    argocd.argoproj.io/sync-wave: "1"
spec:
  backoffLimit: 2
  # A Sync hook that never ends stalls the whole Argo CD sync.
  activeDeadlineSeconds: 300
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: psql
          image: postgres:18.6-alpine
          env:
            - name: PGHOST
              value: postgres.databases.svc.cluster.local
            - name: PGUSER
              value: postgres
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgres-credentials
                  key: password
            - name: EXPORTER_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgres-exporter
                  key: password
          command:
            - /bin/sh
            - -c
            - |
              set -eu
              # Idempotent by construction: CREATE ROLE is guarded by a
              # probe, and the ALTER runs unconditionally so a rotated
              # password in Vault reaches the database on the next sync.
              until pg_isready -q; do
                echo "waiting for postgres"; sleep 2
              done
              if psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='exporter'" | grep -q 1; then
                echo "role exporter exists"
              else
                psql -v ON_ERROR_STOP=1 -c "CREATE ROLE exporter LOGIN"
                echo "role exporter created"
              fi
              # The password is passed through a psql variable, never
              # interpolated into the SQL text, so it cannot reach a log or
              # the process list.
              psql -v ON_ERROR_STOP=1 -v pw="$EXPORTER_PASSWORD" \
                -c "ALTER ROLE exporter WITH PASSWORD :'pw'"
              psql -v ON_ERROR_STOP=1 -c "GRANT pg_monitor TO exporter"
              echo "done"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-exporter
  namespace: databases
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres-exporter
  template:
    metadata:
      labels:
        app: postgres-exporter
    spec:
      serviceAccountName: postgres-exporter
      containers:
        - name: postgres-exporter
          image: quay.io/prometheuscommunity/postgres-exporter:v0.20.1
          env:
            - name: DATA_SOURCE_URI
              value: postgres.databases.svc.cluster.local:5432/postgres?sslmode=disable
            - name: DATA_SOURCE_USER
              value: exporter
            - name: DATA_SOURCE_PASS
              valueFrom:
                secretKeyRef:
                  name: postgres-exporter
                  key: password
          ports:
            - name: metrics
              containerPort: 9187
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 65534
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              memory: 64Mi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-exporter
  namespace: databases
  labels:
    app: postgres-exporter
spec:
  selector:
    app: postgres-exporter
  ports:
    - name: metrics
      port: 9187
      targetPort: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: postgres-exporter
  namespace: databases
spec:
  selector:
    matchLabels:
      app: postgres-exporter
  endpoints:
    - port: metrics
```

- [ ] **Step 3: Confirm the VaultConnection name this reuses actually exists**

```bash
sg k3s-admin -c 'kubectl -n databases get vaultconnection'
```

Expected: a connection named `postgres`. If it is named otherwise, fix
`vaultConnectionRef` above to match — a wrong ref is a permission denial that
names neither side while Argo CD reports Synced.

- [ ] **Step 4: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 5: Commit**

```bash
git add observability/monitoring/targets/postgres-exporter.yaml
git commit -m "$(cat <<'EOF'
Scrape PostgreSQL through a least-privilege exporter login

Reusing the postgres superuser already in Vault would have been one line
and would have given a metrics sidecar read-everything. A dedicated
`exporter` login with pg_monitor costs an idempotent Job and one more
Vault path, and is what the database provides that role for.

The Job is an Argo CD Sync hook rather than a plain Job, for the reason
the Nexus bootstrap job documents: a Job is immutable, so re-applying an
unchanged one does nothing and a changed script never lands.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 6: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 7: Verify the role, then the metrics**

```bash
sg k3s-admin -c 'kubectl -n databases logs job/postgres-exporter-role'
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- psql -U postgres -tAc "SELECT rolname FROM pg_roles WHERE rolname='"'"'exporter'"'"'"'
```

Expected: the log ends in `done`; the query returns `exporter`.

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=pg_up' | head -c 300; echo
kill %1
```

Expected: `pg_up` value `1`.

- [ ] **Step 8: Prove the Job is genuinely idempotent**

```bash
sg k3s-admin -c 'kubectl -n argocd exec deploy/argocd-server -- argocd app sync monitoring-config' \
  || echo "sync via the Argo CD UI instead"
sg k3s-admin -c 'kubectl -n databases logs job/postgres-exporter-role'
```

Expected: the second run logs `role exporter exists` and still ends in `done`.
A failure here means the hook is not re-runnable, which would break every
future resync.

---

## Task 9: Traefik — last, and revertible alone

Highest blast radius, lowest payoff. It touches nothing the other tasks touch.

**Files:**
- Create: `observability/monitoring/targets/traefik.yaml`

- [ ] **Step 1: Record what "working" looks like before changing it**

```bash
sg k3s-admin -c 'kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik'
sg k3s-admin -c 'kubectl -n kube-system get helmchartconfig'
```

Write down the restart count and the pod's age. Expected: no HelmChartConfig
exists yet.

- [ ] **Step 2: Write the manifest**

```yaml
# Traefik's metrics, and the k3s HelmChartConfig that enables them. Phase 22.
#
# HIGHEST BLAST RADIUS, LOWEST PAYOFF of this phase's five integrations, and
# deliberately the only thing in this file so that `git revert` of the commit
# that added it is a complete rollback.
#
# Applying this makes k3s' helm-controller redeploy the Traefik it manages.
# Traefik has restarted 708 times on this node, and it carries NO tailnet
# traffic: every published URL here uses ingressClassName: tailscale and
# reaches its backend through a Tailscale proxy pod, never through Traefik.
# What this buys is edge metrics for a path nothing currently takes -- which
# becomes worth having the moment anything does.
---
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  # NOT this Application's destination namespace. k3s' helm-controller only
  # reads a HelmChartConfig that sits in the same namespace as the HelmChart
  # it modifies, and k3s puts both in kube-system. Elsewhere it is silently
  # ignored: no error, no metrics, and a green Argo CD.
  namespace: kube-system
spec:
  valuesContent: |-
    metrics:
      prometheus:
        service:
          enabled: true
        entryPoint: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: traefik
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: traefik
  endpoints:
    - port: metrics
```

- [ ] **Step 3: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`. This is the run that exercises the
`HelmChartConfig` schema — it is in the datreeio catalog (verified
2026-09-18), so a skip here means the schema-location flags are wrong, not
that an exemption is needed.

- [ ] **Step 4: Commit**

```bash
git add observability/monitoring/targets/traefik.yaml
git commit -m "$(cat <<'EOF'
Scrape Traefik, in one revertible commit

Enabling metrics.prometheus means a HelmChartConfig in kube-system,
which makes k3s' helm-controller redeploy the Traefik it manages. Of
this phase's five integrations that is the highest blast radius and the
lowest payoff: Traefik has restarted 708 times here and carries no
tailnet traffic, because every published URL uses ingressClassName:
tailscale.

It is alone in its own file and its own commit so that reverting it is
a single revert.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 5: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 6: Verify, and know the rollback**

```bash
sg k3s-admin -c 'kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik'
sg k3s-admin -c 'kubectl -n kube-system get svc traefik -o jsonpath="{.spec.ports[*].name}"; echo'
```

Expected: the Traefik pod restarts once and returns to Running; a `metrics`
port appears on the Service. If the Service has no `metrics` port after a few
minutes, the HelmChartConfig was not picked up — check the
`helm-install-traefik` Job's logs in `kube-system`.

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=traefik_entrypoint_requests_total' | head -c 300; echo
kill %1
```

Expected: a non-empty result. **If Traefik does not stabilise**, the rollback
is `git revert` of this one commit followed by a push — nothing else in the
phase depends on it.

---

## Task 10: Measure, document, and close the phase

**Files:**
- Modify: `observability/monitoring/README.md`, `docs/workstation-plan.md`

- [ ] **Step 1: Confirm every target is UP and no ghost targets exist**

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/targets?state=active' | python3 -c "
import sys,json
d=json.load(sys.stdin)['data']['activeTargets']
from collections import Counter
print(Counter((t['labels'].get('job','?'), t['health']) for t in d))
bad=[t['labels'].get('job') for t in d if t['health']!='up']
print('NOT UP:', bad or 'none')
"
kill %1
```

Expected: `NOT UP: none`, and **no job** for kube-scheduler,
kube-controller-manager, kube-proxy or etcd anywhere in the output.

- [ ] **Step 2: Prove Prometheus survives a restart with its history**

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 5
curl -s 'http://localhost:9090/api/v1/query?query=up' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['result'][0]['value'][0])" > /tmp/before.txt
cat /tmp/before.txt
kill %1
sg k3s-admin -c 'kubectl -n monitoring delete pod prometheus-monitoring-kube-prometheus-prometheus-0'
sg k3s-admin -c 'kubectl -n monitoring wait --for=condition=ready pod/prometheus-monitoring-kube-prometheus-prometheus-0 --timeout=300s'
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090' &
sleep 10
TS=$(cat /tmp/before.txt)
curl -s "http://localhost:9090/api/v1/query?query=up&time=${TS}" | head -c 300; echo
kill %1
```

Expected: the query at the pre-restart timestamp still returns data. An empty
result means the volume is not persisting and the PVC is wrong.

- [ ] **Step 3: Prove Grafana is stateless and self-restoring**

```bash
sg k3s-admin -c 'kubectl -n monitoring delete pod -l app.kubernetes.io/name=grafana'
sg k3s-admin -c 'kubectl -n monitoring wait --for=condition=ready pod -l app.kubernetes.io/name=grafana --timeout=300s'
curl -sS -o /dev/null -w '%{http_code}\n' https://grafana.taildf6cd4.ts.net/login
```

Expected: `200`, and after logging in the dashboards and the datasource are
both back with no intervention.

- [ ] **Step 4: Measure memory — the figures, not an impression**

```bash
sg k3s-admin -c 'kubectl top pods -n monitoring'
sg k3s-admin -c 'kubectl top pods -n databases | grep exporter'
sg k3s-admin -c 'kubectl top nodes'
free -g
```

Record every number. These go in the README and get compared against spec §5.

- [ ] **Step 5: Write the real README**

Replace the Task 1 stub. It must contain, in this order:

1. What this is and which phases it covers, linking the spec.
2. **The statelessness warning, in plain words**: a dashboard edited in the
   Grafana UI is lost on restart; anything that must survive is a ConfigMap
   labelled `grafana_dashboard: "1"` committed to this repository.
3. The port-forward command for Prometheus, and the sentence explaining that
   it has no Ingress because it has no authentication.
4. **The measured memory table from Step 4, next to spec §5's estimates**, so
   the next phase's budget starts from a fact.
5. The five scrape targets, each with the one PromQL query that proves it.
6. What is deliberately absent: Alertmanager, the k3s control-plane scrapers,
   curated dashboards, Nexus metrics, and any backup of metrics.
7. The Traefik rollback: revert the single commit, push.

- [ ] **Step 6: Annotate the workstation plan**

In `docs/workstation-plan.md`, under `# 25. Phase 22 — Prometheus` and
`# 26. Phase 23 — Grafana`, record what was actually built — the merge of the
two phases, the delivered architecture, the five scrape targets, and the
pointers to the spec and to `observability/monitoring/README.md`. Follow the
shape §24 uses for Nexus: state plainly where the delivery differs from what
the roadmap described, and why.

- [ ] **Step 7: Run yamllint and the kubeconform gate from Task 1 Step 4**

Expected: clean, `GATE PASSES`.

- [ ] **Step 8: Commit**

```bash
git add observability/monitoring/README.md docs/workstation-plan.md
git commit -m "$(cat <<'EOF'
Close phases 22 and 23 with measured figures

The README carries the real memory numbers rather than the spec's
estimates, the port-forward route to Prometheus, the PromQL query that
proves each of the five targets, and -- in plain words -- the warning
that a dashboard edited in the Grafana UI is lost on restart.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
EOF
)"
```

- [ ] **Step 9: OPERATOR HAND-OFF**

```
git push origin main
```

- [ ] **Step 10: Final state check**

```bash
sg k3s-admin -c 'kubectl -n argocd get application'
```

Expected: 15 Applications, all Synced/Healthy — the 13 that existed plus
`monitoring` and `monitoring-config`.
