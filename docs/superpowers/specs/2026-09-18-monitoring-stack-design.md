# Monitoring Stack — Design

Covers workstation-plan phases 22 (Prometheus) and 23 (Grafana), as a single
phase. The roadmap separates them in §25 and §26 but couples them in §38,
item 22: "Ajouter Prometheus et Grafana".

**They are merged here because Grafana is a subchart of kube-prometheus-stack.**
Splitting them across two phases means either installing the chart twice or
shipping a deliberately crippled release in phase 22 and editing it in phase
23. One release, one phase, one values file.

## 1. Goal

A Prometheus that scrapes this cluster and every workload already running in
it, and a Grafana that draws it — published at
`https://grafana.taildf6cd4.ts.net` behind an admin password generated inside
Vault, delivered by the Vault Secrets Operator, and never seen by a human.

Grafana holds no state. Its datasource and its dashboards arrive from git, and
a pod restart is a factory reset **by design** — §26 asks that "une partie
importante de la configuration devrait être déclarative et versionnée dans
GitHub", and a PVC would quietly make the cluster authoritative over git.

The phase is finished when node, pod and namespace metrics render in Grafana;
when Argo CD, Vault, Traefik, PostgreSQL and Redis all report **UP** on the
Prometheus Targets page; when Prometheus survives a pod restart with its
history intact; when Grafana survives one with its dashboards intact and its
admin password unchanged; and when memory has been **measured**, not assumed.

## 2. Measured starting state

Measured 2026-09-18 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 13 including `root`, all Synced/Healthy |
| k3s | `v1.36.4+k3s1` |
| containerd | `v2.3.4-k3s1.36` |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `ingress-config` 21, `vso-operator` 21, `vso-config` 22, `postgres` 23, `redis` 23, `registry` 23, `nexus` 23, `beacon` 24 |
| PVCs | 3 — `data-vault-0` 5Gi, `data-postgres-0` 10Gi, `nexus-data` 20Gi |
| **Memory available** | **~6Gi of 15.5Gi** (9.5Gi used); Nexus alone holds 1.27Gi |
| `/srv` free | 850G of 938G |
| Namespaces from `namespaces.yaml` | `cert-manager`, `vault`, `tailscale`, `vault-secrets-operator-system`, `databases`, `apps`, `artifacts` |
| Traefik | 708 restarts, and **not in the ingress path** — every tailnet URL uses `ingressClassName: tailscale` |
| Vault | Raft, single replica, chart HCL untouched, auto-unsealed in-cluster |
| Argo CD metrics Services | **Do not exist.** See §6.1 |
| `observability/` in CI | **Not validated by anything.** See §9 |

