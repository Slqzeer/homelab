# Logging Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every container log in this cluster in Loki for 31 days, collected by a Grafana Alloy DaemonSet, queryable from the Grafana that phase 23 already published — completing the `Prometheus → métriques / Loki → logs / Grafana → visualisation` architecture the workstation plan draws in §27.

**Architecture:** Two Helm releases, two Argo CD Applications, both at **wave 23** — the same wave as `monitoring`, not behind it, because neither gates on anything there and `monitoring-config.yaml`'s invariant that nothing sits behind wave 24 must survive this phase. Loki runs in `SingleBinary` mode on a 10Gi `local-path` PVC with filesystem storage; Alloy tails `/var/log/pods` as root and pushes over HTTP. The two ServiceMonitors join the seven already in `observability/monitoring/targets/`, becoming integrations 6 and 7.

**Tech Stack:** `grafana/loki` chart 7.3.0 (Loki 3.6.12), `grafana/alloy` chart 1.12.1 (Alloy v1.19.2), kube-prometheus-stack 91.4.1 (already installed), k3s v1.36.4+k3s1, containerd 2.3.4-k3s1.36, Helm 4.3.0, Argo CD.

**Spec:** `docs/superpowers/specs/2026-09-19-logging-stack-design.md`

## Global Constraints

- **Never `git push`.** Commit, then hand the push command to the operator. A push to `main` is a deploy: Argo CD reconciles `main` with `selfHeal: true`.
- **Work on `main`.** Nothing reconciles a feature branch into this cluster.
- **Validate with the real CI command**, never a bare local `kubeconform`. Local kubeconform without both `-schema-location` flags silently skips every CRD and has already hidden a Critical finding in this repository. The exact commands are in Task 1, Step 5.
- **Pin every version exactly.** Chart `loki` 7.3.0, chart `alloy` 1.12.1, `loki.image.tag: 3.6.12`. No `latest`, no ranges.
- **Never add a `-ignore-filename-pattern` to `validate.yaml`.** This phase needs no CI change at all (spec §9). If you find yourself wanting one, you have named a file wrongly — both values files must be called exactly `values.yaml`.
- **Chart values files are not manifests.** `observability/logging/loki/values.yaml` and `observability/logging/alloy/values.yaml` — one per directory, both named `values.yaml`, which is what keeps the existing `values\.yaml$` CI exemption working with no maintenance.
- **Every manifest that lands outside its Application's destination namespace carries an explicit `metadata.namespace`.** The Grafana datasource ConfigMap goes to `monitoring`; the ServiceMonitors go to `logging`.
- **`yamllint` runs `--strict` against `.yamllint.yaml`.** Warnings fail. Line length max is **100**. Comment-first headers are the house style — no `---` document-start needed on a single-document file.
- **`kubectl` needs the `k3s-admin` group.** If a command returns a permissions error, prefix it: `sg k3s-admin -c '…'`.
- **`sudo` has no TTY in this session.** Any host-root step is an operator hand-off, not something to attempt.
- **`yamllint` runs through Docker, not `pip`.** This host has no `ensurepip`, so `pip install yamllint` fails and `sudo` cannot fix it. The exact command is in Task 1, Step 5, pinned to the same `yamllint==1.38.0` the workflow uses.
- **Docker is a hard requirement of this plan**, not a convenience. Tasks 2 and 3 have Loki 3.6.12 and Alloy v1.19.2 validate their own configuration before it is committed, which catches a class of error no YAML linter can see. `docker` is present on this host and needs no `sudo`.
- Commit messages end with the two trailers used throughout this repository:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M`

---

## File Structure

**Created:**

| Path | Responsibility |
| --- | --- |
| `observability/logging/loki/values.yaml` | Every Loki chart value. The single place the log store is tuned |
| `observability/logging/loki/config/grafana-datasource.yaml` | ConfigMap in ns `monitoring`, label `grafana_datasource: "1"`. The only thing this phase puts in another namespace |
| `observability/logging/alloy/values.yaml` | Every Alloy chart value, including the whole collector pipeline in `alloy.configMap.content` |
| `observability/logging/README.md` | Operational doc: measured memory, port-forward commands, the retention and cardinality warnings |
| `environments/homelab/apps/logging.yaml` | Argo CD Application for Loki, wave 23, three sources |
| `environments/homelab/apps/logging-agent.yaml` | Argo CD Application for Alloy, wave 23 |
| `observability/monitoring/targets/loki.yaml` | ServiceMonitor — integration 6 |
| `observability/monitoring/targets/alloy.yaml` | ServiceMonitor — integration 7 |
| `observability/monitoring/targets/loki-rules.yaml` | PrometheusRule on the Loki PVC's free space. The first in this repository |

**Modified:** `bootstrap/namespaces/namespaces.yaml`, `infrastructure/ingress/config/grafana-ingress.yaml`, `README.md`, `docs/workstation-plan.md`.

**Deleted:** `observability/logging/.gitkeep`.

**Deliberately untouched:** `.github/workflows/validate.yaml` (spec §9 — no CI change needed), `platform/vault/configure-vault.sh` (spec §4.1 — this phase adds no credential), `environments/homelab/apps/monitoring-config.yaml` (spec §4.3 — its wave-24 invariant survives).

---

## Task 1: The `logging` namespace and the directory skeleton

Nothing deploys in this task. It creates the ground everything else lands on, and it establishes the two validation commands every later task runs.

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Create: `observability/logging/README.md` (stub — replaced with measured figures in Task 8)
- Delete: `observability/logging/.gitkeep`

**Interfaces:**
- Produces: namespace `logging`; the validation commands in Step 5, which every later task copies verbatim.

- [ ] **Step 1: Record the starting state**

```bash
kubectl get ns logging
ls -a observability/logging
git log --oneline -1
```

Expected: `Error from server (NotFound): namespaces "logging" not found`; the
directory holds only `.gitkeep`; HEAD is the spec commit.

- [ ] **Step 2: Add the namespace**

Append to `bootstrap/namespaces/namespaces.yaml`, after the `monitoring`
block that currently ends the file:

```yaml
---
# Loki and the Grafana Alloy DaemonSet that feeds it -- workstation-plan
# phase 24. See docs/superpowers/specs/2026-09-19-logging-stack-design.md.
#
# Separate from `monitoring` on purpose: this is a different chart pair with a
# different lifecycle, and phase 22 already set the precedent of putting a
# component outside `monitoring` when ownership argued for it (the PostgreSQL
# and Redis exporters live in `databases`).
#
# The Grafana DATASOURCE for Loki does not live here. It is a ConfigMap in
# `monitoring`, because the grafana-sc-datasources sidecar carries no
# NAMESPACE environment variable and watches only its own namespace --
# unlike grafana-sc-dashboard, which runs with NAMESPACE=ALL. Verified on the
# live pod 2026-09-19.
apiVersion: v1
kind: Namespace
metadata:
  name: logging
```

- [ ] **Step 3: Create the directory skeleton and a stub README**

```bash
mkdir -p observability/logging/loki/config observability/logging/alloy
git rm -q observability/logging/.gitkeep
cat > observability/logging/README.md <<'EOF'
# Logging

Loki and Grafana Alloy. Workstation-plan phase 24.

Design: `docs/superpowers/specs/2026-09-19-logging-stack-design.md`

This file is a stub. It is replaced with measured figures and operational
commands in the last task of `docs/superpowers/plans/2026-09-19-logging-stack.md`.
EOF
```

- [ ] **Step 4: Fetch kubeconform, pinned exactly as CI pins it**

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
curl -sL https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o kc.tgz
echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  kc.tgz" | sha256sum -c -
tar xzf kc.tgz kubeconform
cd /srv/projects/homelab
```

Expected: `kc.tgz: OK`.

- [ ] **Step 5: Run the real CI commands**

These three commands are the gate for every later task. Copy them verbatim;
do not simplify them.

```bash
cd /srv/projects/homelab
KC=/tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad/kubeconform

# yamllint through Docker: this host has no ensurepip and `sudo` has no TTY
# here, so `pip install` fails. The version matches the workflow's exactly.
docker run --rm -v /srv/projects/homelab:/w:ro -w /w python:3.12-alpine \
  sh -c "pip install --quiet yamllint==1.38.0 >/dev/null 2>&1 && \
    yamllint --strict -c .yamllint.yaml \
    bootstrap environments infrastructure observability platform .github"
echo "yamllint exit=$?"

$KC -strict -summary \
  -kubernetes-version 1.36.0 \
  -ignore-filename-pattern 'values\.yaml$' \
  -ignore-filename-pattern 'platform/nexus/registries\.yaml$' \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  bootstrap environments infrastructure observability platform

$KC -summary -kubernetes-version 1.36.0 \
  -ignore-filename-pattern 'values\.yaml$' \
  -ignore-filename-pattern 'platform/nexus/registries\.yaml$' \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  bootstrap environments infrastructure observability platform \
  | tee summary.txt
grep -q 'Skipped: 0' summary.txt && echo "SKIPPED-GATE: PASS"
```

Expected: `yamllint exit=0` with no findings above it; both kubeconform runs
report `Invalid: 0` and `Skipped: 0`; the last line prints
`SKIPPED-GATE: PASS`.

- [ ] **Step 6: Commit**

