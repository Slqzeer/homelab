# Logging Stack — Design

Covers workstation-plan phase 24 (§27, "Logging"), which asks for a log
system — "par exemple Loki" — completing the three-part architecture the plan
draws there:

```text
Prometheus → métriques
Loki       → logs
Grafana    → visualisation
```

Phase 22/23 built the first and third. This phase builds the second and wires
it into the third.

## 1. Goal

A Loki that stores every container log in this cluster for 31 days, and a
Grafana that can query it — reached through the datasource, never through a
URL of its own.

The collector is **Grafana Alloy**, not Promtail. Promtail is marked
`deprecated: true` in the Helm index, its last real release was 2025-05, and
upstream Promtail reached end of life on 2026-03-02. Shipping it into a phase
opened in September 2026 would be installing a dead component on purpose.

The phase is finished when logs from a namespace other than `logging` render
in Grafana Explore; when `loki` and `alloy` both report **UP** on the
Prometheus Targets page as integrations 6 and 7; when the rendered Loki
ConfigMap is shown to carry `retention_enabled: true` rather than a retention
period the compactor never acts on; when Loki survives a pod restart with its
history intact and Alloy survives one without replaying every log file from
byte zero; and when memory has been **measured**, not assumed.

## 2. Measured starting state

Measured 2026-09-19 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 15 including `root` |
| k3s | `v1.36.4+k3s1`, single node, control-plane |
| Container runtime | `containerd://2.3.4-k3s1.36` — **not Docker** |
| **Memory** | **11.2Gi used of 15.5Gi — ~4Gi available** |
| Node limits already committed | **70%** (11242Mi); requests 26% |
| `/srv` free | 846G of 938G |
| PVCs | 4 — `data-vault-0` 5Gi, `data-postgres-0` 10Gi, `nexus-data` 20Gi, Prometheus 20Gi |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `ingress-config` 21, `vso-operator` 21, `vso-config` 22, `postgres` 23, `redis` 23, `registry` 23, `nexus` 23, `monitoring` 23, `monitoring-config` 24, `beacon` 24 |
| ServiceMonitors | 8 in ns `monitoring` from the chart; 7 hand-written in `targets/`, most of them in other namespaces |
| `PrometheusRule` objects in this repository | **Zero.** This phase ships the first |
| `kubelet_volume_stats_available_bytes` | **Present**, 8 series — verified through the Prometheus API |
| `grafana-sc-dashboard` sidecar | `LABEL=grafana_dashboard`, **`NAMESPACE=ALL`** |
| `grafana-sc-datasources` sidecar | `LABEL=grafana_datasource`, **no `NAMESPACE` env** — own namespace only |
| `/var/log/pods` | **`drwxr-x--- root:root`** |
| `/var/log/containers/*.log` | symlinks into `/var/log/pods/…` — inside the same mount |
| Chart `grafana/loki` | **7.3.0**, appVersion 3.6.12 |
| Chart `grafana/alloy` | **1.12.1**, appVersion v1.19.2 |
| Chart `grafana/promtail` | **deprecated** |
| Chart `grafana/loki-stack` | **deprecated**, pins Loki 2.9.3 |

