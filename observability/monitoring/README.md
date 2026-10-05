# Monitoring — Prometheus agent to Grafana Cloud

`kube-prometheus-stack`, covering workstation-plan phases 22 and 23. Since
2026-10-05 nothing here stores or serves metrics: Prometheus runs in
**agent mode** (scrape and `remote_write` only) and ships an allowlisted
subset to **Grafana Cloud**, where dashboards, Explore and alerting live.
The Grafana subchart, the local TSDB and the in-cluster Loki were removed to
free about 1.1Gi of RAM on this single node. Original design:
`docs/superpowers/specs/2026-09-18-monitoring-stack-design.md`; the local
Grafana/Prometheus documentation is in git history before that date.

```text
Argo CD
├── monitoring            wave 23   ns monitoring
│   ├── kube-prometheus-stack chart  ($values -> observability/monitoring/values.yaml)
│   │     ├── prometheus-operator + CRDs
│   │     ├── PrometheusAgent  -> remote_write -> Grafana Cloud (allowlist)
│   │     ├── kube-state-metrics
│   │     └── node-exporter (DaemonSet, one node)
│   └── observability/monitoring/config/   VSO path for Secret grafana-cloud
│
└── monitoring-config     wave 24   (every manifest carries an explicit namespace)
    ├── ServiceMonitor  argocd-application-controller / -server / -repo-server   ns argocd
    ├── ServiceMonitor  vault, keycloak, alloy, tle-dev, traefik
    ├── postgres-exporter + role Job + VSO path + ServiceMonitor       ns databases
    └── redis-exporter + ServiceMonitor                                ns databases
```

Logs follow the same path through Alloy; see
`observability/logging/README.md`.

| | |
| --- | --- |
| Chart | `kube-prometheus-stack` 91.4.1 (`prometheus-community`) |
| prometheus-operator | v0.94.0 |
| Dashboards, Explore, alerting | Grafana Cloud stack, login with the grafana.com account |
| Cluster label | every series and log stream carries `cluster="homelab"` |

## Grafana Cloud

### Free-tier budget

The free tier caps active metric series (about 10k at the time of writing;
re-check the current limits). The old local Prometheus held **~47k** series,
so shipping everything would not fit. `values.yaml` therefore keeps an
**allowlist** of metric names on the `remote_write`; everything else is
scraped and dropped. Measured against the old TSDB on 2026-10-05, the
allowlist selects **~1.3k series**: per-container CPU/memory/network, pod
and workload state from kube-state-metrics, node CPU/memory/disk/pressure,
PVC usage, `up` for every target, and the health gauges of each exporter
(`pg_*`, `redis_*`, `vault_core_unsealed`, `argocd_app_info`, Keycloak,
Traefik, the portal, Alloy's log shipping, the agent's own remote-write
health).

The apiserver job (13.5k series alone) is **not scraped** at all
(`kubeApiServer.enabled: false`): an agent pays memory for every series it
scrapes, shipped or not.

To add a metric: check its cardinality first, then add its NAME to the
allowlist regex. Never widen the allowlist to a job or namespace wildcard —
it is the only thing between this cluster and the series cap. In Grafana
Cloud, *Administration → Cost management → Usage* shows the live count.

### Credential

Vault path `homelab/grafana-cloud`, three keys:

| Key | Value |
| --- | --- |
| `metrics-username` | the stack's Prometheus instance ID (grafana.com → stack → Prometheus → Details) |
| `logs-username` | the stack's Loki instance ID (stack → Loki → Details) |
| `token` | an access-policy token with `metrics:write` and `logs:write` scopes only |

`configure-vault.sh` seeds placeholders; the real values are pasted in once,
the same way as the GHCR token. VSO projects two Secrets named
`grafana-cloud`, one in `monitoring` (for the agent) and one in `logging`
(for Alloy), each carrying only its own `username` plus the token.

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh'
# inside vault-0 -- echo off, so the token never reaches the screen or history:
stty -echo; printf 'token: '; read T; stty echo; echo
printf '%s' "$T" > /tmp/t
vault kv put homelab/grafana-cloud metrics-username=<id> logs-username=<id> token=@/tmp/t
rm -f /tmp/t; unset T; exit
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

The agent picks a rotated token up on its next config reload; VSO restarts
the Alloy DaemonSet itself (`rolloutRestartTargets`).

### Checking that data arrives

Every Application reports Healthy even with a placeholder or revoked token,
so check the data, not Argo CD:

- In Grafana Cloud Explore: `up{cluster="homelab"}` returns one series per
  target, and `{cluster="homelab"}` in Loki returns recent logs.
- In the cluster: the agent's own counter
  `prometheus_remote_storage_samples_failed_total` stays flat. Its logs show
  `401` on a bad credential:
  `sg k3s-admin -c 'kubectl -n monitoring logs prom-agent-monitoring-kube-prometheus-prometheus-0 -c prometheus --tail=50'`.

### What leaves the homelab

Metric values and pod logs are sent to Grafana Labs. Logs are not filtered
for content; a service that logs a secret sends it off-site. Keep secrets out
of logs at the source, and drop a namespace in Alloy's pipeline if that ever
cannot be guaranteed.

### The scraper's pod labels changed

In agent mode the operator labels pods `app.kubernetes.io/name:
prometheus-agent` (not `prometheus`); `operator.prometheus.io/name` is
unchanged. The NetworkPolicies that admit the scraper by pod label
(`tle-dev-metrics`, `keycloak-metrics`) select the new value. A new policy
that admits the scraper must do the same.

## Scrape integrations