```bash
git add bootstrap/namespaces/namespaces.yaml observability/logging/README.md
git add -u observability/logging
git commit -F - <<'MSG'
Add the logging namespace for phase 24

Ground for Loki and Grafana Alloy. Separate from `monitoring` because it
is a different chart pair with a different lifecycle -- the same argument
phase 22 used to put the PostgreSQL and Redis exporters in `databases`.

The namespace comment records where the Grafana datasource does NOT go:
grafana-sc-datasources runs with no NAMESPACE environment variable and
watches only its own namespace, unlike grafana-sc-dashboard, which runs
with NAMESPACE=ALL. A datasource ConfigMap placed here would be silently
ignored -- Grafana healthy, sidecar healthy, no datasource.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 7: Hand the push to the operator, then verify**

Print exactly this for the operator to run, and wait:

```bash
git push origin main
```

Once they confirm, verify Argo CD created it:

```bash
kubectl get ns logging
kubectl -n argocd get app namespaces -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
```

Expected: the namespace exists and is `Active`; `namespaces` is
`Synced Healthy`.

---

## Task 2: Loki

The log store. Every value in this task exists because a chart default is
actively wrong here — the spec argues each one in §6, and the comments in the
file repeat the argument where someone might otherwise "restore" a default.

**Files:**
- Create: `observability/logging/loki/values.yaml`
- Create: `environments/homelab/apps/logging.yaml`

**Interfaces:**
- Consumes: namespace `logging` (Task 1).
- Produces: Service `loki` in ns `logging`, port name `http-metrics` (3100) and
  `grpc` (9095); Services `loki-headless` and `loki-memberlist`; StatefulSet
  `loki` with **one** container named `loki`; PVC **`storage-loki-0`**;
  ConfigMap `loki` holding the rendered `config.yaml`. Tasks 4, 5 and 6 depend
  on these exact names.

- [ ] **Step 1: Prove the chart's defaults are what the spec says they are**

Do not skip this. It is the failing-test half of the cycle: it establishes
that the overrides in Step 2 are load-bearing, not decoration.

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
helm repo add grafana https://grafana.github.io/helm-charts 2>/dev/null || true
helm repo update grafana >/dev/null
helm pull grafana/loki --version 7.3.0 --untar

python3 -c "
import yaml; v=yaml.safe_load(open('loki/values.yaml'))
print('deploymentMode      =', v['deploymentMode'])
print('read/write/backend  =', v['read']['replicas'], v['write']['replicas'], v['backend']['replicas'])
print('chunksCache memory  =', v['chunksCache']['allocatedMemory'])
print('resultsCache memory =', v['resultsCache']['allocatedMemory'])
print('auth_enabled        =', v['loki']['auth_enabled'])
print('replication_factor  =', v['loki']['commonConfig']['replication_factor'])
print('schemaConfig        =', v['loki']['schemaConfig'])
print('compactor           =', v['loki']['compactor'])
print('storage.type        =', v['loki']['storage']['type'])
print('sidecar.rules       =', v['sidecar']['rules']['enabled'])
print('pvc whenDeleted     =', v['singleBinary']['persistence']['whenDeleted'])
"
```

Expected, exactly:

```text
deploymentMode      = SimpleScalable
read/write/backend  = 3 3 3
chunksCache memory  = 8192
resultsCache memory = 1024
auth_enabled        = True
replication_factor  = 3
schemaConfig        = {}
compactor           = {}
storage.type        = s3
sidecar.rules       = True
pvc whenDeleted     = Delete
```

Nine wrong defaults and one empty required field. That is the task.

- [ ] **Step 2: Write the values file**

Create `observability/logging/loki/values.yaml`:

```yaml
# Loki -- workstation-plan phase 24, the log store.
# Chart: grafana/loki 7.3.0 (Loki 3.6.12)
# Repo:  https://grafana.github.io/helm-charts
#
# Design: docs/superpowers/specs/2026-09-19-logging-stack-design.md
#
# Almost nothing here is a default, and several settings will look wrong to
# anyone who has run this chart against S3. Two in particular:
#
#   * the caches are OFF -- chunksCache.allocatedMemory defaults to 8192, and
#     this node has roughly 4Gi free.
#   * the PVC retention policy is Retain, against a chart default of Delete.
#
# Both are argued in the spec, sections 5 and 6. Do not "restore" them.

# Decision L3. One process, one pod. The chart's templates/validate.yaml
# FAILS THE RENDER -- "Cannot run scalable targets (backend, read, write) or
# distributed targets without an object storage backend" -- if any of the
# three replica counts below is left at its default of 3.
deploymentMode: SingleBinary

read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0

loki:
  # Pinned explicitly because chart 7.3.0 disagrees with itself: its
  # Chart.yaml declares appVersion 3.6.12 while its values pin the image at
  # 3.6.11. This states which is meant.
  image:
    tag: 3.6.12

  # Decision L5. The chart default is TRUE. Multi-tenancy with one tenant
  # costs an X-Scope-OrgID header on every read and write and buys nothing --
  # and its absence surfaces as "no org id" from the Grafana datasource,
  # which reads like a broken datasource rather than a tenancy setting.
  auth_enabled: false

  # Decision L9. Loki reports usage to Grafana Labs by default.
  analytics:
    reporting_enabled: false

  # Decision L6. The chart default is 3. One ingester cannot satisfy three
  # replicas, and every write fails.
  commonConfig:
    replication_factor: 1

  # Decision L3 again, from the other direction: this key is what
  # loki.isUsingObjectStorage reads, and the chart default is `s3`. Leaving
  # it would make the chart believe an object store exists and re-enable the
  # scalable-target validation above.
  storage:
    type: filesystem

  # Decision L7. MANDATORY -- the chart fails the render without it, because
  # a schema is individual to each Loki cluster and cannot be defaulted.
  #
  # `from:` IS PERMANENT. It is the day this was first applied. Changing it
  # later does not migrate anything: it makes every block written before the
  # new date unreadable, with no recovery path. If a schema change is ever
  # genuinely needed, APPEND a second entry with a future date -- never edit
  # this one.
  schemaConfig:
    configs:
      - from: "2026-09-19"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h

  # Decision L8 and the caps of spec section 5.1.
  #
  # retention_period here is the number people set, and ALONE IT DELETES
  # NOTHING -- the compactor block below is what arms it. See its comment.
  #
  # The three ingest caps bound a CATASTROPHE, not ordinary growth: 4MB/s is
  # still 345GB/day, so they stop a runaway loop and nothing subtler. The cap
  # that does real work is max_global_streams_per_user, lowered from the
  # chart's 5000, because label-cardinality explosion -- not volume -- is
  # what kills a small Loki, and cardinality written into blocks cannot be
  # undone. It is the server-side half of the four-label rule the Alloy
  # config enforces on the way in.
  limits_config:
    retention_period: 744h
    max_global_streams_per_user: 1000
    ingestion_rate_mb: 4
    ingestion_burst_size_mb: 6
    per_stream_rate_limit: 3MB
    per_stream_rate_limit_burst: 10MB

  # Decision L8. THIS is what makes retention happen. The chart's default is
  # `compactor: {}`, which means the compactor never runs retention at all --
  # so a retention_period above with this block missing yields a Loki that
  # looks correctly configured and keeps every log forever. Verified by
  # reading the rendered ConfigMap, not this file: see the plan's Task 7.
  compactor:
    working_directory: /var/loki/compactor
    retention_enabled: true
    delete_request_store: filesystem

singleBinary:
  replicas: 1

  # NOT `loki.resources`. That key exists in the chart's values and is not
  # the one templates/single-binary/statefulset.yaml reads -- it reads this
  # one. Memory-only limit, no CPU limit, matching every other limits: block
  # in this repository.
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      memory: 512Mi

  persistence:
    enabled: true
    storageClass: local-path
    # local-path does not enforce capacity -- the PV is a directory on /srv,
    # which has 846G free. Nominal, not a quota. And unlike Prometheus next
    # door, LOKI HAS NO retentionSize EQUIVALENT: the real bound is
    # retention x ingest rate, watched by the PrometheusRule in
    # observability/monitoring/targets/loki-rules.yaml.
    size: 10Gi
    # Decision L10. The chart defaults BOTH of these to Delete, reasoning in
    # a comment in its own StatefulSet template that single-binary data "is
    # easy to replace". That is true behind S3. Here this PVC is the only
    # copy of 31 days of logs and nothing backs it up, so at the default a
    # `kubectl scale --replicas=0` destroys all of it.
    whenDeleted: Retain
    whenScaled: Retain

# Decision L4. chunksCache.allocatedMemory defaults to 8192 -- 8GiB, from one
# key, on a node with roughly 4Gi free. resultsCache asks for another 1GiB.
# The gateway is an nginx that only matters in front of a scalable
# deployment; with it off, the Service to address is `loki` itself on 3100.
# The canary writes synthetic log lines to measure the pipeline, which is
# monitoring the monitoring; the chart refuses to render `test` without it,
# so the two come off together.
chunksCache:
  enabled: false
resultsCache:
  enabled: false
gateway:
  enabled: false
lokiCanary:
  enabled: false
test:
  enabled: false

sidecar:
  rules:
    # Decision L19. The chart default is true, which injects a SECOND
    # container -- kiwigrid/k8s-sidecar 2.5.0 with `resources: {}` -- into
    # the Loki pod, watching for ruler-rule ConfigMaps this phase never
    # ships. An unbounded sidecar sharing a pod with a limited process is the
    # exact defect observability/monitoring/values.yaml records twice, for
    # the Grafana sidecars and for prometheusConfigReloader.
    #
    # ruler.enabled is deliberately left alone: inside the single binary that
    # target is inert without rules. It is the sidecar that costs.
    enabled: false
```

- [ ] **Step 3: Prove it renders, and that the render is what was intended**

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
helm template loki ./loki -n logging \
  -f /srv/projects/homelab/observability/logging/loki/values.yaml > render.yaml
echo "render exit=$?"
grep -h '^kind:' render.yaml | sort | uniq -c
```

Expected exactly — note there is no Deployment, no memcached, no Job:

```text
      1 kind: ClusterRole
      1 kind: ClusterRoleBinding
      2 kind: ConfigMap
      3 kind: Service
      1 kind: ServiceAccount
      1 kind: StatefulSet
