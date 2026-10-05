# Monitoring — Prometheus and Grafana

`kube-prometheus-stack`, covering workstation-plan phases 22 (Prometheus) and
23 (Grafana), delivered as one Helm release because Grafana is a subchart of
the same chart — see decision P2. Design:
`docs/superpowers/specs/2026-09-18-monitoring-stack-design.md`. Verified and
measured on 2026-09-19; the figures in this file come from that pass, not
from the spec's budget.

Two Applications, split on the CRD boundary the way `vso-operator`/
`vso-config` already are (P11):

```text
Argo CD
├── monitoring            wave 23   ns monitoring
│   ├── kube-prometheus-stack chart  ($values -> observability/monitoring/values.yaml)
│   │     ├── prometheus-operator + CRDs
│   │     ├── Prometheus  -> PVC 20Gi (local-path) -> /srv/kubernetes/storage
│   │     ├── Grafana     -> emptyDir, provisioned from ConfigMaps
│   │     ├── kube-state-metrics
│   │     └── node-exporter (DaemonSet, one node)
│   └── observability/monitoring/config/   VSO path for Secret grafana-admin
│
└── monitoring-config     wave 24   (every manifest carries an explicit namespace)
    ├── ServiceMonitor  argocd-application-controller / -server / -repo-server   ns argocd
    ├── ServiceMonitor  vault                                          ns vault
    ├── HelmChartConfig traefik + ServiceMonitor                       ns kube-system
    ├── postgres-exporter + role Job + VSO path + ServiceMonitor       ns databases
    └── redis-exporter + ServiceMonitor                                ns databases
```

`monitoring` installs the CRDs `monitoring-config`'s ServiceMonitors need, so
it must land first; `monitoring-config` depends on nothing else and nothing
depends on it, which is why observability sits at the far end of the wave
order rather than gating anything.

| | |
| --- | --- |
| Chart | `kube-prometheus-stack` 91.4.1 (`prometheus-community`) |
| prometheus-operator | v0.94.0 |
| Grafana subchart | 13.2.5 |
| Prometheus UI | port-forward only — see below |
| Grafana UI | <https://grafana.taildf6cd4.ts.net> |

- `values.yaml` — the chart's Helm values, applied by the `monitoring`
  Application. **Not** a Kubernetes manifest.
- `config/` — the VaultStaticSecret path for Grafana's admin credential.
- `targets/` — every ServiceMonitor and its exporter, applied by
  `monitoring-config`. One file per integration, each independently
  revertible (see "Rolling back Traefik" below).

## A dashboard edited in the Grafana UI does not survive a restart

Decision P4: Grafana runs with `persistence.enabled: false`.
`/var/lib/grafana` is an `emptyDir`, and the SQLite database it holds —
dashboards, folders, anything clicked together in the UI — is deleted the
moment the pod is replaced. This is the design, not a defect: plan §26 asks
for configuration that is declarative and versioned in git, and a PVC would
quietly make the running cluster authoritative over git instead (spec §3,
rejected alternatives).

**Anything that must survive a restart has to exist as a file in this
repository.** A new dashboard is a ConfigMap labelled `grafana_dashboard:
"1"` — the sidecar's label discovery (`grafana.sidecar.dashboards`) picks it
up from any namespace with no other change. A dashboard built by hand in the
web UI and never exported to a ConfigMap is gone the next time the pod
restarts for any reason — an OOMKill, a node drain, a chart upgrade.

That restart is not free, either: bringing Grafana back cold takes roughly
**4 minutes** (measured on the Task 10 pod deletion) and re-provisions all 24
dashboards from ConfigMaps in one burst, which was the OOMKill this phase
found — see "Memory" below.

## Logging into Grafana

The admin credential lives in Vault and reaches the cluster as the
`grafana-admin` Secret via VSO (see `config/vault-secrets.yaml`). Read it
back with:

```bash
sg k3s-admin -c 'kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data.admin-password}"' | base64 -d
```

Username is `admin`.

### Keycloak SSO, phase 25

Grafana is the first OIDC client at
<https://grafana.taildf6cd4.ts.net>. The local admin form stays enabled as
the recovery route; Argo CD is deliberately not an OIDC client yet.
`values.yaml` supplies `grafana.grafana.ini.auth.generic_oauth`: client
`grafana`, scopes `openid profile email groups`, and Keycloak's `homelab`
endpoints. The browser-facing authorization endpoint uses tailnet HTTPS.
The token and userinfo back-channels use
`http://keycloak.keycloak.svc.cluster.local:8080`: CoreDNS does not resolve
the Tailscale MagicDNS name from Grafana's pod. `homelab-admins` maps to
organization Admin, everyone else to Viewer; `allow_assign_grafana_admin:
false` reserves server-admin access for the local account. CI parses the
values with `test_oauth_routing.py` to preserve this routing split.