The table below records the original five integrations at the phase-22/23
measurement date. Phase 24 added Loki and Alloy as integrations 6 and 7
(Loki was removed with the move to Grafana Cloud);
phase 25 adds **Keycloak as integration 8**, in `targets/keycloak.yaml`.
Its ServiceMonitor scrapes `/metrics` on the Service's management port
9000 in namespace `keycloak`, not the application port 8080 exposed by the
Ingress. `monitoring-config` ships it at wave 24 and can apply before
Keycloak's Service exists; it acquires a target when the Service appears.
Check `up{job="keycloak"}` in Grafana Cloud. The new database NetworkPolicies
were accepted live on 2026-09-20 with `pg_up=1` and `redis_up=1`; preserving
the exporters' access and cross-namespace scrape ports is part of the fence.

Each proven live with one PromQL query against the then-local Prometheus,
2026-09-19. Every metric below except `traefik_config_reloads_total` is in
the Grafana Cloud allowlist:

| Target | ServiceMonitor | Query | Result |
| --- | --- | --- | --- |
| PostgreSQL | `targets/postgres-exporter.yaml` | `pg_up` | `1` |
| Redis | `targets/redis-exporter.yaml` | `redis_up` | `1` |
| Vault | `targets/vault.yaml` | `vault_core_unsealed` | `1` |
| Traefik | `targets/traefik.yaml` | `traefik_config_reloads_total` | `3` |
| Argo CD | `targets/argocd.yaml` | `count(argocd_app_info)` | `15` |

All 18 active targets are `up`; there are no ghost jobs for
`kube-scheduler`, `kube-controller-manager`, `kube-proxy` or `etcd` — the
result decision P6 was written to guarantee.

**All six CRD selectors are open, not just the original four.** `values.yaml`
sets `serviceMonitorSelectorNilUsesHelmValues: false`,
`serviceMonitorNamespaceSelector: {}`, `ruleSelectorNilUsesHelmValues: false`,
`podMonitorSelectorNilUsesHelmValues: false`,
`probeSelectorNilUsesHelmValues: false` and
`scrapeConfigSelectorNilUsesHelmValues: false` — every ServiceMonitor,
PodMonitor, Probe and ScrapeConfig in the cluster is picked
up regardless of label. The chart does expose the equivalent
nil-uses-helm-values toggle for `Probe` and `ScrapeConfig` (91.4.1's
`values.yaml`, lines 4714 and 4739, defaulting `true`); this repo does not
ship either kind of object today, but the toggles are set anyway so a future
one is discovered the same way a ServiceMonitor already is, rather than
falling back to the operator's default release-scoped label and being
silently ignored.

## What is deliberately absent

- **Alertmanager and rules.** An agent evaluates no rules, so
  `defaultRules.create: false` and Alertmanager stays off. Alerting, when it
  is set up, is configured in Grafana Cloud against the shipped series.
- **The k3s control-plane scrapers** — `kubeScheduler`, `kubeControllerManager`,
  `kubeProxy`, `kubeEtcd`. Decision P6. On this single-node k3s cluster
  these run as goroutines inside one process bound to `127.0.0.1`; turning
  them on is a host-root systemd edit, an operator hand-off deliberately not
  taken here.
- **Local dashboards and history.** Grafana Cloud's Kubernetes Monitoring
  app provides the dashboards; history is Grafana Cloud's retention, not a
  local volume.

## Re-syncing `monitoring-config` — a hook-only edit does not trigger a sync

Editing only the `postgres-exporter-role` Job inside `postgres-exporter.yaml`
does **not** turn the `monitoring-config` Application `OutOfSync`. Argo CD
excludes `hook: true` resources from the sync-status diff it uses to decide
whether an Application has drifted, so a changed hook script sits there,
already applied to git, with the cluster never told to look at it again.
Nothing reports this as a problem: `kubectl -n argocd get application
monitoring-config` keeps reading `Synced`/`Healthy` throughout.

Forcing the hook to re-run needs an actual sync operation, not a `kubectl
apply` and not a status check. The command that looks right does not work
here:

```bash
kubectl -n argocd exec deploy/argocd-server -- argocd app sync monitoring-config --core
```

**This fails on this cluster** — `argocd-server`'s own ServiceAccount cannot
list `services` in the `argocd` namespace, which `--core` mode needs to talk
to the API server directly. What works instead is patching the
Application's `operation` field directly:

```bash
sg k3s-admin -c 'kubectl -n argocd patch application monitoring-config --type merge -p "{\"operation\":{\"sync\":{\"syncStrategy\":{\"hook\":{}}}}}"'
```

Then confirm the Job actually re-ran by its `creationTimestamp`, not by the
Application's status: an Application can report a successful sync while
the hook Job it should have replaced is still the old one. This cost a real debugging session on this phase; anyone
who needs to re-run `postgres-exporter-role` after editing it should start
from the patch above, not from `argocd app sync`.

## Rolling back Traefik

Traefik carries the highest blast radius and the lowest payoff of the five
integrations, and it is isolated on purpose: the entire change is one file,
`targets/traefik.yaml`, added in commit `546d7eb` ("Scrape Traefik, in one
revertible commit") with a comment-only correction on top in `758188a`
("Correct how Traefik's metrics port is actually exposed"). Traefik carries
no tailnet traffic today — every published hostname in this repository uses
`ingressClassName: tailscale` and reaches its backend through a dedicated
Tailscale proxy pod, never through Traefik — so reverting it costs nothing
currently in flight.

To roll back, revert both commits (newest first, so there is nothing left
for the second revert to conflict with) and hand the push to the operator:

```bash
git revert 758188a 546d7eb
git push origin main
```

Argo CD's `monitoring-config` Application prunes on sync (`syncPolicy.
automated.prune: true`), so the resulting sync deletes the `HelmChartConfig`
and its ServiceMonitor, and k3s' `helm-controller` redeploys Traefik back to
its unmodified state.