```

Then assert the four things that are easy to get wrong:

```bash
python3 -c "
import yaml
docs=[d for d in yaml.safe_load_all(open('render.yaml')) if d]
sts=[d for d in docs if d['kind']=='StatefulSet'][0]
sp=sts['spec']['template']['spec']
assert [c['name'] for c in sp['containers']]==['loki'], sp['containers']
assert sts['spec']['persistentVolumeClaimRetentionPolicy']=={'whenDeleted':'Retain','whenScaled':'Retain'}
assert sts['spec']['volumeClaimTemplates'][0]['metadata']['name']=='storage'
cfg=yaml.safe_load([d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='loki'][0]['data']['config.yaml'])
assert cfg['auth_enabled'] is False
assert cfg['common']['replication_factor']==1
assert cfg['compactor']['retention_enabled'] is True
assert cfg['compactor']['delete_request_store']=='filesystem'
assert cfg['limits_config']['retention_period']=='744h'
assert cfg['limits_config']['max_global_streams_per_user']==1000
assert cfg['analytics']['reporting_enabled'] is False
assert cfg['schema_config']['configs'][0]['schema']=='v13'
print('RENDER ASSERTIONS: PASS')
"
```

Expected: `RENDER ASSERTIONS: PASS`. One container proves L19; `Retain` proves
L10; `storage` proves the PVC will be named `storage-loki-0`, which Task 6
selects on by name.

- [ ] **Step 4: Have Loki itself validate the config**

Stronger than any linter: the real binary parses the real rendered config.

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
python3 -c "
import yaml
docs=[d for d in yaml.safe_load_all(open('render.yaml')) if d]
cm=[d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='loki'][0]
open('loki-config.yaml','w').write(cm['data']['config.yaml'])
"
docker run --rm -v "$PWD/loki-config.yaml:/tmp/c.yaml:ro" \
  grafana/loki:3.6.12 -config.file=/tmp/c.yaml -verify-config
```

Expected: a log line ending `msg="config is valid"`, exit 0.

- [ ] **Step 5: Write the Argo CD Application**

Create `environments/homelab/apps/logging.yaml`:

```yaml
# Loki -- workstation-plan phase 24, the log store.
#
# Wave 23 -- the SAME wave as monitoring, postgres, redis, registry and
# nexus. NOT a new wave behind them, and the distinction matters.
#
# redis.yaml and nexus.yaml both warn against stacking a wave "out of
# habit": sharing one means these reconcile in parallel and none gates
# another. Applying that test here, Loki gates on nothing at wave 23 --
# auth_enabled is false so there is no Vault credential and the wave-22
# floor vso-config.yaml establishes does not apply, and it needs no CRD.
#
# The consequence is the reason for the choice. monitoring-config.yaml
# records as an invariant that NOTHING sits behind wave 24, because
# observability watches the cluster and the cluster must never wait on it.
# Putting logging at 25 would have broken that invariant and required
# editing that comment. At 23 it survives untouched -- and the two
# ServiceMonitors this phase adds land at 24, ABOVE the Services they
# select, which is also the stricter ordering.
#
# There is deliberately NO Ingress for Loki. It has no authentication of any
# kind, its API includes a delete surface, and
# infrastructure/networking/policy.hujson is still a single
# {"src": ["*"], "dst": ["*"], "ip": ["*"]} grant -- so publishing it would
# hand that surface to every device on the tailnet. This is the same
# argument grafana-ingress.yaml already records for Prometheus. Reach it
# through the Grafana datasource, or by port-forward; the command is in
# observability/logging/README.md.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: logging
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  sources:
    - repoURL: https://grafana.github.io/helm-charts
      chart: loki
      targetRevision: 7.3.0
      helm:
        # Pinned deliberately, and it must stay `loki`. The chart's
        # fullname helper returns the release name alone when it already
        # contains the chart name, so this is what makes the Service
        # `loki` rather than `loki-loki`. The ServiceMonitor in
        # observability/monitoring/targets/loki.yaml, the PrometheusRule's
        # persistentvolumeclaim="storage-loki-0" selector and the Grafana
        # datasource URL all reference that name.
        releaseName: loki
        valueFiles:
          - $values/observability/logging/loki/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: logging
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 6: Run the real CI commands**

Copy Task 1 Step 5 verbatim.

Expected: `yamllint exit=0`; `Invalid: 0`; `SKIPPED-GATE: PASS`. The values
file is skipped by the `values\.yaml$` rule — that is correct, and is why it
had to be named exactly that.

- [ ] **Step 7: Commit**

```bash
git add observability/logging/loki/values.yaml environments/homelab/apps/logging.yaml
git commit -F - <<'MSG'
Add Loki, the phase 24 log store

grafana/loki 7.3.0 in SingleBinary mode on a 10Gi local-path PVC,
filesystem storage, 31-day retention. Argo CD Application at wave 23 --
the same wave as monitoring, not behind it, so the invariant
monitoring-config.yaml records (nothing sits behind wave 24) survives.

Nine chart defaults are overridden, each verified against chart 7.3.0
rather than assumed:

  * SimpleScalable with read/write/backend at 3 -- the chart hard-fails
    the render on filesystem storage, and it is nine pods on a node with
    ~4Gi free.
  * chunksCache.allocatedMemory 8192 and resultsCache 1024 -- 9GiB of
    memcached from two keys.
  * auth_enabled true -- multi-tenancy with one tenant, whose absence
    surfaces as "no org id" from the datasource.
  * replication_factor 3 against one ingester.
  * schemaConfig empty, and mandatory. from: is permanent; the file says
    so where someone would otherwise edit it.
  * compactor {} -- so retention_period alone deletes NOTHING, ever.
  * storage.type s3, which re-enables the scalable-target validation.
  * sidecar.rules true -- an unbounded kiwigrid sidecar watching for rule
    ConfigMaps this phase never ships.
  * PVC retention Delete on both keys, against the only copy of 31 days
    of logs.

Verified before commit by rendering the pinned chart and having Loki
3.6.12 itself parse the result: "config is valid".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 8: Hand the push to the operator, then verify in the cluster**

```bash
git push origin main
```

Once they confirm:

```bash
kubectl -n argocd get app logging -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
kubectl -n logging get pods,pvc,svc
kubectl -n logging get pod loki-0 -o jsonpath='{range .spec.containers[*]}{.name}{"\n"}{end}'
```

Expected: `Synced Healthy`; pod `loki-0` `1/1 Running`; PVC
`storage-loki-0` **Bound**, 10Gi, `local-path`; Services `loki`,
`loki-headless`, `loki-memberlist`; **exactly one** container name printed,
`loki` — the L19 proof, in the cluster this time.

- [ ] **Step 9: Prove Loki is actually ready, not merely Running**

```bash
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
curl -s http://127.0.0.1:3100/ready
echo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/metrics
kill %1
```

Expected: `ready`, then `200`. The `/metrics` check matters because Task 4's
ServiceMonitor scrapes exactly that endpoint on this port.

---

## Task 3: Alloy, the collector

Three of the four overrides in this task exist because the chart default
fails **silently** — the DaemonSet starts, passes its probes, reports Healthy,
and collects nothing or duplicates everything.

**Files:**
- Create: `observability/logging/alloy/values.yaml`
- Create: `environments/homelab/apps/logging-agent.yaml`

**Interfaces:**
- Consumes: Service `loki` on port 3100 in ns `logging` (Task 2).
- Produces: Service `alloy` in ns `logging`, port name `http-metrics` (12345);
  DaemonSet `alloy`; ConfigMap `alloy` holding `config.alloy`. Task 4 selects
  on these names. Stream labels produced: `namespace`, `pod`, `container`,
  `app` — and nothing else.

- [ ] **Step 1: Prove the three silent defaults, and the permission that forces root**

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
helm pull grafana/alloy --version 1.12.1 --untar

python3 -c "
import yaml; a=yaml.safe_load(open('alloy/values.yaml'))
print('storagePath     =', a['alloy']['storagePath'])
print('securityContext =', a['alloy']['securityContext'])
print('enableReporting =', a['alloy']['enableReporting'])
print('mounts.varlog   =', a['alloy']['mounts']['varlog'])
print('crds.create     =', a['crds']['create'])
"
grep -n 'mountPath: /var/log' -B4 alloy/templates/containers/_agent.yaml | head -12
stat -c '%A %U:%G %n' /var/log/pods /var/log/containers
```

Expected:

```text
storagePath     = /tmp/alloy
securityContext = {}
enableReporting = True
mounts.varlog   = False
crds.create     = True
```

and `/var/log/pods` reported as **`drwxr-x--- root:root`**.

Read the `volumeMounts` block printed by the `grep`: it contains `config` and
`varlog` and **nothing mounted at `/tmp/alloy`**. That is the positions file
living on the container's writable layer, which is L12.

- [ ] **Step 2: Write the values file**

Create `observability/logging/alloy/values.yaml`:

```yaml
# Grafana Alloy -- workstation-plan phase 24, the log collector.
# Chart: grafana/alloy 1.12.1 (Alloy v1.19.2)
# Repo:  https://grafana.github.io/helm-charts
#
# Design: docs/superpowers/specs/2026-09-19-logging-stack-design.md
#
# NOT Promtail. grafana/promtail is marked deprecated in the Helm index, its
# last real release was 2025-05, and upstream Promtail reached end of life on
# 2026-03-02.
#
# Three of the overrides below exist because the chart default fails
# SILENTLY: the DaemonSet starts, passes its probes, reports Healthy, and
# either collects nothing (securityContext) or replays every log file on
# every restart (storagePath). A green pod is not evidence here.

# Decision L13. The chart's `crds` subchart installs a monitoring.grafana.com
# PodLogs CRD. This pipeline uses discovery.kubernetes and file tailing, not
# PodLogs, and an unused CRD is a kind in this cluster that CI has never
# validated.
crds:
  create: false