Memory is the binding constraint of this phase, as it was in phases 18 and 21.
§5 is the budget, and it is a budget to be checked against reality in §11, not
a promise.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| P1 | `kube-prometheus-stack` from `prometheus-community`, pinned to an exact chart version recorded in the values header | Same rule phase 18 and phase 21 applied: never a floating version on a component that holds data |
| P2 | Phases 22 and 23 in one release | Grafana is a subchart. §38 couples them anyway |
| P3 | **Alertmanager disabled** | §4.1. No notification destination exists |
| P4 | **Grafana stateless** — `emptyDir`, provisioning from git | §1. A PVC makes the cluster authoritative over git, which is the opposite of what §26 asks |
| P5 | Grafana admin credential from Vault via VSO: `homelab/grafana`, policy `vso-grafana-read`, role `vso-grafana`, ServiceAccount `grafana` in `monitoring` | The doctrine every other credential in this repository follows |
| P6 | **`kubeScheduler`, `kubeControllerManager`, `kubeProxy` and `kubeEtcd` scrapers disabled**, and their default rules with them | §7. On k3s these bind to `127.0.0.1` inside a single process |
| P7 | Built-in stack dashboards only; the Grafana sidecar's label discovery stays enabled | §8. This phase proves the pipe; curation is a one-file commit afterwards |
| P8 | Prometheus PVC **20Gi** `local-path`, retention **15d** capped at **12GiB**, scrape interval 30s | §5.1. Metrics history is explicitly not backed up (plan §32) |
| P9 | Grafana published on the tailnet; **Prometheus is not** | §8.2. Prometheus has no authentication and the tailnet policy is a single open `*` → `*` grant |
| P10 | `grafana-ingress.yaml` lives in `infrastructure/ingress/config` at **wave 21**, ahead of its own backend | The convention is absolute here; the argument is written in `argocd-ingress.yaml` |
| P11 | Two Applications: `monitoring` (wave 23) and `monitoring-config` (wave 24) | §4.2. The CRD boundary, exactly as `vso-operator`/`vso-config` |
| P12 | `observability/prometheus/` and `observability/grafana/` become **`observability/monitoring/`** | One Helm release cannot live in two directories. The `platform/artifactory/` → `platform/nexus/` move, repeated |
| P13 | `validate.yaml` gains `observability` in **both** jobs | §9. That tree is validated by nothing today |
| P14 | A dedicated PostgreSQL `exporter` login with `pg_monitor`, created by an idempotent Job | §6.4. Reusing the `postgres` superuser would hand read-everything to a metrics sidecar |
| P15 | **Argo CD's own `metrics.enabled` toggles are set; its chart-native `serviceMonitor` toggles are NOT** | §6.1. A ServiceMonitor emitted at wave -1 references a CRD that arrives at wave 23 |
| P16 | Vault metrics via `unauthenticated_metrics_access`, not a Prometheus token | §6.3 |
| P17 | Traefik lands last, as its own revertible commit | §6.5. Highest blast radius, lowest payoff of the four integrations |
| P18 | No backup path for metrics | Plan §32 lists what to back up and metrics history is not on it. Stated rather than left unsaid |

### 3.1 Rejected alternatives

- **kube-prometheus-stack with Alertmanager on a null receiver.** Running a
  router with nowhere to route is the same infrastructure-without-a-consumer
  the Nexus spec cut in its §2.1. The default rules still load and evaluate;
  only delivery is absent, and delivery has no destination.
- **The bare `prometheus` chart, no operator, no CRDs.** Lighter, and it would
  have kept `HelmChartConfig` out of CI's CRD path. Rejected because every
  future scrape target then becomes a hand-edited block in one central values
  file instead of a ServiceMonitor that ships beside the workload it scrapes.
- **Grafana with a PVC.** Rejected under P4. A middle option — PVC for SQLite,
  dashboards and alert rules still provisioned — was considered and rejected
  for the same reason: it leaves Grafana-managed alert rules as state that
  exists in no repository.
- **Dashboards fetched from grafana.com by ID at pod start.** A tiny diff, but
  a stateless pod that cannot reach the internet at restart comes up with no
  dashboards at all, and the content is not versioned here.

## 4. Scope

### 4.1 What this phase deliberately does not do

- **No Alertmanager.** P3.
- **No Loki.** That is phase 24.
- **No curated dashboards.** P7.
- **No k3s control-plane scraping.** P6, and §7 explains what it would cost.
- **No Nexus metrics.** Its Prometheus endpoint requires authentication and a
  CE capability check. Worth pulling only if someone wants it.
- **No backup of metrics.** P18.

### 4.2 Architecture

```text
Argo CD
├── monitoring            wave 23   ns monitoring
│   ├── kube-prometheus-stack chart  ($values → observability/monitoring/values.yaml)
│   │     ├── prometheus-operator + CRDs
│   │     ├── Prometheus  → PVC 20Gi → /srv/kubernetes/storage
│   │     ├── Grafana     → emptyDir, provisioned from ConfigMaps
│   │     ├── kube-state-metrics
│   │     └── node-exporter (DaemonSet, one node)
│   └── observability/monitoring/config/   VSO path for Secret grafana-admin
│
└── monitoring-config     wave 24   (every manifest carries an explicit namespace)
    ├── ServiceMonitor  argocd-metrics / argocd-server-metrics
    │                   / argocd-repo-server-metrics             ns argocd
    ├── ServiceMonitor  vault                                    ns vault
    ├── HelmChartConfig traefik + ServiceMonitor                 ns kube-system
    ├── postgres-exporter + role Job + VSO path + ServiceMonitor ns databases
    └── redis-exporter + ServiceMonitor                          ns databases

infrastructure/ingress/config/grafana-ingress.yaml    wave 21
    → dedicated Tailscale proxy, ProxyClass homelab
    → https://grafana.taildf6cd4.ts.net
```

