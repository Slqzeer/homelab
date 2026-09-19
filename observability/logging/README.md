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

Measured 2026-09-20 on `slqzeer-ms7c56`, after the restart tests.

| Figure | Value |
| --- | --- |
| Loki memory | 76Mi |
| Alloy memory | 62Mi (`alloy` 56Mi + `config-reloader` 6Mi) |
| `/var/loki` on disk | 4.6M |
| Node memory | 73% |
| Namespaces collected | 9 (of 14 cluster namespaces; the other 5 have no running pods, or (in `apps`) a pod that has logged nothing since Loki started) |

The spec's §5 budget was Loki 250-350Mi, Alloy 100-150Mi, roughly 350-500Mi
combined. The measured combined total is **138Mi** — roughly 2.5x below the
estimate. That is not a reason to shrink the budget: it is a reason for
whoever sizes the next component off that estimate to know it ran high.
Node memory limits sit at 75%.

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
NOW=$(date +%s)
curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="vault"}' \
  --data-urlencode "start=$((NOW-3600))000000000" \
  --data-urlencode "end=${NOW}000000000"
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

Checking this the obvious way is a trap: `/loki/api/v1/labels` gives a
**false failure**. It resolves label names against the index over a looser
range than any window you pass, so it keeps returning labels from older
streams alongside the current four — a correct configuration looks broken.
Use `/loki/api/v1/series` instead, which returns each stream's actual label
set for the window you ask about:

```bash
NOW=$(date +%s)
curl -sG http://127.0.0.1:3100/loki/api/v1/series \
  --data-urlencode 'match[]={namespace="vault"}' \
  --data-urlencode "start=$((NOW-3600))000000000" \
  --data-urlencode "end=${NOW}000000000"
```

This is not hypothetical: it made a correct configuration look broken
during this phase.

**The schema date is permanent.** `schemaConfig.configs[0].from` is the day
Loki was first applied. Editing it does not migrate anything — it makes every
block written before the new date unreadable. If a schema change is ever
needed, **append** an entry with a future date.

The first day's history is thin for the same reason, by design. On first
start Alloy tails existing log files from byte zero, so it replayed roughly
a week of historical lines — and Loki rejected and dropped every one of them
with `failed to create stream: no schema config found for time ...`, because
those lines predate `from:`. (Separately, the chart's default
`reject_old_samples_max_age: 168h` would have rejected anything older than 7
days regardless of the schema date — the two reasons overlap here.) This is
self-resolving — it stopped once the replay drained — and there is no way to
recover that history: backdating `from:` would make every block written
since unreadable, which is the trade-off the entry above already describes.

**Alloy runs as root.** `/var/log/pods` is `drwxr-x--- root:root`. Without
`runAsUser: 0` the DaemonSet starts, passes its probes, reports Healthy and
collects nothing. A green pod is not evidence — and neither is a green
`alloy` target on the Prometheus Targets page: `up{job="alloy"}` is 1
whenever Alloy's HTTP server on :12345 answers, which is entirely
independent of whether `/var/log/pods` is readable. Remove `runAsUser: 0`
and the Targets page stays green; the DaemonSet is still Healthy and still
collects nothing. The real evidence is `loki_source_file_files_active_total
> 0` (63 at last check) and a non-zero
`rate(loki_write_sent_entries_total[10m])` (~1.72/s at last check), both
exposed by that same scrape.

**Logs are not backed up.** Plan §32 does not list them. A lost PVC costs 31
days of logs and nothing else — every byte of configuration is in git.

## The volume alert pins `job="kubelet"`

`observability/monitoring/targets/loki-rules.yaml`'s two expressions both
pin `job="kubelet"` rather than matching on the PVC name alone: on k3s,
`kubelet_volume_stats_*` arrives twice, once from the kubelet and once from
the apiserver endpoint, and the apiserver copy carries a misleading
`namespace="default"`. The full argument, including the trade-off of pinning
the job, is in that file's header comment.

## Files

| Path | What |
| --- | --- |
| `loki/values.yaml` | Loki chart values |
| `loki/config/grafana-datasource.yaml` | The datasource ConfigMap — in `monitoring`, not here, because the Grafana datasources sidecar watches only its own namespace |
| `alloy/values.yaml` | Alloy chart values, including the whole pipeline and the `config-reloader` sidecar's memory bound |
| `../monitoring/targets/loki.yaml` | ServiceMonitor — integration 6 |
| `../monitoring/targets/alloy.yaml` | ServiceMonitor — integration 7 |
| `../monitoring/targets/loki-rules.yaml` | The volume alert |