controller:
  # The chart default, restated so a future chart default cannot quietly
  # turn one pod per node into one pod per cluster.
  type: daemonset

  # Decision L12. Where the positions file actually lives. See storagePath
  # below for why this volume has to exist at all.
  volumes:
    extra:
      - name: positions
        hostPath:
          path: /var/lib/alloy
          type: DirectoryOrCreate

alloy:
  # Decision L9. Adds --disable-reporting. Alloy phones home to Grafana Labs
  # by default.
  enableReporting: false

  # Decision L12. The chart starts the container with
  # --storage.path=/tmp/alloy and MOUNTS NO VOLUME THERE -- verified in
  # templates/containers/_agent.yaml, whose volumeMounts block carries only
  # `config` and `varlog`. That path holds loki.source.file's positions, so
  # at the default they die with the pod and Alloy re-reads every log file
  # from byte zero on every restart -- including every restart caused by
  # editing this file.
  storagePath: /var/lib/alloy

  # Decision L11. Measured on this host: /var/log/pods is drwxr-x--- root:root.
  # The chart sets securityContext: {} and the image does not run as root, so
  # WITHOUT THIS LINE the DaemonSet comes up Healthy and tails nothing at all.
  # This is the single most important line in this file.
  securityContext:
    runAsUser: 0

  mounts:
    # hostPath /var/log, mounted readOnly. One mount covers both trees:
    # /var/log/containers/*.log are symlinks INTO /var/log/pods/... , so they
    # resolve inside it.
    varlog: true
    # This cluster runs containerd 2.3.4-k3s1.36. There is no /var/lib/docker.
    dockercontainers: false
    extra:
      - name: positions
        mountPath: /var/lib/alloy

  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      memory: 256Mi

  configMap:
    create: true
    # The whole collector pipeline. Validated by `alloy validate` against
    # v1.19.2 before this file was committed -- see the plan's Task 3.
    #
    # K8S_NODE_NAME is supplied BY THE CHART from spec.nodeName (alongside
    # HOSTNAME and ALLOY_DEPLOY_MODE); this file deliberately does not add a
    # duplicate NODE_NAME of its own.
    #
    # On one node the node filter changes nothing. It costs six lines and
    # keeps the pipeline correct if a second node ever appears.
    content: |-
      discovery.kubernetes "pods" {
        role = "pod"

        selectors {
          role  = "pod"
          field = "spec.nodeName=" + sys.env("K8S_NODE_NAME")
        }
      }

      // Decision L14. EXACTLY FOUR LABELS: namespace, pod, container, app.
      // Not the pod's labels wholesale. Every distinct combination is a
      // separate stream, index entry and chunk set, and relabelling pod
      // labels in is the standard way a small Loki falls over. It is also
      // the one mistake here that cannot be undone, because the cardinality
      // is written into blocks already on disk. Everything else about a pod
      // stays searchable as log CONTENT, which is what Loki is good at.
      discovery.relabel "pod_logs" {
        targets = discovery.kubernetes.pods.targets

        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          target_label  = "namespace"
        }

        rule {
          source_labels = ["__meta_kubernetes_pod_name"]
          target_label  = "pod"
        }

        rule {
          source_labels = ["__meta_kubernetes_pod_container_name"]
          target_label  = "container"
        }

        rule {
          source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name"]
          target_label  = "app"
        }

        // Builds /var/log/pods/*<uid>/<container>/*.log. The separator joins
        // the two source labels into "<uid>/<container>", and the default
        // regex (.*) captures the pair as $1.
        rule {
          source_labels = ["__meta_kubernetes_pod_uid", "__meta_kubernetes_pod_container_name"]
          separator     = "/"
          action        = "replace"
          replacement   = "/var/log/pods/*$1/*.log"
          target_label  = "__path__"
        }
      }

      local.file_match "pod_logs" {
        path_targets = discovery.relabel.pod_logs.output
      }

      loki.source.file "pod_logs" {
        targets    = local.file_match.pod_logs.targets
        forward_to = [loki.process.pod_logs.receiver]
      }

      // stage.cri is NOT optional. containerd writes each line as
      // "<RFC3339Nano> <stream> <F|P> <text>"; without this stage every log
      // line in Loki carries that prefix as content and is stamped with the
      // ingest time rather than the time it was emitted.
      loki.process "pod_logs" {
        stage.cri {}

        forward_to = [loki.write.default.receiver]
      }

      loki.write "default" {
        endpoint {
          url = "http://loki.logging.svc.cluster.local:3100/loki/api/v1/push"
        }
      }
```

- [ ] **Step 3: Have Alloy itself validate the pipeline**

```bash
cd /tmp/claude-1000/-srv-projects-homelab/eaf19b98-a8cb-4a41-b8a3-6c21edd0e78b/scratchpad
python3 -c "
import yaml
v=yaml.safe_load(open('/srv/projects/homelab/observability/logging/alloy/values.yaml'))
open('config.alloy','w').write(v['alloy']['configMap']['content'])
"
docker run --rm -v "$PWD/config.alloy:/tmp/config.alloy:ro" \
  grafana/alloy:v1.19.2 validate /tmp/config.alloy
echo "validate exit=$?"
```

Expected: no output, `validate exit=0`. `validate` checks component names and
argument types, not merely syntax — a typo in `loki.source.file` or an
unknown argument fails here rather than in the cluster.

- [ ] **Step 4: Prove the render carries the four overrides**

```bash
helm template alloy ./alloy -n logging \
  -f /srv/projects/homelab/observability/logging/alloy/values.yaml > arender.yaml

grep -h '^kind:' arender.yaml | sort | uniq -c

python3 -c "
import yaml
docs=[d for d in yaml.safe_load_all(open('arender.yaml')) if d]
assert not any(d['kind']=='CustomResourceDefinition' for d in docs), 'L13 failed'
ds=[d for d in docs if d['kind']=='DaemonSet'][0]
sp=ds['spec']['template']['spec']; c=sp['containers'][0]
assert '--storage.path=/var/lib/alloy' in c['args'], c['args']
assert '--disable-reporting' in c['args'], c['args']
assert c['securityContext']=={'runAsUser': 0}, c['securityContext']
mounts={m['name']: m for m in c['volumeMounts']}
assert mounts['varlog']['mountPath']=='/var/log' and mounts['varlog']['readOnly'] is True
assert mounts['positions']['mountPath']=='/var/lib/alloy'
vols={v['name']: v for v in sp['volumes']}
assert vols['positions']['hostPath']=={'path':'/var/lib/alloy','type':'DirectoryOrCreate'}
assert any(e['name']=='K8S_NODE_NAME' for e in c['env']), c['env']
print('RENDER ASSERTIONS: PASS')
"
```

Expected: six kinds (`ClusterRole`, `ClusterRoleBinding`, `ConfigMap`,
`DaemonSet`, `Service`, `ServiceAccount`) — **no CustomResourceDefinition** —
then `RENDER ASSERTIONS: PASS`.

- [ ] **Step 5: Write the Argo CD Application**

Create `environments/homelab/apps/logging-agent.yaml`:

```yaml
# Grafana Alloy -- workstation-plan phase 24, the log collector.
#
# Wave 23, the SAME wave as logging.yaml rather than behind it. Alloy does
# not gate on Loki: if Loki is not yet serving, loki.write retries with
# backoff and Alloy reports Healthy either way, so stacking a wave here
# would be the habit redis.yaml and nexus.yaml warn against. It needs no
# CRD either -- crds.create is false in its values.
#
# Separate from logging.yaml because it is a separate chart, pinned
# separately. Argo CD renders one chart per Application, and pinning the
# collector independently of the store is the point.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: logging-agent
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  sources:
    - repoURL: https://grafana.github.io/helm-charts
      chart: alloy
      targetRevision: 1.12.1
      helm:
        # Pinned for the same reason logging.yaml pins its own: it prefixes
        # every object the chart creates, and the ServiceMonitor in
        # observability/monitoring/targets/alloy.yaml selects the resulting
        # Service `alloy` by name.
        releaseName: alloy
        valueFiles:
          - $values/observability/logging/alloy/values.yaml
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: logging
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 6: Run the real CI commands**

Copy Task 1 Step 5 verbatim. Expected: `yamllint exit=0`; `Invalid: 0`;
`SKIPPED-GATE: PASS`.

- [ ] **Step 7: Commit**