### 4.3 Why two Applications, and why these waves

`monitoring` installs the CRDs. `monitoring-config` contains nothing but
custom resources that need them — ServiceMonitors — plus the exporters those
ServiceMonitors point at. A CR applied before its CRD is registered is a
failed sync, so the two cannot share a wave. This is the same split, for the
same reason, as `vso-operator` (21) → `vso-config` (22).

Wave 23 for `monitoring` puts it alongside `postgres`, `redis`, `registry` and
`nexus` rather than behind them. It depends on none of them, and — the point
`redis.yaml` and `nexus.yaml` both make — stacking a wave "out of habit" turns
an unrelated failure into a gate. Wave 23 is also at or above the wave-22
floor that `vso-config.yaml` establishes for anything depending on a human
having run `platform/vault/configure-vault.sh`, which Grafana's password does.

Wave 24 for `monitoring-config` puts it alongside `beacon`. **Nothing sits
behind wave 24**, so an unhealthy monitoring stack gates nothing at all. That
is the correct position for observability: it watches the cluster, and the
cluster must never wait on it.

## 5. Memory budget

| Component | Request | Limit | Expected steady state |
| --- | --- | --- | --- |
| Prometheus | 512Mi | 2Gi | 700Mi–1.2Gi at ~15 targets |
| Grafana | 128Mi | 256Mi | ~120Mi |
| kube-state-metrics | 64Mi | 128Mi | ~60Mi |
| node-exporter | 32Mi | 64Mi | ~30Mi |
| prometheus-operator | 64Mi | 128Mi | ~60Mi |
| postgres-exporter | 32Mi | 64Mi | ~25Mi |
| redis-exporter | 16Mi | 32Mi | ~15Mi |
| `ts-grafana` proxy | — (ProxyClass `homelab` bounds it) | — | ~25Mi |
| **Total** | **~850Mi** | **~2.7Gi** | **~1.0–1.6Gi** |

Against ~6Gi available with Nexus' 1.27Gi already counted. It fits.

The limit that actually matters is Prometheus' 2Gi ceiling. Exceed it and the
pod is OOMKilled and loses its write-ahead log — which is why P8 caps
retention by **size** as well as by time. A time limit alone does not bound
disk, and disk pressure on this node is what evicts pods.

### 5.1 Storage

20Gi on `local-path`, which is a directory under `/srv/kubernetes/storage` —
850G free, so the PVC size is nominal rather than a quota, exactly as
`platform/vault/values.yaml` notes for Vault. Retention is 15 days
`retentionSize: 12GiB`, deliberately well under 20Gi: Prometheus needs room
for the WAL and for compaction, and `retentionSize` governs blocks only.

Metrics are not backed up (P18). A lost Prometheus volume costs history and
nothing else; every dashboard, rule and datasource is reconstructed from git.

## 6. The five integrations

### 6.1 Argo CD — requires editing Argo CD's own release

The obvious assumption is that Argo CD needs no work because it already serves
Prometheus endpoints. **It does not.** Measured on 2026-09-18, the `argocd`
namespace holds exactly four Services — `argocd-applicationset-controller`,
`argocd-redis`, `argocd-repo-server`, `argocd-server` — and none of them is a
metrics Service. The argo-cd chart (10.5.0, verified) creates
`argocd-application-controller-metrics`, `argocd-server-metrics` and
`argocd-repo-server-metrics` only when
`controller.metrics.enabled`, `server.metrics.enabled` and
`repoServer.metrics.enabled` are set, and `bootstrap/argocd/values.yaml` — 83
lines — sets none of them.

So this integration edits the wave -1, self-managing Argo CD release. That is
the most consequential file this phase touches and the change must stay
minimal: three `metrics.enabled: true` toggles and nothing else.

**P15 is the trap worth stating loudly.** The same chart also offers
`metrics.serviceMonitor.enabled`. Setting it looks like the tidy option and is
a cold-rebuild failure: Argo CD syncs at wave **-1**, and the ServiceMonitor
CRD does not exist until wave **23**. Argo CD's own Application would fail to
sync on a rebuilt cluster, and Argo CD is the thing that installs everything
else. The ServiceMonitors therefore live in `monitoring-config` at wave 24,
where their CRD is already present, and the chart's toggles stay off.