Memory is the binding constraint of this phase, as it was in 18, 21, 22 and
23. §5 is a budget to be checked against reality in §11, not a promise.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| L1 | `loki` chart from `grafana`, pinned to **7.3.0**, and `loki.image.tag` pinned **explicitly** | The rule since phase 18: never a floating version on a component that holds data. The explicit image pin because chart 7.3.0 declares `appVersion: 3.6.12` while its values pin `3.6.11` — the chart disagrees with itself, so the values file states which is meant |
| L2 | `alloy` chart from `grafana`, pinned to **1.12.1** | Same rule. Alloy replaces Promtail, which is EOL (§1) |
| L3 | **`deploymentMode: SingleBinary`**, `singleBinary.replicas: 1`, `read`/`write`/`backend` explicitly `0` | §6. The chart's `validate.yaml` hard-fails the render otherwise, and its default `SimpleScalable` is nine pods against 4Gi |
| L4 | **`chunksCache`, `resultsCache`, `gateway`, `lokiCanary` and `test` disabled** | §6. `chunksCache.allocatedMemory` defaults to **8192** — twice the memory this node has left, from one key |
| L5 | **`auth_enabled: false`** | §6. The chart defaults it to `true`; multi-tenancy with one tenant costs an `X-Scope-OrgID` header on every read and write and buys nothing |
| L6 | **`commonConfig.replication_factor: 1`** | §6. Default 3; one ingester cannot satisfy three replicas |
| L7 | `schemaConfig`: TSDB, **`v13`**, `index.period: 24h`, `object_store: filesystem`, `from:` fixed at first apply and **never edited** | §6. Mandatory — the chart fails the render without it. A retroactive `from:` makes every earlier block unreadable |
| L8 | Retention **31 days**, armed by **three** keys: `limits_config.retention_period: 744h`, `compactor.retention_enabled: true`, `compactor.delete_request_store: filesystem` | §6. The chart's `compactor: {}` means a retention period alone deletes nothing, ever — the most likely way this phase could ship looking correct and silently keep logs forever |
| L9 | **`analytics.reporting_enabled: false`** on Loki and **`enableReporting: false`** on Alloy | Both phone home to Grafana Labs by default. A homelab should not, unasked |
| L10 | Loki PVC **10Gi `local-path`**, `persistentVolumeClaimRetentionPolicy` **`Retain`** on both `whenDeleted` and `whenScaled` | §5.1. The chart defaults both to `Delete`, reasoning in its own StatefulSet template that single-binary data "is easy to replace" — true behind S3, false here, where the PVC is the only copy of 31 days of logs |
| L11 | **`alloy.securityContext.runAsUser: 0`** | §7. `/var/log/pods` is `drwxr-x--- root:root`. Without it the DaemonSet starts, passes its probes, reports Healthy and tails nothing |
| L12 | Alloy positions moved off `/tmp/alloy` onto a **hostPath `/var/lib/alloy`** | §7. The chart starts Alloy with `--storage.path=/tmp/alloy` and mounts no volume there, so positions die with the pod and every restart replays every file |
| L13 | **`crds.create: false`** on the Alloy chart | §7. It installs a `monitoring.grafana.com` `PodLogs` CRD this design never uses, and an unused CRD is a kind in the cluster CI has never validated |
| L14 | Exactly four stream labels: `namespace`, `pod`, `container`, `app` | §7.2. Pod labels relabelled in wholesale is the standard way a small Loki dies, and cardinality baked into written blocks cannot be undone |
| L15 | **Two Applications, both at wave 23** — `logging` and `logging-agent` | §4.3. Neither gates on anything at 23, and `monitoring-config.yaml`'s invariant that nothing sits behind wave 24 survives this phase unedited |
| L16 | The two ServiceMonitors go into the **existing `observability/monitoring/targets/`**, and the charts' own `serviceMonitor` toggles stay **off** | §8. That directory is the single answer to "what does Prometheus scrape?", it is CI-validated, and Loki's chart-side toggle drags `metricsInstance.enabled: true` with it — a CRD from the grafana-agent-operator, which this cluster does not have |
| L17 | **No Ingress for Loki** | §8.3. Identical to P9's argument for Prometheus: no authentication, and the tailnet policy is still one `*` → `*` grant |
| L18 | **No backup of logs**, and a `PrometheusRule` instead of a size cap | §5.1. Plan §32 does not list logs. Loki has no `retentionSize` analogue, so growth is bounded by a rule that turns red in the Prometheus UI, not by the store itself |
| L19 | **`sidecar.rules.enabled: false`** | §6. The chart's default puts a `kiwigrid/k8s-sidecar` container with `resources: {}` inside the Loki pod to watch for ruler-rule ConfigMaps this phase never ships — an unbounded sidecar, the exact defect phase 22 found twice |

### 3.1 Rejected alternatives