```bash
git add observability/logging/alloy/values.yaml environments/homelab/apps/logging-agent.yaml
git commit -F - <<'MSG'
Add Grafana Alloy, the phase 24 log collector

grafana/alloy 1.12.1 as a DaemonSet tailing /var/log/pods and pushing to
Loki. Not Promtail: that chart is deprecated in the Helm index and
upstream Promtail reached end of life on 2026-03-02.

Three of the four chart overrides exist because the default fails
SILENTLY -- the pod starts, passes its probes and reports Healthy:

  * securityContext {} against /var/log/pods being drwxr-x--- root:root,
    measured on this host. Without runAsUser: 0 the DaemonSet collects
    nothing at all.
  * --storage.path=/tmp/alloy with no volume mounted there, verified in
    the chart's own container template. The positions file dies with the
    pod, so every restart replays every log file from byte zero --
    including restarts caused by editing this values file.
  * enableReporting true, which phones home to Grafana Labs.

The fourth, crds.create, installs a PodLogs CRD this pipeline never uses.

The pipeline keeps exactly four labels -- namespace, pod, container, app.
Pod labels relabelled in wholesale is what kills a small Loki, and the
cardinality is written into blocks that cannot be rewritten.

Validated before commit by `alloy validate` against v1.19.2, which checks
component names and argument types rather than syntax alone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 8: Hand the push to the operator, then verify**

```bash
git push origin main
```

Once they confirm:

```bash
kubectl -n argocd get app logging-agent -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
kubectl -n logging get ds alloy
kubectl -n logging logs ds/alloy --tail=40
```

Expected: `Synced Healthy`; the DaemonSet `1/1` ready; **and the logs free of
`permission denied`**. This is the L11 check, and it is the one step in this
task a Healthy pod does not prove.

- [ ] **Step 9: Prove logs are actually arriving in Loki**

```bash
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
curl -sG http://127.0.0.1:3100/loki/api/v1/labels | python3 -m json.tool
curl -sG http://127.0.0.1:3100/loki/api/v1/label/namespace/values | python3 -m json.tool
kill %1
```

Expected: the label list is exactly `namespace`, `pod`, `container`, `app`
(plus Loki's own `__stream_shard__` if present) — the L14 proof — and the
namespace values include `vault`, `argocd`, `databases` and `kube-system`,
not merely `logging`. Collection from a namespace other than Alloy's own is
what proves discovery works.

---

## Task 4: Integrations 6 and 7

Loki and Alloy join PostgreSQL, Redis, Vault, Traefik and Argo CD on the
Targets page. This is what turns "Alloy is Healthy" into "Alloy is working" —
the L11 failure mode is invisible without it.

**Files:**
- Create: `observability/monitoring/targets/loki.yaml`
- Create: `observability/monitoring/targets/alloy.yaml`

**Interfaces:**
- Consumes: Services `loki` and `alloy` in ns `logging` (Tasks 2 and 3); the
  `monitoring.coreos.com` CRDs installed by `monitoring` at wave 23.
- Produces: Prometheus jobs `loki` and `alloy`.

- [ ] **Step 1: Prove the selector trap before writing the selector**

```bash
kubectl -n logging get svc --show-labels
```

Expected: **three** Loki Services, and `loki`, `loki-headless` and
`loki-memberlist` all carry `app.kubernetes.io/name=loki` and
`app.kubernetes.io/instance=loki`.

```bash
kubectl -n logging get svc -o custom-columns=\
'NAME:.metadata.name,PORTS:.spec.ports[*].name,SMHINT:.metadata.labels.prometheus\.io/service-monitor'
```

Expected:

```text
NAME              PORTS              SMHINT
alloy             http-metrics       <none>
loki              http-metrics,grpc  <none>
loki-headless     http-metrics       false
loki-memberlist   tcp                <none>
```

`loki-headless` exposes `http-metrics` too. A selector on the name/instance
pair alone would scrape the same pod twice under two `service` labels. The
chart's own `prometheus.io/service-monitor: "false"` is the exclusion signal,
and Step 2 honours it.

- [ ] **Step 2: Write the Loki ServiceMonitor**

Create `observability/monitoring/targets/loki.yaml`:

```yaml
# Loki's own metrics -- integration 6. Phase 24.
#
# In `logging`, beside what it scrapes, like every other file in this
# directory. A ServiceMonitor's default scope is its own namespace, so no
# namespaceSelector is needed. (The Prometheus-side
# serviceMonitorNamespaceSelector: {} is a DIFFERENT selector: it governs
# which ServiceMonitors get discovered, not which Services this one picks.)
#
# The selector is not the obvious one, and the reason is worth the lines.
# This chart renders THREE Services -- loki, loki-headless and
# loki-memberlist -- and all three carry the same app.kubernetes.io/name and
# app.kubernetes.io/instance pair. loki-memberlist exposes only tcp/7946 so
# it yields no target, but loki-headless exposes http-metrics on 3100 just
# like loki does. Selecting on the pair alone therefore scrapes the same
# single pod twice, under two different `service` labels, and every Loki
# series silently doubles.
#
# The chart signals the intent with prometheus.io/service-monitor: "false"
# on the headless Service, and the matchExpressions below honours it. Note
# the subtlety that makes it work: in a Kubernetes label selector, an object
# MISSING the key satisfies NotIn. So `loki`, which carries no such label,
# is selected; `loki-headless`, which carries "false", is not.
#
# helm.sh/chart is deliberately NOT in the selector: it carries the chart
# version (loki-7.3.0) and would break on the next chart bump.
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: loki
  namespace: logging
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: loki
      app.kubernetes.io/instance: loki
    matchExpressions:
      - key: prometheus.io/service-monitor
        operator: NotIn
        values:
          - "false"
  endpoints:
    - port: http-metrics
```

- [ ] **Step 3: Write the Alloy ServiceMonitor**

Create `observability/monitoring/targets/alloy.yaml`:

```yaml
# Alloy's own metrics -- integration 7. Phase 24.
#
# This is the target that makes decision L11 visible. Alloy's failure mode
# when it cannot read /var/log/pods is a pod that starts, passes its probes
# and reports Healthy while collecting nothing: the only signals are
# loki_source_file_* series that stay flat and a log line nobody is reading.
# Scraping it means the Targets page answers the question instead.
#
# No exclusion is needed here, unlike loki.yaml: the alloy chart renders a
# single Service.
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: alloy
  namespace: logging
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: alloy
      app.kubernetes.io/instance: alloy
  endpoints:
    - port: http-metrics
```

- [ ] **Step 4: Run the real CI commands**

Copy Task 1 Step 5 verbatim. Expected: `Invalid: 0` and
`SKIPPED-GATE: PASS`. The `Skipped: 0` line is the one that matters here —
`ServiceMonitor` is a CRD kind, and it is exactly what a bare local
`kubeconform` would skip while still exiting 0.

- [ ] **Step 5: Commit**

```bash
git add observability/monitoring/targets/loki.yaml observability/monitoring/targets/alloy.yaml
git commit -F - <<'MSG'
Scrape Loki and Alloy: integrations 6 and 7

Two ServiceMonitors beside the five this directory already holds for
PostgreSQL, Redis, Vault, Traefik and Argo CD. They live in `logging`
with no namespaceSelector, like every other file here -- a
ServiceMonitor already defaults to its own namespace.

The Alloy one earns its place: when Alloy cannot read /var/log/pods it
still starts, passes its probes and reports Healthy. Without a target,
collecting nothing looks exactly like collecting everything.

The Loki selector is deliberately not the obvious one. The chart renders
three Services, all carrying the same name/instance label pair, and
loki-headless exposes http-metrics on 3100 just as loki does -- so the
naive selector scrapes one pod twice and doubles every Loki series. The
chart marks the headless Service with
prometheus.io/service-monitor: "false"; the matchExpressions NotIn
honours it, selecting loki because a missing key satisfies NotIn.

Neither selector includes helm.sh/chart, which carries the chart version
and would break on the next bump.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 6: Hand the push to the operator, then verify both targets are UP**

```bash
git push origin main
```

Once they confirm, wait about a minute for a scrape, then:

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9091:9090 >/dev/null 2>&1 &
sleep 6
for q in 'up{job="loki"}' 'up{job="alloy"}' 'count(up{job="loki"})'; do
  echo -n "$q -> "
  curl -sG --data-urlencode "query=$q" http://127.0.0.1:9091/api/v1/query \
    | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print([x["value"][1] for x in r])'
done
kill %1
```

Expected: `up{job="loki"} -> ['1']`, `up{job="alloy"} -> ['1']`, and
**`count(up{job="loki"}) -> ['1']`** — one, not two. The count is the
Step 1 trap, checked in the cluster.

---

## Task 5: The Grafana datasource

**Files:**
- Create: `observability/logging/loki/config/grafana-datasource.yaml`
- Modify: `environments/homelab/apps/logging.yaml` (add a third source)

**Interfaces:**
- Consumes: Service `loki` (Task 2); the Grafana from phase 23.
- Produces: a Grafana datasource named `Loki`, non-default.

- [ ] **Step 1: Confirm the sidecar's scope before choosing a namespace**

```bash
kubectl -n monitoring get deploy monitoring-grafana -o json | python3 -c "
import json,sys
for c in json.load(sys.stdin)['spec']['template']['spec']['containers']:
    if c['name'].startswith('grafana-sc-'):
        env={e['name']: e.get('value') for e in c.get('env',[])}
        print(c['name'], 'LABEL=', env.get('LABEL'), 'NAMESPACE=', env.get('NAMESPACE'), 'METHOD=', env.get('METHOD'))
"
```

Expected:

```text
grafana-sc-dashboard LABEL= grafana_dashboard NAMESPACE= ALL METHOD= WATCH
grafana-sc-datasources LABEL= grafana_datasource NAMESPACE= None METHOD= WATCH
```

`NAMESPACE= None` means the environment variable is absent, so the
datasources sidecar watches **only its own namespace**. The ConfigMap must
therefore be created in `monitoring`, not `logging`. `METHOD= WATCH` means it
is picked up live, with no Grafana restart — which matters, because phase 23
decision P4 makes every Grafana restart a ~4-minute cold start and a memory
spike.

- [ ] **Step 2: Write the datasource ConfigMap**

Create `observability/logging/loki/config/grafana-datasource.yaml`:

```yaml
# The Loki datasource for the Grafana that phase 23 published.
#
# NAMESPACE: monitoring, NOT logging, and not this Application's destination
# namespace either. Measured on the live pod 2026-09-19: grafana-sc-dashboard
# runs with NAMESPACE=ALL, but grafana-sc-datasources carries NO NAMESPACE
# environment variable at all and therefore watches only its own namespace.
# Placed in `logging` this file would be silently ignored -- Grafana healthy,
# sidecar healthy, no datasource, no error anywhere.
#
# It lives in THIS directory rather than under observability/monitoring/
# because it dies with Loki: it is a logging artefact pointed at Grafana, not
# a Grafana artefact. The `logging` Application carries it as a third source,
# the same multi-source shape monitoring.yaml already uses for
# observability/monitoring/config.
#
# The sidecar runs METHOD=WATCH, so this is picked up live. Grafana is NOT
# restarted to apply it, which matters: phase 23's decision P4 leaves Grafana
# without a PVC, so every restart is a ~4-minute cold start that re-provisions
# ~24 dashboards in one burst -- the memory peak that OOMKilled it once.
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasource-loki
  namespace: monitoring
  labels:
    # The label the sidecar selects on. Value must be the string "1".
    grafana_datasource: "1"