### 6.2 Redis — the cheapest of the five

The exporter reads the password from the VSO-delivered `redis-credentials`
Secret in `databases`. Verified 2026-09-18: that Secret carries three keys —
`password`, `requirepass.conf` and `username`. A VSO `transformation.templates`
block **adds** templated keys rather than replacing the source keys, so the
bare `password` the exporter needs is present and no new Vault path, policy or
VaultStaticSecret is required.

### 6.3 Vault — an HCL override and a restart

Vault exposes metrics at `/v1/sys/metrics?format=prometheus`, which requires
authentication unless the listener is told otherwise. Two routes exist:

1. A Prometheus token with a `prometheus-metrics` ACL policy, delivered by VSO
   and renewed.
2. `telemetry { unauthenticated_metrics_access = true }` inside the listener,
   which makes that one endpoint readable without a token.

**P16 takes the second.** The first adds a token whose renewal is a new
failure mode, to protect an endpoint that exposes operational counters and
seal status — not secrets — on a ClusterIP reachable only from inside this
cluster. The trade is recorded here so it is a decision rather than an
oversight.

The cost is real: the hashicorp/vault chart renders the listener from
`server.ha.raft.config`, a single HCL string. Overriding it to add two stanzas
means **replicating the chart's default HCL verbatim** and owning it from then
on — a drifting copy of an upstream default is a maintenance liability, so the
values file must say which chart version the copy was taken from. The
implementation verifies the default against the pinned chart 0.34.1 rather
than reproducing it from memory.

Changing it restarts Vault, which reseals it. That is safe here and only here:
`platform/vault/config/unsealer.yaml` unseals it automatically from the
in-cluster key Secret. It is still a Vault restart, so it is its own commit.

### 6.4 PostgreSQL — a dedicated login, created by a Job

`postgres_exporter` needs a database login. Reusing the `postgres` superuser
already in Vault would be one line of work and would hand read-everything to a
metrics sidecar, so P14 creates a dedicated `exporter` login granted
`pg_monitor` — the role PostgreSQL provides for exactly this.

The mechanism follows the precedent this repository already set in
`platform/nexus/config/bootstrap-job.yaml`: an idempotent Job, keyed on the
role's existence rather than on a flag, so it is safe on every resync and on a
rebuild. Its password is generated by `platform/vault/configure-vault.sh` at
`homelab/postgres-exporter`, alongside the five credentials that script
already manages, and reaches the exporter through a VSO path in `databases`.

`configure-vault.sh` is run by a human. Anything at wave 22 or later already
inherits that dependency, per `vso-config.yaml`; this adds one more entry to
what that script must have created.

### 6.5 Traefik — last, and separable

Enabling `metrics.prometheus` means a `HelmChartConfig` in `kube-system`,
which makes k3s' helm-controller redeploy the Traefik it manages.

Stated plainly: of the five integrations this one has the **highest blast
radius and the lowest payoff**. Traefik has restarted 708 times on this node,
and it carries no tailnet traffic at all — every published URL here uses
`ingressClassName: tailscale` and reaches its backend through a Tailscale
proxy pod, never through Traefik. What it buys is edge metrics for a path
nothing currently takes.

It stays in scope because request rates and error codes become meaningful the
moment anything does use that path. It lands **last**, as its own commit,
touching no file the other four touch, so reverting it is a single revert.

## 7. Why the k3s control plane is not scraped

kube-prometheus-stack ships ServiceMonitors for `kube-scheduler`,
`kube-controller-manager`, `kube-proxy` and `etcd`, and enables them by
default. On a stock k3s node all four are goroutines inside one `k3s server`
process with their metrics bound to `127.0.0.1`. Left enabled, they are four
permanently red targets and a set of default alert rules that can never
evaluate — a monitoring system whose own dashboard is broken teaches you to
ignore it.