`grafana.ini.server.root_url: https://grafana.taildf6cd4.ts.net` is
**required, not cosmetic**. Grafana constructs its OIDC callback from it;
without it, an `http://` redirect can be generated and rejected by
Keycloak. It must agree with the client's exact HTTPS callback
`https://grafana.taildf6cd4.ts.net/login/generic_oauth`.

`GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` comes from the `keycloak-grafana`
Secret via `envValueFrom.secretKeyRef`, never from the rendered ConfigMap.
The issuer-generated value needs the paste ceremony in
`platform/keycloak/README.md`; VSO refreshing the Secret alone does not
reload an environment variable, so restart Grafana after it lands.
`invalid_client` means to check this path; `user email is not found` means
the Keycloak user needs an email address. All `homelab` users enrol TOTP.

**`values.yaml` is excluded from kubeconform.** CI checks its YAML syntax
with yamllint, but `helm template` is the only rendering gate on this OIDC
edit. Before changing it, render both the ConfigMap and Deployment from
the pinned chart:

```bash
helm template monitoring prometheus-community/kube-prometheus-stack --version 91.4.1 -n monitoring -f observability/monitoring/values.yaml --show-only charts/grafana/templates/configmap.yaml --show-only charts/grafana/templates/deployment.yaml
```

Check `[auth.generic_oauth]`, the external `auth_url`, both internal
back-channel URLs and `[server]` `root_url`, then the environment variable's
Secret reference. The ConfigMap must not contain a `client_secret` value.

### Rotating the local admin password

Grafana keeps no copy of its admin password to go stale, for the same
reason a dashboard clicked together in the UI does not survive a restart:
decision P4 gives it no PVC. `admin.existingSecret: grafana-admin` (see
`values.yaml`) means the container reads this Secret fresh on every cold
start and re-applies it as the admin credential each time. Rotating the
value in Vault takes effect on Grafana's next restart, full stop; there is
no ordering trap and no stranded-admin recovery path.

## Reaching Prometheus — port-forward, not Ingress

There is deliberately no Ingress for Prometheus. It has no authentication of
any kind, and `infrastructure/networking/policy.hujson` is currently a
single `{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant — publishing it
would put its admin API in front of every device on the tailnet (spec P9).
`infrastructure/ingress/config/grafana-ingress.yaml` points here for the
command:

```bash
sg k3s-admin -c 'kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090'
```

Then `http://localhost:9090`. Grafana, which does have authentication, is
published on the tailnet instead (P9) — that asymmetry is deliberate, not an
oversight.

## Memory: measured against the budget

Taken with `kubectl top` on 2026-09-19, once all 18 targets were confirmed
`up` and immediately after the Task 10 restart tests — so Grafana's row
reflects a cold start, not idle steady state.

| Component | Spec §5 request | Spec §5 limit | Spec §5 expected steady state | Measured |
| --- | --- | --- | --- | --- |
| Prometheus | 512Mi | 2Gi | 700Mi–1.2Gi at ~15 targets | 624Mi at 18 targets |
| Grafana (grafana container) | 128Mi | 256Mi | ~120Mi | **252Mi** (98% of the pre-fix 256Mi limit — see below) |
| kube-state-metrics | 64Mi | 128Mi | ~60Mi | 30Mi |
| node-exporter | 32Mi | 64Mi | ~30Mi | 12Mi |
| prometheus-operator | 64Mi | 128Mi | ~60Mi | 33Mi |
| postgres-exporter | 32Mi | 64Mi | ~25Mi | 9Mi |
| redis-exporter | 16Mi | 32Mi | ~15Mi | 10Mi |

Stack total: roughly **1.1Gi** across all seven pods, against the spec's
~1.0–1.6Gi expected range — inside the estimate, with everything but Grafana
running well under it.

**Grafana was the one figure the spec got wrong, and it did so in a way that
actually mattered.** The `grafana` container's `lastState` showed
`reason=OOMKilled, exitCode=137`, and its two sidecars — `grafana-sc-dashboard`
and `grafana-sc-datasources` — had **no resources block at all** (chart
default `{}`), measured at 79Mi and 77Mi, unbounded. Both are now fixed in
`values.yaml`: `grafana.resources.limits.memory` raised to 512Mi, and
`grafana.sidecar.resources` set with a memory limit. The mechanism, recorded
in the values file: no PVC (P4) means every restart is a cold start that
rebuilds SQLite and re-provisions 24 dashboards in one burst, and that burst
is the memory peak — so statelessness guarantees a spike on exactly the
event a tight limit is most likely to kill it on. The cold-start cost itself
is not removed by the fix; only the OOMKill is.