data:
  loki-datasource.yaml: |-
    apiVersion: 1
    datasources:
      - name: Loki
        type: loki
        access: proxy
        url: http://loki.logging.svc.cluster.local:3100
        # Prometheus stays the default. A default datasource is what an
        # unqualified panel or a fresh Explore tab resolves to, and metrics
        # are the more common starting point on this cluster.
        isDefault: false
        # No basicAuth, no httpHeaderName1 for X-Scope-OrgID: the Loki this
        # points at runs auth_enabled: false (decision L5), so a tenant
        # header would be rejected rather than required.
        jsonData:
          maxLines: 1000
```

- [ ] **Step 3: Add the third source to the Loki Application**

In `environments/homelab/apps/logging.yaml`, insert this block after the
`ref: values` source and before `destination:`:

```yaml
    # The Grafana datasource is not part of the chart, so it needs a source
    # of its own. path is .../loki/config -- NOT .../loki, which also holds
    # values.yaml, and a Helm values file has no kind. Same split, and the
    # same reason, as observability/monitoring/config and platform/vault/config.
    #
    # It writes into `monitoring`, not this Application's destination
    # namespace. The manifest states its own namespace; destination.namespace
    # is only the default for a manifest that omits one.
    - repoURL: git@github.com:Slqzeer/homelab.git
      targetRevision: main
      path: observability/logging/loki/config
```

- [ ] **Step 4: Run the real CI commands**

Copy Task 1 Step 5 verbatim. Expected: `Invalid: 0`, `SKIPPED-GATE: PASS`.

Note that `grafana-datasource.yaml` is a **manifest**, not a values file: it
lives in a `config/` subdirectory precisely so the `values\.yaml$` exemption
does not swallow it, and kubeconform validates it as a `ConfigMap`.

- [ ] **Step 5: Commit**

```bash
git add observability/logging/loki/config/grafana-datasource.yaml
git add environments/homelab/apps/logging.yaml
git commit -F - <<'MSG'
Provision the Loki datasource into Grafana

A ConfigMap labelled grafana_datasource: "1", carried as a third source on
the logging Application -- the same multi-source shape monitoring.yaml
uses for observability/monitoring/config.

It lands in `monitoring`, not `logging`, and that is not a stylistic
choice. Measured on the live pod: grafana-sc-dashboard runs with
NAMESPACE=ALL, but grafana-sc-datasources carries no NAMESPACE variable
at all and watches only its own namespace. In `logging` this file would
be silently ignored, with Grafana healthy, the sidecar healthy and no
error anywhere.

The sidecar runs METHOD=WATCH, so it is picked up live. Grafana is not
restarted: phase 23 left it without a PVC, so every restart is a
four-minute cold start and the memory peak that OOMKilled it once.

isDefault is false -- Prometheus stays the default -- and no tenant
header is set, because this Loki runs auth_enabled: false.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 6: Hand the push to the operator, then verify end to end**

```bash
git push origin main
```

Once they confirm:

```bash
kubectl -n monitoring get cm grafana-datasource-loki
kubectl -n monitoring logs deploy/monitoring-grafana -c grafana-sc-datasources --tail=20
```

Expected: the ConfigMap exists, and the sidecar log shows it writing
`loki-datasource.yaml` into `/etc/grafana/provisioning/datasources`.

Then prove Grafana can actually query it, through the datasource rather than
around it:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 >/dev/null 2>&1 &
sleep 5
GRAFANA_PW=$(kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)
GRAFANA_USER=$(kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-user}' | base64 -d)
curl -s -u "$GRAFANA_USER:$GRAFANA_PW" http://127.0.0.1:3000/api/datasources \
  | python3 -c 'import sys,json; print([(d["name"],d["type"],d["isDefault"]) for d in json.load(sys.stdin)])'
DSUID=$(curl -s -u "$GRAFANA_USER:$GRAFANA_PW" http://127.0.0.1:3000/api/datasources/name/Loki \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["uid"])')
curl -s -u "$GRAFANA_USER:$GRAFANA_PW" "http://127.0.0.1:3000/api/datasources/uid/$DSUID/health"
echo
kill %1
```

Expected: the datasource list contains `('Loki', 'loki', False)` alongside
Prometheus, and the health check returns a JSON body with
`"status": "OK"`. The admin password is read from the Secret and never
typed — the phase-23 convention.

Finally, run the query that spec §11 step 5 actually asks for — real log
lines, fetched *through* Grafana's datasource proxy rather than around it,
which is what Explore does when a human opens it:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 >/dev/null 2>&1 &
sleep 5
NOW=$(date +%s)
curl -s -u "$GRAFANA_USER:$GRAFANA_PW" -G \
  "http://127.0.0.1:3000/api/datasources/proxy/uid/$DSUID/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="vault"}' \
  --data-urlencode "start=$((NOW-1800))000000000" \
  --data-urlencode "end=${NOW}000000000" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print("streams:",len(r),"lines:",sum(len(s["values"]) for s in r))'
kill %1
```

Expected: a non-zero stream and line count. A namespace other than `logging`
on purpose — self-logging would pass even if discovery were broken.

---

## Task 6: The PrometheusRule on the Loki volume

The first `PrometheusRule` in this repository, and the substitute for the
`retentionSize` Loki does not have.

**Files:**
- Create: `observability/monitoring/targets/loki-rules.yaml`

**Interfaces:**
- Consumes: PVC `storage-loki-0` (Task 2); the `kubelet_volume_stats_*`
  series the kubelet ServiceMonitor already produces.
- Produces: alert rules `LokiVolumeFillingUp` and `LokiVolumeCritical`.

- [ ] **Step 1: Confirm the metric and the PVC name exist as assumed**

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9091:9090 >/dev/null 2>&1 &
sleep 6
curl -sG --data-urlencode \
  'query=kubelet_volume_stats_available_bytes{persistentvolumeclaim="storage-loki-0"}' \
  http://127.0.0.1:9091/api/v1/query \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["result"])'
kill %1
```

Expected: a non-empty result with one series. If it is empty, stop: either
the PVC is named something other than `storage-loki-0` (check
`kubectl -n logging get pvc`) or the kubelet target is down, and writing a
rule against a metric that does not exist is worse than writing no rule.

- [ ] **Step 2: Write the rule**

Create `observability/monitoring/targets/loki-rules.yaml`:

```yaml
# Free space on Loki's volume. Phase 24, and the FIRST PrometheusRule in
# this repository.
#
# THESE ALERTS NOTIFY NOBODY. Alertmanager is disabled (phase 22, decision
# P3: no notification destination exists), so these turn red on the
# Prometheus UI's Alerts page and nowhere else. That is deliberate and
# consistent with what already runs -- the stack keeps roughly 25 default
# rules evaluating with no delivery -- but do not read a green Alerts page
# as "somebody would tell me".
#
# Why this file exists at all: Prometheus next door is bounded by
# retentionSize, which observability/monitoring/values.yaml calls "the real
# limit". LOKI HAS NO retentionSize EQUIVALENT. Its only bound is retention
# x ingest rate, and local-path does not enforce PVC capacity -- the PV is a
# directory on /srv, so a pod logging in a loop is bounded by 846G, not by
# the 10Gi on the claim. The ingest caps in the Loki values file bound a
# catastrophe; this rule is what bounds ordinary growth.
#
# In `monitoring`: unlike the ServiceMonitors in this directory, a
# PrometheusRule is not scoped to what it watches, and the series it reads
# (kubelet_volume_stats_*) come from the kubelet target, not from Loki.
---
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: loki-volume
  namespace: monitoring
spec:
  groups:
    - name: loki-volume
      rules:
        - alert: LokiVolumeFillingUp
          # The PVC name is fixed by the chart's volumeClaimTemplate
          # (`storage`) and the StatefulSet name (`loki`, which is fixed by
          # releaseName in environments/homelab/apps/logging.yaml). If any
          # of those three changes, this selector matches nothing and the
          # alert silently stops watching -- which is why the plan's Task 6
          # verifies the series exists before this file is written.
          expr: >-
            kubelet_volume_stats_available_bytes{persistentvolumeclaim="storage-loki-0"}
            / kubelet_volume_stats_capacity_bytes{persistentvolumeclaim="storage-loki-0"}
            < 0.30
          for: 30m
          labels:
            severity: warning
          annotations:
            summary: Loki's volume is below 30% free
            description: >-
              Loki has no size-based retention. Lower retention_period in
              observability/logging/loki/values.yaml, or find the stream
              responsible with
              topk(5, sum by (namespace, pod) (count_over_time({namespace=~".+"}[1h]))).
        - alert: LokiVolumeCritical
          expr: >-
            kubelet_volume_stats_available_bytes{persistentvolumeclaim="storage-loki-0"}
            / kubelet_volume_stats_capacity_bytes{persistentvolumeclaim="storage-loki-0"}
            < 0.15
          for: 10m
          labels:
            severity: critical
          annotations:
            summary: Loki's volume is below 15% free
            description: >-
              Disk pressure on this node evicts pods. local-path does not
              enforce the 10Gi claim, so this grows into /srv until the
              node notices.
```

- [ ] **Step 3: Run the real CI commands**

Copy Task 1 Step 5 verbatim.

Expected: `Invalid: 0` and `SKIPPED-GATE: PASS`. `PrometheusRule` was
confirmed present in the datreeio catalog in phase 22, so **no
`-ignore-filename-pattern` may be added**. If `Skipped` is non-zero, the
schema fetch failed — investigate, do not exempt.

- [ ] **Step 4: Commit**

```bash
git add observability/monitoring/targets/loki-rules.yaml
git commit -F - <<'MSG'
Watch Loki's volume: this repository's first PrometheusRule

Prometheus is bounded by retentionSize, which its values file calls the
real limit. Loki has no equivalent: its only bound is retention x ingest
rate, and local-path does not enforce PVC capacity, so a pod logging in
a loop is bounded by 846G of /srv rather than by the 10Gi claim.

Two rules on kubelet_volume_stats_*, verified to exist for
persistentvolumeclaim="storage-loki-0" before the file was written --
a rule against a metric that does not exist is worse than no rule.

They notify nobody, and the header says so: Alertmanager is disabled
since phase 22, so these turn red on the Alerts page and nowhere else.
That matches the roughly 25 default rules already evaluating without
delivery, but a green Alerts page must not be read as "somebody would
tell me".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 5: Hand the push to the operator, then verify the rule loaded**

```bash
git push origin main
```

Once they confirm, wait for the operator to reload Prometheus (up to a
minute), then:

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9091:9090 >/dev/null 2>&1 &
sleep 6
curl -s http://127.0.0.1:9091/api/v1/rules \
  | python3 -c "
import sys,json
gs=[g for g in json.load(sys.stdin)['data']['groups'] if g['name']=='loki-volume']
assert gs, 'rule group not loaded'
for r in gs[0]['rules']:
    print(r['name'], r['health'], r['state'])
"
kill %1
```

Expected: both rules listed with health `ok` and state `inactive`. `ok`
proves the expression parses and evaluates against real series; `inactive`
proves the volume is not actually filling up.

---

## Task 7: Prove it, and measure it

No files change. This task produces the evidence the next task writes down.
Record every figure — Task 8 needs them.

**Files:** none.

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: the measured figures for `observability/logging/README.md`.

- [ ] **Step 1: Retention is armed, checked against the rendered config**

The values file is not evidence — the chart could have dropped the block.

```bash
kubectl -n logging get cm loki -o jsonpath='{.data.config\.yaml}' \
  | python3 -c "
import sys,yaml
c=yaml.safe_load(sys.stdin)
print('retention_period      =', c['limits_config']['retention_period'])
print('retention_enabled     =', c['compactor']['retention_enabled'])
print('delete_request_store  =', c['compactor']['delete_request_store'])
print('max_global_streams    =', c['limits_config']['max_global_streams_per_user'])
print('replication_factor    =', c['common']['replication_factor'])
print('auth_enabled          =', c['auth_enabled'])
"
```

Expected: `744h`, `True`, `filesystem`, `1000`, `1`, `False`. A `True` on the
second line is the whole point of decision L8 — without it the first line is
decorative.

- [ ] **Step 2: The compactor is actually running**

```bash
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
curl -s http://127.0.0.1:3100/metrics | grep -E '^loki_compactor_(running|apply_retention)' | head
kill %1
```

Expected: at least `loki_compactor_running 1`. If the compactor is not
running, retention is configured and inert.

- [ ] **Step 3: Loki survives a restart with its history**

```bash
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
BEFORE=$(date +%s)
curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="vault"}' \
  --data-urlencode "start=$((BEFORE-600))000000000" \
  --data-urlencode "end=${BEFORE}000000000" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print("streams:",len(r),"lines:",sum(len(s["values"]) for s in r))'