- **`loki-stack`, one chart and one Application.** Genuinely simpler: one
  release, one values file, one wave, Loki and its collector together.
  Rejected because it is marked deprecated, pins Loki **2.9.3** — two majors
  behind — and installs Promtail. Buying one fewer Application with a
  two-major-old log store and an EOL agent is a bad trade in the very phase
  whose purpose is to start keeping logs.
- **`SimpleScalable` with the MinIO subchart.** The topology Loki is actually
  designed for, and it would make the storage path resemble production.
  Rejected on memory: nine Loki pods plus MinIO plus two memcached against
  ~4Gi available. It also introduces an object store to back up, for data
  plan §32 does not ask to be backed up.
- **`loki.source.kubernetes` instead of file tailing.** Tempting — it would
  remove the hostPath, the `runAsUser: 0` and the positions file in one move,
  taking L11 and L12 with it. Rejected because it keeps no positions across
  restarts, so every Alloy restart duplicates or drops a window of logs, and
  the collector restarts on every values change. A permanent low-grade lie in
  the data costs more than a readOnly host mount.
- **A `logging-config` Application mirroring `monitoring-config`.** Symmetric,
  and it would keep every logging artefact under one tree. Rejected because
  the split in `monitoring`/`monitoring-config` exists for a CRD boundary that
  does not exist here, and because it would put the ServiceMonitors somewhere
  other than the one directory that currently answers "what does Prometheus
  scrape?".
- **Loki's chart dashboards (`monitoring.dashboards.enabled`).** Eight
  ConfigMaps the chart maintains, picked up automatically by the sidecar,
  covering Loki's own read and write paths. Rejected to hold P7's line: this
  phase proves the pipe, and curating dashboards is a one-file commit
  afterwards.
- **Host journald and Kubernetes Events.** Journald would catch what pod logs
  structurally cannot — why k3s itself restarted — but needs `/var/log/journal`
  and a wider privilege grant. Events would survive their 1h etcd expiry.
  Both are real gaps; both are deliberately out of scope so this phase ships
  one pipeline that works rather than three that half do.

## 4. Scope

### 4.1 What this phase deliberately does not do

- **No host journald collection.** §3.1. Container logs only.
- **No Kubernetes Events pipeline.** §3.1.
- **No Ingress, no tailnet URL for Loki.** L17.
- **No dashboards.** L16, holding P7's line. Explore is the interface.
- **No alert delivery.** Alertmanager is still off (P3). The rule in §8.4
  turns red in the Prometheus UI and nowhere else, and says so in its header.
- **No backup of logs.** L18.
- **No Vault path, no credential of any kind.** `auth_enabled: false` means
  this is the first phase since 17 that adds nothing to
  `configure-vault.sh` — worth stating, because the absence looks like an
  omission otherwise.

### 4.2 Architecture

```text
Argo CD
├── logging               wave 23   ns logging
│     ├── loki chart 7.3.0        ← observability/logging/loki/values.yaml
│     └── config/                 ← Grafana datasource ConfigMap (ns monitoring)
├── logging-agent         wave 23   ns logging
│     └── alloy chart 1.12.1      ← observability/logging/alloy/values.yaml
└── monitoring-config     wave 24   (existing)
      └── targets/loki.yaml, targets/alloy.yaml, targets/loki-rules.yaml
```

Data path:

```text
containerd
   ↓ writes
/var/log/pods/<ns>_<pod>_<uid>/<container>/0.log      root:root, 0750
   ↓ hostPath /var/log, readOnly, read as uid 0
Alloy DaemonSet  (positions → hostPath /var/lib/alloy)
   ↓ HTTP push
loki.logging.svc.cluster.local:3100
   ↓ filesystem, TSDB v13, 31d
PVC storage-loki-0  (local-path → /srv/kubernetes/storage)
   ↑ query
Grafana datasource (ConfigMap in ns monitoring, grafana_datasource: "1")
```

### 4.3 Why two Applications, and why both at wave 23

Two Applications because they are two charts, independently versioned. Argo CD
renders one chart per Application, and pinning them separately is the point.

Both at **wave 23** — the same wave as `monitoring`, not behind it. The rule
this repository states in `redis.yaml` and `nexus.yaml` is that a wave is
stacked only for a real gate, never out of habit, because sharing a wave means
components reconcile in parallel and none blocks another. Applying that test:

- Loki needs no Vault secret. `auth_enabled: false`; there is no credential,
  so the wave-22 floor that `vso-config.yaml` establishes does not apply.
- Alloy needs no CRD. L13 turns off the only one its chart would install.
- Alloy does not gate on Loki. It tails, fails to push, retries with backoff,
  and reports Healthy either way — its readiness is not Loki's readiness.

None of those is a real gate, so both share wave 23.

The consequence matters more than the tidiness. `monitoring-config.yaml`
records as an invariant that **"NOTHING sits behind wave 24"**, with the
reason that observability watches the cluster and the cluster must never wait
on it. Putting logging at 25 and 26 would have broken that invariant and
required editing that comment. At 23 it survives this phase untouched, and the
two ServiceMonitors land at 24 — *above* the Services they select, which is
also the stricter ordering.

## 5. Memory and storage budget

| Component | Request | Limit | Expected steady state |
| --- | --- | --- | --- |
| Loki (SingleBinary) | 128Mi | 512Mi | 250–350Mi |
| Alloy (DaemonSet, 1 node) | 64Mi | 256Mi | 100–150Mi |
| **Total** | **192Mi** | **768Mi** | **350–500Mi** |

Against ~4Gi available with node limits already at 70%. It fits, and it is the
tightest budget of any phase so far — which is why L4 matters more than it
looks: a single chart default left alone (`chunksCache.allocatedMemory: 8192`)
asks for twice the headroom that exists.

Limits are memory-only, no CPU limit, matching every other `limits:` block in
this repository.

### 5.1 Storage, and the one place this phase is weaker than phase 22

10Gi on `local-path`, a directory under `/srv/kubernetes/storage`, 846G free —
so the PVC size is nominal rather than a quota, exactly as the Prometheus and
Vault values files already note for theirs.

For Prometheus, `values.yaml` says plainly that `retentionSize` "is the one
that protects the node", because retention by time alone does not bound disk
and disk pressure here evicts pods. **Loki has no `retentionSize` analogue.**
Its only bound is retention × ingest rate, and because `local-path` does not
enforce capacity, a pod logging in a loop is bounded by `/srv`, not by the
10Gi on the claim.

Three things stand in for the missing size cap:

1. **`limits_config` ingest caps**, stated explicitly rather than inherited:
   `ingestion_rate_mb`, `ingestion_burst_size_mb`, `per_stream_rate_limit`.
   These bound a catastrophe, not ordinary growth, and the README says so in
   those words.
2. **`max_global_streams_per_user` lowered from 5000 to 1000.** This is the
   real protection. Label-cardinality explosion is what kills a small Loki,
   and L14's four-label rule should be enforced by the server and not only by
   the collector's configuration.
3. **The `PrometheusRule` of §8.4**, on the PVC's own free space.

Logs are not backed up (L18). A lost volume costs 31 days of logs and nothing
else; the configuration is entirely in git.

## 6. The Loki values file

`observability/logging/loki/values.yaml`. Nine settings, each because a chart
default is actively wrong here. The header records chart 7.3.0, the Loki
version, and a pointer to this document — the convention every values file in
this repository follows.

**Topology (L3).** `deploymentMode: SingleBinary`, `singleBinary.replicas: 1`,
`read.replicas`, `write.replicas` and `backend.replicas` all explicitly `0`.
Not cosmetic: the chart's `templates/validate.yaml` fails the render with
"Cannot run scalable targets (backend, read, write) or distributed targets
without an object storage backend" if any is left at its default of 3.

**Caches and extras off (L4).** `chunksCache.enabled: false` (the 8 GiB one),
`resultsCache.enabled: false` (1 GiB), `gateway.enabled: false`,
`lokiCanary.enabled: false`, `test.enabled: false`. The chart refuses to
render `test` without the canary, so the two come off together. With the
gateway off, the Service to address is `loki` itself on 3100.