Enabling them requires `--kube-scheduler-arg=bind-address=0.0.0.0` and its
three siblings in the k3s systemd unit: a host-root edit, which on this host is
an operator hand-off rather than something Argo CD can apply. The value on a
single-node homelab — scheduler queue depth, controller work queues, etcd
latency on a cluster with one etcd member — does not justify it.

P6 disables the four scrapers **and** their rules. Verified against chart
91.4.1, the `defaultRules.rules` keys that must be set false are exactly
`etcd`, `kubeControllerManager`, `kubeProxy`, `kubeSchedulerAlerting` and
`kubeSchedulerRecording` — scheduler rules are split across two keys in this
chart, and there is no `kubernetesSystemScheduler` key. Disabling the scrapers
alone leaves rules that reference series nothing produces.

## 8. Grafana

### 8.1 State and provisioning

`persistence.enabled: false`, so `/var/lib/grafana` is an `emptyDir` and the
SQLite database is discarded on every restart. What survives is what git
provides:

- **The datasource** — Prometheus, set as default. The stack provisions it
  itself through the Grafana sidecar
  (`grafana.sidecar.datasources.defaultDatasourceEnabled`, on by default), so
  this phase writes no datasource manifest and only verifies the result.
- **The dashboards** — `defaultDashboardsEnabled: true` ships roughly 25 of
  them as ConfigMaps (nodes, pods, namespaces, workloads, kubelet, PV usage),
  which covers plan §25's "CPU / RAM / nodes / pods / Kubernetes" list
  outright.
- **The sidecar's label discovery** stays on, so a future dashboard is a
  ConfigMap labelled `grafana_dashboard: "1"` and nothing else.

A dashboard edited in the UI is lost on restart. That is P4 working, not a
defect, and `observability/monitoring/README.md` must say so in those words —
someone will lose an afternoon's work to it otherwise.

Alert rules are subject to the same rule: anything created in Grafana's UI
lives in the discarded SQLite database. Alerting that must survive is
provisioned from a file. This phase provisions none, because there is nothing
to route to (P3).

### 8.2 Publication

`https://grafana.taildf6cd4.ts.net`, on a dedicated Tailscale proxy with
ProxyClass `homelab`, from `infrastructure/ingress/config/grafana-ingress.yaml`
at wave 21 (P10). TLS terminates at the proxy with a real Let's Encrypt
certificate; the hop to the Grafana Service is plain HTTP inside the cluster,
as it is for Argo CD and Nexus.

Prometheus gets no Ingress (P9). It has no authentication whatsoever, and the
tailnet policy in `infrastructure/networking/policy.hujson` is currently a
single `{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant — publishing it would
expose its admin API to every device on the tailnet. `kubectl port-forward` is
the documented route to the Targets page, and the README carries the command.

## 9. CI, and the gate that will fail

`.github/workflows/validate.yaml` runs yamllint over
`bootstrap environments infrastructure platform .github` and kubeconform over
`bootstrap environments infrastructure platform`. **`observability` is in
neither list**, so everything this phase writes would land unvalidated. P13
adds it to both.

The harder half is kubeconform's `Skipped: 0` gate, which exists precisely so
that a missing CRD schema cannot turn the job green while checking nothing.
This phase introduces three new kinds:

| Kind | Group | Expected |
| --- | --- | --- |
| `ServiceMonitor` | `monitoring.coreos.com` | Present in the datreeio catalog |
| `PrometheusRule` | `monitoring.coreos.com` | Present in the datreeio catalog |
| `HelmChartConfig` | `helm.cattle.io` | Present — **verified 2026-09-18** |

All three were checked against the catalog on 2026-09-18 and all three return
HTTP 200, `helm.cattle.io/helmchartconfig_v1.json` included. It was the one
this design expected to be missing; it is not. **No `-ignore-filename-pattern`
exemption is needed and none should be added** — an exemption written "just in
case" would silently un-validate a real manifest later.

Local kubeconform is not evidence here. It skips CRDs silently unless given
the same two `-schema-location` flags, and that gap has already hidden a
Critical finding in this repository. Validation means running the workflow's
own command.

## 10. Files

New:

```text
observability/monitoring/values.yaml            kube-prometheus-stack values
observability/monitoring/config/vault-secrets.yaml   SA/Connection/Auth/StaticSecret for grafana-admin
observability/monitoring/targets/argocd.yaml         3 ServiceMonitors
observability/monitoring/targets/vault.yaml          1 ServiceMonitor
observability/monitoring/targets/traefik.yaml        HelmChartConfig + ServiceMonitor
observability/monitoring/targets/postgres-exporter.yaml  Deployment, Service, Job, VSO path, ServiceMonitor
observability/monitoring/targets/redis-exporter.yaml     Deployment, Service, ServiceMonitor
observability/monitoring/README.md
environments/homelab/apps/monitoring.yaml            wave 23
environments/homelab/apps/monitoring-config.yaml     wave 24
infrastructure/ingress/config/grafana-ingress.yaml   wave 21
```

Modified:

```text
bootstrap/namespaces/namespaces.yaml     + namespace monitoring
bootstrap/argocd/values.yaml             + 3 metrics.enabled toggles (§6.1)
platform/vault/values.yaml               + server.ha.raft.config HCL override (§6.3)
platform/vault/configure-vault.sh        + homelab/grafana + policy vso-grafana-read + role vso-grafana
                                         + homelab/postgres-exporter + policy vso-postgres-exporter-read
                                         + role vso-postgres-exporter