kill %1
echo "BEFORE=$BEFORE  -- write this number down"
```

Record the line count. Then restart and re-ask the **same** window:

```bash
kubectl -n logging delete pod loki-0
kubectl -n logging wait --for=condition=ready pod/loki-0 --timeout=300s
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 8
curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="vault"}' \
  --data-urlencode "start=$((BEFORE-600))000000000" \
  --data-urlencode "end=${BEFORE}000000000" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print("streams:",len(r),"lines:",sum(len(s["values"]) for s in r))'
kill %1
```

Expected: the same line count for the same pre-restart window. This is the
phase-22 convention — history survives the volume, it does not restart empty.
It also proves L10's `Retain` did not need to be exercised.

- [ ] **Step 4: Alloy survives a restart without replaying**

```bash
kubectl -n logging exec ds/alloy -- ls -la /var/lib/alloy
```

Expected: a positions database exists on the hostPath (not an empty dir).

```bash
NOW=$(date +%s)
kubectl -n logging delete pod -l app.kubernetes.io/name=alloy
kubectl -n logging rollout status ds/alloy --timeout=180s
sleep 30
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="vault"}' \
  --data-urlencode "start=$((NOW-3600))000000000" \
  --data-urlencode "end=$((NOW-1800))000000000" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["data"]["result"]; print("lines:",sum(len(s["values"]) for s in r))'
kill %1
```

Expected: a line count for that **old, already-ingested** window that is
unchanged from before the restart — not roughly doubled. A doubled count
means the positions file was not persisted and L12 failed.

- [ ] **Step 5: Exactly four labels**

```bash
kubectl -n logging port-forward svc/loki 3100:3100 >/dev/null 2>&1 &
sleep 5
curl -sG http://127.0.0.1:3100/loki/api/v1/labels \
  | python3 -c 'import sys,json; print(sorted(json.load(sys.stdin)["data"]))'
curl -sG http://127.0.0.1:3100/loki/api/v1/label/namespace/values \
  | python3 -c 'import sys,json; v=json.load(sys.stdin)["data"]; print(len(v), sorted(v))'