**`auth_enabled: false` (L5).** The chart default is `true`. Left alone, every
read and write needs an `X-Scope-OrgID` header, and the Grafana datasource
returns "no org id" on its first query — a failure that reads like a broken
datasource rather than a tenancy setting.

**`commonConfig.replication_factor: 1` (L6).** Default 3.

**`schemaConfig` (L7).** Mandatory; the chart fails the render without it and
tells you so. TSDB, schema `v13`, `index.period: 24h`,
`object_store: filesystem`. Its `from:` is the day it is first applied, and
the values header carries the warning that it is **never** edited afterwards:
a retroactive change makes every block written before it unreadable.

**Retention, three keys (L8).** `limits_config.retention_period: 744h` is the
one people set. Alone it does nothing, because the chart's default
`compactor: {}` means the compactor never runs retention at all. It also needs
`compactor.retention_enabled: true` and
`compactor.delete_request_store: filesystem`. §11 step 6 exists specifically
to catch this, by reading the rendered ConfigMap rather than trusting the
values file.

**`analytics.reporting_enabled: false` (L9).**

**Persistence (L10).** 10Gi `local-path`, and
`persistentVolumeClaimRetentionPolicy` set to `Retain` on both `whenDeleted`
and `whenScaled`. The chart defaults both to `Delete` and explains itself in a
comment in `single-binary/statefulset.yaml`: "Data on the singleBinary nodes
is easy to replace … we will rely on re-fetching data when needed." That is
true when chunks live in S3. Here the PVC is the only copy, so left at the
default a `kubectl scale --replicas=0` destroys 31 days of logs.

**The rules sidecar off (L19).** `sidecar.rules.enabled` defaults to `true`,
which injects a second container — `kiwigrid/k8s-sidecar` 2.5.0 with
`resources: {}` — into the Loki pod, watching for ruler-rule ConfigMaps that
this phase never ships. An unbounded sidecar sharing a pod with a limited
process is the defect `observability/monitoring/values.yaml` already documents
twice, for the Grafana sidecars and for `prometheusConfigReloader`: total pod
usage passes the budget while `kubectl top pod` still reports a plausible
figure. `ruler.enabled` is left alone — inside the single binary the ruler
target is inert without rules; it is the sidecar that costs.

**Resources (§5).** 128Mi request, 512Mi limit, memory-only, on
**`singleBinary.resources`** — `loki.resources` exists in the chart's values
and is *not* the key the StatefulSet reads.

## 7. The Alloy values file and the pipeline

`observability/logging/alloy/values.yaml`. `controller.type: daemonset` is the
chart default and is stated explicitly anyway; one node means one pod.

The chart's default RBAC is far wider than this pipeline needs: `rbac.rules`
ships cluster-wide `get/list/watch` on `configmaps`, `secrets`, `pods/log`,
`replicasets`, and six `monitoring.coreos.com` kinds, bound to a
ServiceAccount whose pod runs as `runAsUser: 0` (L20). This phase narrows it
— `rules` is overridden to `pods` and `namespaces` only, which is all
`discovery.kubernetes` and file tailing require; `clusterRules` is left at
its default, whose `nodes`/`nodes/pods`/`nodes/metrics` grants are harmless.
Five other defaults are overridden, three of them because they fail
*silently*:

**`securityContext.runAsUser: 0` (L11).** Measured: `/var/log/pods` is
`drwxr-x--- root:root`, and the chart sets `securityContext: {}`. Without
root the DaemonSet starts, passes its probes, reports Healthy, and collects
nothing. This is precisely the failure the `alloy` ServiceMonitor in §8 exists
to make visible.

**Positions off `/tmp/alloy` (L12).** The chart starts the container with
`--storage.path=/tmp/alloy` (`templates/containers/_agent.yaml`) and mounts no
volume there — the `volumeMounts` block carries only `config` and `varlog`.
That path holds `loki.source.file`'s positions, so on the container's writable
layer it does not survive pod recreation and Alloy replays every log file from
byte zero. Fixed with a hostPath `/var/lib/alloy` through
`controller.volumes.extra` and `alloy.mounts.extra`, with `storagePath`
pointed at it. On a single node that is real persistence.