.github/workflows/validate.yaml          + observability in both jobs (§9)
docs/workstation-plan.md                 §25/§26 annotated with what was built
```

Removed:

```text
observability/prometheus/.gitkeep        P12
observability/grafana/.gitkeep           P12
```

`observability/logging/.gitkeep` stays; that is phase 24.

## 11. Verification

Every claim below is a command with an expected output, not an impression.

1. **Chart installed, CRDs registered.** `monitoring` Synced/Healthy;
   `kubectl get crd | grep monitoring.coreos.com` lists at least
   `servicemonitors`, `prometheusrules`, `prometheuses`.
2. **All targets UP.** From the Prometheus API through a port-forward:
   `/api/v1/targets?state=active` shows every job `up`, and **no** job for
   kube-scheduler, kube-controller-manager, kube-proxy or etcd exists at all.
3. **The five integrations individually.** One PromQL query each proving real
   series arrive: Argo CD (`argocd_app_info`), Vault (`vault_core_unsealed`),
   Traefik (`traefik_entrypoint_requests_total`), PostgreSQL
   (`pg_up`), Redis (`redis_up`).
4. **Prometheus survives a restart.** Record a sample's timestamp, delete the
   pod, wait for Ready, query the same range and confirm the pre-restart
   samples are still there.
5. **Grafana is stateless and provisioned.** Dashboards list non-empty and the
   datasource healthy; then delete the pod and confirm both return without
   intervention.
6. **The password was never seen.** `homelab/grafana` exists in Vault, the
   `grafana-admin` Secret exists in `monitoring`, the value appears in no file
   in this repository, and a login with it succeeds.
7. **Memory measured, not assumed.** `kubectl top pods -n monitoring` and
   `kubectl top nodes` recorded, compared against §5, and the README updated
   with the real figures — the phase-21 practice.
8. **CI is green on the real command.** The workflow's own kubeconform
   invocation, including `grep -q 'Skipped: 0'`.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| Argo CD's own release is edited at wave -1 | §6.1. Three toggles, no ServiceMonitor from that chart, its own commit, verified by Argo CD returning Healthy before anything else proceeds |
| Vault HCL override drifts from the chart default | §6.3. The values file records the chart version the copy was taken from; the implementation diffs against the pinned chart rather than reproducing from memory |
| Traefik redeploy destabilises `kube-system` | §6.5. Last, isolated, single-commit revert. Traefik carries no tailnet traffic, so the cost of a revert is nil |
| Prometheus OOMKilled, WAL lost | §5. 2Gi limit with retention capped by size; step 7 of §11 measures rather than trusts |
| `HelmChartConfig` schema missing, CI red | §9. Checked before manifests are written, path-scoped exemption with a written reason if absent |
| `configure-vault.sh` not re-run on a rebuild | Already the wave-22 floor documented in `vso-config.yaml`. This phase adds two entries to that script and the README names it |
| Metrics volume lost | Accepted (P18). History only; everything reconstructible lives in git |