kill %1
```

Expected: the label list is `app`, `container`, `namespace`, `pod` (Loki may
add `__stream_shard__` of its own), and the namespace count is close to the
cluster's namespace count — **record both numbers**. Anything resembling a
pod label leaking in means the relabel rules in the Alloy config were
widened; fix it now, because cardinality written into blocks cannot be
rewritten.

- [ ] **Step 6: Measure memory and disk — record these for Task 8**

```bash
kubectl top pods -n logging
kubectl top nodes
kubectl -n logging get pvc storage-loki-0
kubectl -n logging exec loki-0 -- du -sh /var/loki 2>/dev/null
kubectl describe node slqzeer-ms7c56 | sed -n '/Allocated resources/,/^Events/p'
```

Record: the Loki pod's memory, the Alloy pod's memory, the node's total
memory percentage, the node's limits percentage, and `/var/loki`'s size.
Compare against the spec's §5 budget (Loki 250–350Mi, Alloy 100–150Mi,
~350–500Mi total). If the total is materially above the budget, say so in
Task 8's README rather than quietly adjusting the budget — the phase-22
practice, which is how the Grafana OOMKill was caught.

- [ ] **Step 7: Confirm the whole Argo CD picture is still green**

```bash
kubectl -n argocd get app -o custom-columns=\
'NAME:.metadata.name,WAVE:.metadata.annotations.argocd\.argoproj\.io/sync-wave,SYNC:.status.sync.status,HEALTH:.status.health.status'
```

Expected: 17 Applications, every one `Synced Healthy`, with `logging` and
`logging-agent` both at wave `23`.

---

## Task 8: Documentation

The phase is not finished until the next person can find out what was built
and why without reading the chart.

**Files:**
- Create (replacing the Task 1 stub): `observability/logging/README.md`
- Modify: `infrastructure/ingress/config/grafana-ingress.yaml`
- Modify: `README.md`
- Modify: `docs/workstation-plan.md`

**Interfaces:**
- Consumes: the measured figures from Task 7 Step 6 and the label/namespace
  counts from Task 7 Step 5.

- [ ] **Step 1: Write `observability/logging/README.md`**

Replace the stub entirely. Substitute the five values marked `MEASURED-…`
with the figures recorded in Task 7 — do not leave the markers in place, and
do not guess: if a figure was not recorded, go back and measure it.

````markdown
# Logging

Loki and Grafana Alloy — workstation-plan phase 24.

Design: `docs/superpowers/specs/2026-09-19-logging-stack-design.md`

## What this is

Every container log in this cluster, kept for **31 days**, collected by an
Alloy DaemonSet that tails `/var/log/pods`, stored by a single-process Loki
on a 10Gi `local-path` PVC, and queried from the Grafana that phase 23
published.

Not Promtail: that chart is deprecated in the Helm index and upstream
Promtail reached end of life on 2026-03-02.

| Component | Chart | Version | Namespace | Wave |
| --- | --- | --- | --- | --- |
| Loki | `grafana/loki` | 7.3.0 (Loki 3.6.12) | `logging` | 23 |
| Alloy | `grafana/alloy` | 1.12.1 (Alloy v1.19.2) | `logging` | 23 |

## Measured

Measured MEASURED-DATE on `slqzeer-ms7c56`, after the restart tests.

| Figure | Value |
| --- | --- |
| Loki memory | MEASURED-LOKI-MEM |
| Alloy memory | MEASURED-ALLOY-MEM |
| `/var/loki` on disk | MEASURED-DISK |
| Node memory | MEASURED-NODE-MEM |
| Namespaces collected | MEASURED-NS-COUNT |

## How to reach it

There is **no Ingress and no tailnet URL for Loki**, and that is deliberate:
Loki has no authentication of any kind, its API includes a delete surface,
and `infrastructure/networking/policy.hujson` is still a single
`{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant. The same argument
`grafana-ingress.yaml` records for Prometheus.

Normally: through Grafana, at <https://grafana.taildf6cd4.ts.net>, Explore,
datasource **Loki**.

Directly, when Grafana is the thing that is broken:

```bash
kubectl -n logging port-forward svc/loki 3100:3100
curl -sG http://127.0.0.1:3100/loki/api/v1/labels
curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode '{namespace="vault"}'
```

## Things that will surprise you

**Retention takes three keys, not one.** `retention_period` alone deletes
nothing: the chart's default `compactor: {}` means the compactor never runs
retention. `compactor.retention_enabled` and `delete_request_store` are what
arm it. Check the rendered config, never the values file:

```bash
kubectl -n logging get cm loki -o jsonpath='{.data.config\.yaml}' | grep -A3 '^compactor:'
```

**There is no size cap.** Prometheus next door is bounded by
`retentionSize`, which its values file calls the real limit. Loki has no
equivalent — the only bound is retention × ingest rate, and `local-path` does
not enforce the 10Gi claim, so growth runs into `/srv` (846G) rather than
stopping at the PVC. Two things stand in: the ingest caps in `loki/values.yaml`,
which bound a catastrophe rather than ordinary growth, and the
`PrometheusRule` in `observability/monitoring/targets/loki-rules.yaml`, which
bounds growth. **That rule notifies nobody** — Alertmanager is off since
phase 22 — so it turns red on the Prometheus Alerts page and nowhere else.

**Four labels, on purpose.** `namespace`, `pod`, `container`, `app`. Not the
pod's labels. Every combination is a separate stream, index entry and chunk
set; relabelling pod labels in is how a small Loki dies, and the cardinality
is written into blocks that cannot be rewritten. Everything else about a pod
is searchable as log *content*.

**The schema date is permanent.** `schemaConfig.configs[0].from` is the day
Loki was first applied. Editing it does not migrate anything — it makes every
block written before the new date unreadable. If a schema change is ever
needed, **append** an entry with a future date.

**Alloy runs as root.** `/var/log/pods` is `drwxr-x--- root:root`. Without
`runAsUser: 0` the DaemonSet starts, passes its probes, reports Healthy and
collects nothing. A green pod is not evidence; the `alloy` target on the
Prometheus Targets page is.

**Logs are not backed up.** Plan §32 does not list them. A lost PVC costs 31
days of logs and nothing else — every byte of configuration is in git.

## Files

| Path | What |
| --- | --- |
| `loki/values.yaml` | Loki chart values |
| `loki/config/grafana-datasource.yaml` | The datasource ConfigMap — in `monitoring`, not here, because the Grafana datasources sidecar watches only its own namespace |
| `alloy/values.yaml` | Alloy chart values, including the whole pipeline |
| `../monitoring/targets/loki.yaml` | ServiceMonitor — integration 6 |
| `../monitoring/targets/alloy.yaml` | ServiceMonitor — integration 7 |
| `../monitoring/targets/loki-rules.yaml` | The volume alert |
````

- [ ] **Step 2: Extend the Ingress comment to name Loki**

In `infrastructure/ingress/config/grafana-ingress.yaml`, replace the
paragraph that begins `# There is deliberately NO Ingress for Prometheus.`
with:

```yaml
# There is deliberately NO Ingress for Prometheus, and none for Loki either.
# Neither has authentication of any kind -- Prometheus exposes an admin API,
# Loki exposes a delete surface -- and
# infrastructure/networking/policy.hujson is currently a single
# {"src": ["*"], "dst": ["*"], "ip": ["*"]} grant, so publishing either
# would hand that surface to every device on the tailnet. Reach them with a
# port-forward; the commands are in observability/monitoring/README.md and
# observability/logging/README.md.
```

- [ ] **Step 3: Update the root `README.md`**

Add a row to the directory table, immediately after the
`observability/monitoring/` row:

```markdown
| `observability/logging/` | Loki and Grafana Alloy: Helm values for both charts, the Grafana datasource, and the collector pipeline. See `observability/logging/README.md` |
```

Then, in the wave narrative, immediately after the sentence ending
`lets them reconcile in parallel since none of the five depends on another.`,
insert:

```markdown
Phase 24 added `logging` and `logging-agent` to that same wave for a
*different* reason: they need neither the VSO operator nor Vault's configure
ceremony — Loki runs `auth_enabled: false` and holds no credential at all —
and nothing at wave 23 gates them. By the rule below they therefore belong at
or below 23; stacking them higher would have broken the wave-24 invariant the
next sentences describe, for no dependency that exists.
```

- [ ] **Step 4: Rewrite `docs/workstation-plan.md` §27**

Replace the whole of section 27 — from the `# 27. Phase 24 — Logging`
heading down to the `---` that precedes `# 28. Phase 25` — with:

```markdown
# 27. Phase 24 — Logging

Livrée. Loki stocke les logs de conteneurs du cluster, collectés par un
DaemonSet Grafana Alloy, et interrogés depuis le Grafana de la phase 23.
L'architecture visée est donc complète :

```text
Prometheus → métriques
Loki       → logs
Grafana    → visualisation
```

**Alloy, pas Promtail** : le chart `grafana/promtail` est marqué `deprecated`
dans l'index Helm, sa dernière version réelle date de mai 2025, et Promtail a
atteint sa fin de vie le 2026-03-02. Le chart `loki-stack`, qui aurait livré
les deux en une seule release, est lui aussi déprécié et fige Loki en 2.9.3.

Loki tourne en mode `SingleBinary` — un seul processus, un seul pod — sur un
PVC 10Gi `local-path`, donc sous `/srv/kubernetes/storage`, avec un stockage
`filesystem` et une rétention de **31 jours**. Le mode par défaut du chart
(`SimpleScalable`, neuf pods) et ses deux caches memcached — dont un qui
réclame **8 GiB** à lui seul — sont désactivés : ce nœud n'a qu'environ 4 Gio
disponibles.

**La rétention demande trois clés, pas une.** `retention_period` seul ne
supprime rien, parce que le `compactor: {}` par défaut du chart n'exécute
jamais la rétention. C'est `compactor.retention_enabled` et
`delete_request_store` qui l'arment réellement.

**Loki n'a pas d'équivalent de `retentionSize`.** Contrairement à Prometheus,
dont le plafond en taille est décrit dans `observability/monitoring/values.yaml`
comme « la vraie limite », la seule borne ici est rétention × débit, et
`local-path` n'applique pas la capacité du PVC. Deux choses s'y substituent :
des plafonds d'ingestion qui bornent une catastrophe et non la croissance
ordinaire, et une `PrometheusRule` sur l'espace libre du volume — la première
de ce dépôt. Cette règle **ne notifie personne** : Alertmanager reste
désactivé depuis la phase 22, elle passe au rouge dans l'UI de Prometheus et
nulle part ailleurs.

Le collecteur conserve exactement **quatre labels** — `namespace`, `pod`,
`container`, `app` — et non les labels du pod : chaque combinaison est un flux
distinct, et une explosion de cardinalité est ce qui tue un petit Loki, sans
retour possible une fois écrite dans les blocs.

Loki et Alloy sont scrapés par Prometheus (intégrations 6 et 7). Ce n'est pas
décoratif : quand Alloy ne peut pas lire `/var/log/pods`, il démarre, passe
ses probes et se déclare *Healthy* tout en ne collectant rien.

Il n'y a **pas d'Ingress pour Loki**, pour la raison exacte déjà retenue pour
Prometheus : aucune authentification, une API qui inclut une surface de
suppression, et une policy tailnet toujours en `*` → `*`. On l'atteint par le
datasource Grafana, ou par port-forward.

Aucune sauvegarde des logs, conformément à la section 32 : perdre le volume
coûte 31 jours de logs et rien d'autre.

Détails complets, mesures et décisions (L1–L19) dans
`docs/superpowers/specs/2026-09-19-logging-stack-design.md` et
`observability/logging/README.md`.
```

- [ ] **Step 5: Run the real CI commands**

Copy Task 1 Step 5 verbatim. `yamllint` is the one that matters here — the
edited `grafana-ingress.yaml` must still pass `--strict` at 100 columns.

- [ ] **Step 6: Commit**

```bash
git add observability/logging/README.md infrastructure/ingress/config/grafana-ingress.yaml
git add README.md docs/workstation-plan.md
git commit -F - <<'MSG'
Document phase 24 with measured figures

observability/logging/README.md replaces the stub: what was built, the
measured memory and disk figures, how to reach Loki without an Ingress,
and the five things that will surprise the next person -- retention
needing three keys, the missing size cap, the four-label rule, the
permanent schema date, and why Alloy runs as root.

grafana-ingress.yaml's "no Ingress for Prometheus" paragraph now names
Loki for the same reason, so there is one statement rather than two
half-stated ones.

The root README gains the observability/logging/ row, and the wave
narrative records why logging and logging-agent share wave 23 for a
different reason than the five components already there: they gate on
nothing, so stacking them higher would have broken the wave-24 invariant
for no dependency that exists.

workstation-plan section 27 is rewritten from "ajouter plus tard un
systeme de logs" to what was actually built.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M
MSG
```

- [ ] **Step 7: Hand the final push to the operator**

```bash
git push origin main
```

Once they confirm, close the phase:

```bash
kubectl -n argocd get app -o custom-columns=\
'NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
```

Expected: 17 Applications, all `Synced Healthy`. Phase 24 is complete.

---

## Rollback

Each task is one commit and reverts alone. The two that need care:

- **Task 2 (Loki).** `git revert` removes the Application, and `prune: true`
  deletes the StatefulSet — but **not** the PVC, because L10 sets
  `whenDeleted: Retain`. That is deliberate: a revert should not destroy 31
  days of logs. Delete `storage-loki-0` by hand if you actually want the
  space back.
- **Task 5 (datasource).** Reverting it removes a ConfigMap in `monitoring`.
  The sidecar WATCHes, so Grafana drops the datasource live; it is not
  restarted, and phase 23's cold-start memory spike is not triggered.

Nothing in this phase modifies a component outside `logging` except the
Grafana datasource ConfigMap, the two ServiceMonitors, the PrometheusRule and
four documentation files. Argo CD's own release, Vault, Traefik and
`configure-vault.sh` are untouched.