**`crds.create: false` (L13).**

**`enableReporting: false` (L9)**, which adds `--disable-reporting`.

`mounts.varlog: true` supplies the readOnly `/var/log` hostPath.
`mounts.dockercontainers` stays **false**: this cluster runs
`containerd://2.3.4-k3s1.36` and has no `/var/lib/docker`. One `/var/log`
mount suffices — `/var/log/containers/*.log` are symlinks *into*
`/var/log/pods/…`, so both are inside it.

### 7.1 The pipeline

In `alloy.configMap.content`:

```text
discovery.kubernetes   role = "pod", field selector spec.nodeName = $NODE_NAME
        ↓
discovery.relabel      namespace / pod / container / app  →  __path__
        ↓
local.file_match       /var/log/pods/*<uid>/<container>/*.log
        ↓
loki.source.file       positions in /var/lib/alloy
        ↓
loki.process           stage.cri {}      ← parses containerd's log format
        ↓
loki.write             http://loki.logging.svc.cluster.local:3100/loki/api/v1/push
```

`NODE_NAME` comes from the downward API (`spec.nodeName`) through
`alloy.extraEnv`. On one node the field selector changes nothing; it costs six
lines and keeps the configuration correct if a second node ever appears.

`stage.cri {}` is not optional. containerd writes
`<RFC3339Nano> <stream> <F|P> <line>`, and without the stage every log line in
Loki carries that prefix as content and the ingest timestamp rather than the
emitted one.

### 7.2 Labels (L14)

Exactly four: `namespace`, `pod`, `container`, `app` (from
`app.kubernetes.io/name`). **Not the pod's labels wholesale.**

Every distinct label combination is a separate stream, a separate index entry
and a separate set of chunks. Relabelling pod labels in is the standard way
people make a small Loki fall over, and it is the one mistake here that cannot
be undone, because the cardinality is written into blocks already on disk.
Everything else about a pod stays searchable as log *content*, which is what
Loki is good at.

## 8. Integrations, datasource and rule

### 8.1 Integrations 6 and 7

Two files in `observability/monitoring/targets/`, matching the seven
ServiceMonitors already there:

| File | Selects | Port | Namespace |
| --- | --- | --- | --- |
| `loki.yaml` | Service `loki` | `http-metrics` (3100) | `logging` |
| `alloy.yaml` | Service `alloy` | `http-metrics` (12345) | `logging` |

Alloy needs no such exclusion: its chart renders a single Service.

Both live **in namespace `logging`** and omit `namespaceSelector` entirely,
matching every existing file in that directory: a ServiceMonitor's default
scope is its own namespace, so placing it beside what it scrapes is the whole
configuration. (`serviceMonitorNamespaceSelector: {}` on the Prometheus is a
different selector — it governs which *ServiceMonitors* Prometheus discovers,
not which *Services* a ServiceMonitor selects.)

**The Loki selector cannot be `app.kubernetes.io/name` plus `instance` alone.**
The chart renders three Services — `loki`, `loki-headless` and
`loki-memberlist` — and all three carry that identical label pair.
`loki-memberlist` exposes only `tcp`/7946 and so yields no target, but
**`loki-headless` exposes `http-metrics` on 3100 as well**, so the naive
selector scrapes the same pod twice under two `service` labels. The chart
signals the intent with `prometheus.io/service-monitor: "false"` on the
headless Service, and the ServiceMonitor honours it with a `matchExpressions`
`NotIn` requirement — which selects `loki` because a *missing* key satisfies
`NotIn`, and rejects `loki-headless` because its value matches.

The charts' own `monitoring.serviceMonitor.enabled` (Loki) and
`serviceMonitor.enabled` (Alloy) stay **off** (L16). A chart-rendered
ServiceMonitor is invisible to CI, which validates files in this repository
and does not render Helm. Loki's toggle is worse than merely invisible: it
carries `metricsInstance.enabled: true`, which emits a `MetricsInstance` from
the `monitoring.grafana.com` group — an operator this cluster does not run and
a CRD it does not have.

