# Logging

Grafana Alloy shipping every container log to **Grafana Cloud Loki** —
workstation-plan phase 24. The in-cluster Loki was removed on 2026-10-05 to
free memory on this single node; its documentation is in git history before
that date. Original design:
`docs/superpowers/specs/2026-09-19-logging-stack-design.md`.

| Component | Chart | Version | Namespace | Wave |
| --- | --- | --- | --- | --- |
| Alloy | `grafana/alloy` | 1.12.1 (Alloy v1.19.2) | `logging` | 23 |

An Alloy DaemonSet tails `/var/log/pods`, keeps four labels, adds
`cluster="homelab"` and pushes to the stack's Loki endpoint with the
`grafana-cloud` Secret (`alloy/config/vault-secrets.yaml`) as basic auth.
Credential, free-tier budget and "what leaves the homelab" are documented
once, in `observability/monitoring/README.md`, "Grafana Cloud".

Not Promtail: that chart is deprecated in the Helm index and upstream
Promtail reached end of life on 2026-03-02.

## How to reach it

Grafana Cloud → Explore → the stack's Loki datasource, e.g.
`{cluster="homelab", namespace="vault"}`. Measured on 2026-10-05 the cluster
emits about 65MB of logs a day, around 2GB a month.

## Things that will surprise you

**Four labels, on purpose.** `namespace`, `pod`, `container`, `app`. Not the
pod's labels. Every combination is a separate stream, and Grafana Cloud
bills and limits by streams as well as bytes. Everything else about a pod is
searchable as log *content*. `stage.label_keep` in the pipeline is what
enforces it: `loki.source.file` and `stage.cri` attach `filename` (which
contains the pod UID) and `stream` after the relabel rules run.

**Alloy runs as root.** `/var/log/pods` is `drwxr-x--- root:root`. Without
`runAsUser: 0` the DaemonSet starts, passes its probes, reports Healthy and
collects nothing. A green pod is not evidence — and neither is
`up{job="alloy"}`, which is 1 whenever Alloy's HTTP server on :12345
answers. The real evidence is `loki_source_file_files_active_total > 0` and
a non-zero `rate(loki_write_sent_entries_total[10m])`, both in the metrics
allowlist and visible in Grafana Cloud.

**A bad credential is silent in Argo CD.** With a placeholder or revoked
token every push returns 401, Alloy retries with backoff, and the
Application stays Healthy. `loki_write_dropped_entries_total` rising is the
signal.

**A rotated token needs a restart.** Alloy reads it from environment
variables fixed at container start. The `grafana-cloud` VaultStaticSecret
names the DaemonSet in `rolloutRestartTargets`, so VSO restarts it.

## Files

| Path | What |
| --- | --- |
| `alloy/values.yaml` | Alloy chart values, including the whole pipeline and the `config-reloader` sidecar's memory bound |
| `alloy/config/vault-secrets.yaml` | VSO wiring for the `grafana-cloud` Secret in `logging` |
| `../monitoring/targets/alloy.yaml` | ServiceMonitor — integration 7 |