Node total at measurement time: 1183m CPU (9%), 11391Mi memory (71%).
Allocated: requests 4092Mi (25%), limits 10602Mi (66%) — 4Gi still
available. Host: 15Gi total, 11Gi used. `/srv`: 938G total, 850G free.

**`local-path`'s StorageClass has `ALLOWVOLUMEEXPANSION: false`.** The 20Gi
Prometheus PVC can never be grown in place. Outgrowing it means recreating
the volume, which means losing history — `retentionSize: 12GiB` (P8) is
therefore the real, load-bearing bound on disk use, not the 20Gi request,
which spec §5.1 already calls nominal against 850G free.

## Scrape integrations

The table below records the original five integrations at the phase-22/23
measurement date. Phase 24 added Loki and Alloy as integrations 6 and 7;
phase 25 adds **Keycloak as integration 8**, in `targets/keycloak.yaml`.
Its ServiceMonitor scrapes `/metrics` on the Service's management port
9000 in namespace `keycloak`, not the application port 8080 exposed by the
Ingress. `monitoring-config` ships it at wave 24 and can apply before
Keycloak's Service exists; it acquires a target when the Service appears.
Check that target is UP in Prometheus. The new database NetworkPolicies
were accepted live on 2026-09-20 with `pg_up=1` and `redis_up=1`; preserving
the exporters' access and cross-namespace scrape ports is part of the fence.

Each proven live with one PromQL query against the port-forwarded Prometheus
above, 2026-09-19:

| Target | ServiceMonitor | Query | Result |
| --- | --- | --- | --- |
| PostgreSQL | `targets/postgres-exporter.yaml` | `pg_up` | `1` |
| Redis | `targets/redis-exporter.yaml` | `redis_up` | `1` |
| Vault | `targets/vault.yaml` | `vault_core_unsealed` | `1` |
| Traefik | `targets/traefik.yaml` | `traefik_config_reloads_total` | `3` |
| Argo CD | `targets/argocd.yaml` | `count(argocd_app_info)` | `15` |

All 18 active targets are `up`; there are no ghost jobs for
`kube-scheduler`, `kube-controller-manager`, `kube-proxy` or `etcd` — the
result decision P6 was written to guarantee. Prometheus was also restarted
mid-verification (pod deleted, recreated on the same PVC) and a query at the
pre-restart timestamp still returned data — the 20Gi `local-path` volume is
genuinely persisting history, not silently starting empty on every restart.

**All six CRD selectors are open, not just the original four.** `values.yaml`
sets `serviceMonitorSelectorNilUsesHelmValues: false`,
`serviceMonitorNamespaceSelector: {}`, `ruleSelectorNilUsesHelmValues: false`,
`podMonitorSelectorNilUsesHelmValues: false`,
`probeSelectorNilUsesHelmValues: false` and
`scrapeConfigSelectorNilUsesHelmValues: false` — every ServiceMonitor,
PodMonitor, PrometheusRule, Probe and ScrapeConfig in the cluster is picked
up regardless of label. The chart does expose the equivalent
nil-uses-helm-values toggle for `Probe` and `ScrapeConfig` (91.4.1's
`values.yaml`, lines 4714 and 4739, defaulting `true`); this repo does not
ship either kind of object today, but the toggles are set anyway so a future
one is discovered the same way a ServiceMonitor already is, rather than
falling back to the operator's default release-scoped label and being
silently ignored.

## What is deliberately absent

- **Alertmanager.** Decision P3 — no notification destination exists yet.
  The stack's ~30 default alert rules still load and evaluate in
  Prometheus; only delivery is missing. The Grafana datasource that would
  have pointed at it is explicitly disabled too (`values.yaml`,
  `grafana.sidecar.datasources.alertmanager.enabled: false`) — otherwise it
  provisions against a Service that does not exist.
- **The k3s control-plane scrapers** — `kubeScheduler`, `kubeControllerManager`,
  `kubeProxy`, `kubeEtcd`. Decision P6. On this single-node k3s cluster
  these run as goroutines inside one process bound to `127.0.0.1`; turning
  them on is a host-root systemd edit, an operator hand-off deliberately not
  taken here.
- **Curated dashboards.** Decision P7. The ~24 built-in dashboards the chart
  ships cover the CPU/RAM/nodes/pods/Kubernetes list plan §25 asked for.
  This phase proves the pipe; curation is a later, one-file commit.
- **Any backup of metrics.** Decision P18. Plan §32 lists what this homelab
  backs up, and metrics history is not on it. A lost Prometheus volume
  costs history and nothing else; everything else here is reconstructed
  from git.

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