This is the same reasoning P15 applied to Argo CD's chart-native
`serviceMonitor` toggles, for a different reason, in the previous phase.

### 8.2 The Grafana datasource

`observability/logging/loki/config/grafana-datasource.yaml`: a ConfigMap
labelled `grafana_datasource: "1"`, pointing at
`http://loki.logging.svc.cluster.local:3100`, `isDefault: false` so Prometheus
remains the default.

**It must live in namespace `monitoring`, not `logging`.** Measured on the
live pod: `grafana-sc-dashboard` runs with `NAMESPACE=ALL`, but
`grafana-sc-datasources` has **no `NAMESPACE` environment variable at all**, so
it watches only its own namespace. A datasource ConfigMap in `logging` would
be silently ignored — Grafana healthy, sidecar healthy, no datasource.

It is delivered as a third source on the `logging` Application, the same
multi-source shape `monitoring.yaml` already uses for
`observability/monitoring/config`. Because the sidecar runs `METHOD=WATCH` it
is picked up live, with no Grafana restart — which matters, since P4 makes
every Grafana restart a ~4-minute cold start and a memory spike.

### 8.3 No Ingress (L17)

Loki has no authentication and its API includes a delete surface.
`infrastructure/networking/policy.hujson` is still a single
`{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant, so an Ingress would publish
that surface to every device on the tailnet. This is the argument
`grafana-ingress.yaml` already records for Prometheus, unchanged. Loki is
reached through the Grafana datasource, or by port-forward; the command goes
in `observability/logging/README.md`.

`grafana-ingress.yaml`'s header comment — which today says there is
deliberately no Ingress for Prometheus — is extended to name Loki for the same
reason, so the next person finds one statement rather than two half-stated
ones.

### 8.4 The PrometheusRule

`observability/monitoring/targets/loki-rules.yaml` — the first
`PrometheusRule` in this repository. One group, two rules on
`kubelet_volume_stats_available_bytes{persistentvolumeclaim="storage-loki-0"}`,
verified present in this Prometheus (§2): warning below 30% free, critical
below 15%.

Its header states plainly that **it will never notify anyone**. Alertmanager
is disabled (P3), so this turns red in the Prometheus UI and nowhere else.
That is consistent with what is already running — the stack keeps roughly 25
default rules evaluating with no delivery — and it is the substitute for the
`retentionSize` Loki does not have (§5.1).

CI declared `PrometheusRule` catalog-available in phase 22, so this needs no
CI change.

## 9. CI

**No changes to `.github/workflows/validate.yaml`.** Verified against the
workflow rather than assumed:

- `yamllint` already walks `observability`.
- Both new values files are named `values.yaml`, so the existing
  `-ignore-filename-pattern 'values\.yaml$'` exempts them. This is why
  `loki/` and `alloy/` are separate directories rather than two differently
  named values files in one: the filename rule keeps working with no new
  exemption and no maintenance, exactly as that rule's comment intends.
- Every kind this phase ships is already validated — `Application`,
  `Namespace`, `ConfigMap`, `ServiceMonitor` and `PrometheusRule`, the last
  two checked against the datreeio catalog in phase 22.

Validation is run with the workflow's **own** kubeconform invocation,
including the `grep -q 'Skipped: 0'` gate. A local `kubeconform` without the
catalog `-schema-location` silently skips every CRD and reports success while
checking nothing.

## 10. Files

New:

```text
environments/homelab/apps/logging.yaml                    wave 23, 3 sources
environments/homelab/apps/logging-agent.yaml              wave 23
observability/logging/README.md
observability/logging/loki/values.yaml                    loki 7.3.0
observability/logging/loki/config/grafana-datasource.yaml ConfigMap, ns monitoring
observability/logging/alloy/values.yaml                   alloy 1.12.1
observability/monitoring/targets/loki.yaml                integration 6
observability/monitoring/targets/alloy.yaml               integration 7
observability/monitoring/targets/loki-rules.yaml          first PrometheusRule
```

Modified:

```text
bootstrap/namespaces/namespaces.yaml                 + namespace logging
infrastructure/ingress/config/grafana-ingress.yaml   header names Loki too (§8.3)
README.md                                            directory table + wave narrative
docs/workstation-plan.md                             §27 rewritten with what was built
```

Removed:

```text
observability/logging/.gitkeep                       the directory now has contents
```

Not touched, and deliberately so: `platform/vault/configure-vault.sh` (§4.1,
no credential), `.github/workflows/validate.yaml` (§9),
`environments/homelab/apps/monitoring-config.yaml` (§4.3 — its invariant
survives).

## 11. Verification

Every claim below is a command with an expected output, not an impression.

1. **CI green on the real command.** The workflow's own kubeconform
   invocation, including `grep -q 'Skipped: 0'`, plus `yamllint --strict`.
2. **Both Applications Synced/Healthy**, and both pods Running in `logging`.
3. **Alloy can actually read the logs.** `kubectl -n logging logs ds/alloy`
   free of permission errors — the L11 check. A Healthy pod is not evidence
   here; this step is why.
4. **Integrations 6 and 7 UP.** `up{job="loki"}` and `up{job="alloy"}` both
   `1` from the Prometheus API through a port-forward.
5. **Logs from elsewhere arrive.** `{namespace="vault"}` in Grafana Explore
   returns lines. A namespace other than `logging` on purpose: self-logging
   would pass even if discovery were broken.
6. **Collection is cluster-wide.** `count(count by (namespace) (…))` over the
   label API returns roughly the cluster's namespace count, not 1.
7. **Retention is armed, not merely configured.**
   `kubectl -n logging get cm loki -o yaml` shows `retention_enabled: true`
   and `delete_request_store: filesystem` in the rendered config. L8's trap,
   checked against the ConfigMap rather than the values file.
8. **Loki survives a restart.** Record a log line's timestamp, delete the pod,
   wait for Ready, query that range and get the same lines back — the phase-22
   convention, on the phase-24 volume.
9. **Alloy survives a restart without replaying.** Delete the Alloy pod and
   confirm no duplicate burst for lines already ingested — the L12 check.
10. **The four labels, and only four.** The label API for the stream returns
    `namespace`, `pod`, `container`, `app` and nothing else (L14).
11. **Memory measured, not assumed.** `kubectl top pods -n logging` and
    `kubectl top nodes` recorded, compared against §5, and the README updated
    with the real figures — the phase-21 practice, repeated in 22.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| `chunksCache` default left in place | L4. 8 GiB from one key on a node with ~4Gi. Caught by §11 step 2, but the values file names the figure so it is never "restored" by someone tidying |
| Retention configured but never runs | L8. §11 step 7 reads the rendered ConfigMap, not the values file |
| `schemaConfig.from` edited later | L7. Warning in the values header; every block written before the edit becomes unreadable and there is no recovery |
| Alloy Healthy but collecting nothing | L11. §11 step 3 checks the logs; integration 7 (§8.1) makes it visible on the Targets page afterwards |
| Duplicate logs after an Alloy restart | L12. hostPath positions; §11 step 9 |
| A runaway pod fills `/srv` | §5.1. Ingest caps and `max_global_streams_per_user: 1000` bound catastrophe; the §8.4 rule bounds growth — and it only ever shows in the Prometheus UI, which its own header states |
| Label cardinality explosion | L14, enforced twice: four labels in the collector, 1000 global streams in the server |
| Loki scraped twice under two `service` labels | §8.1. `loki-headless` also exposes `http-metrics`; the selector excludes it by the chart's own `prometheus.io/service-monitor: "false"` label |
| Datasource ConfigMap in the wrong namespace | §8.2. Measured `NAMESPACE` absence on the datasources sidecar; it must be `monitoring` |
| Loki PVC destroyed by a scale-to-zero | L10. `Retain` on both retention-policy keys, against a chart default of `Delete` |
| Unbounded rules sidecar inside the Loki pod | L19. Disabled, not merely bounded — it watches for rules this phase never ships |
| Log volume lost | Accepted (L18). 31 days of logs; every byte of configuration is in git |
