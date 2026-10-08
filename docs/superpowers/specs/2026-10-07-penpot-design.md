# Penpot design

Date: 2026-10-07
Status: draft for review

## Purpose

Add Penpot, the open-source design platform, to the homelab as a
contract-conformant personal application, together with its MCP server so an
AI agent can read and modify design files.

Success means: Penpot is reachable at
`https://penpot.taildf6cd4.ts.net`, signs users in through the homelab's
Keycloak, keeps its state in the cluster's existing PostgreSQL, and answers MCP
requests at the same hostname under `/mcp/stream`, authenticated by an MCP key.

## What Penpot is

Penpot is a multi-component application: a **frontend** (nginx serving the
editor and proxying API calls), a **backend** (the Clojure API server that owns
all business logic and runs database migrations), an **exporter** (renders
boards to SVG/PNG), and an **MCP server**. It requires PostgreSQL 15+ and a
Valkey/Redis instance.

We deploy the **upstream Helm chart**, pinned, rather than maintaining our own
manifests. That is the same choice this repository already makes for Vault, the
monitoring stack, the logging agent, the Tailscale operator and Argo CD. The
chart is a third-party package that keeps its own generic packaging, which the
onboarding contract explicitly allows.

Pinned release:

| Item | Value |
| --- | --- |
| Chart | `penpot` from `http://helm.penpot.app` |
| Chart version | `1.11.3` |
| App version | `2.18.3` |
| Source | <https://github.com/penpot/penpot-helm/releases/tag/penpot-1.11.3> |

Image digests resolved for `2.18.3`, recorded here for the runbook's release
record:

| Image | Digest |
| --- | --- |
| `penpotapp/frontend:2.18.3` | `sha256:bb8abe27d53de84c95597f2c02c0e702b2779971fb0703e543f9ecf183e999f6` |
| `penpotapp/backend:2.18.3` | `sha256:2df1b3440d2a82cc3571db211b4ffdfa2b89ccc910759e8d5e9387fb62971b5c` |
| `penpotapp/exporter:2.18.3` | `sha256:418232d6ca3120b1c2bfde298a56a05a1f41f567cd8494deac3fe7fbc186cfbd` |
| `penpotapp/mcp:2.18.3` | `sha256:5e811e6eeb179d80d8781fb0ffd2991560785d150b3676f1ac5e28d63ba9f7c2` |

### Images cannot be digest-pinned through chart values

The contract requires every image to carry a release tag **and** a digest.
Chart `1.11.3` renders `image: "{{ .Values.backend.image.repository }}:{{
.Values.backend.image.tag }}"` (`backend-deployment.yml:45`) with no digest
field anywhere in `values.yaml`, so digest pinning is not expressible through
chart values.

This is a **documented deviation**, consistent with existing practice in this
repository: no chart-managed component here pins image digests
(`grep -rn "sha256:" --include=values.yaml` returns nothing). Pinning is
enforced for hand-written manifests and for images this homelab's own CI
builds. Penpot's images are pinned transitively — the chart version is exact, and
it selects exact image tags. The runbook records this exception, its rationale,
and the digests above so a drifted tag is detectable by inspection.

## The MCP server

This is the part with the most design consequence, and the part where the
obvious approach is wrong.

### Penpot's MCP server ships with the chart

`config.flags` defaults to a string containing `enable-mcp`, and
`penpot.mcpEnabled` (`_helpers.tpl:155`) tests for exactly that token:

```
{{- define "penpot.mcpEnabled" -}}
{{- has "enable-mcp" (splitList " " (default "" .Values.config.flags)) -}}
{{- end -}}
```

So chart `1.11.3` already renders a `penpot-mcp` Deployment and a ClusterIP
Service from the official `penpotapp/mcp` image. **No custom image is built and
no product repository is needed.** The chart sets the container's environment
itself (`mcp-deployment.yml:52-67`): `PENPOT_MCP_SERVER_PORT`, the WebSocket
port, and `PENPOT_MCP_REMOTE_MODE=true` unconditionally — remote mode is what
disables the server's local-filesystem access, which is what we want on a shared
cluster. The same template feeds it `PENPOT_MCP_REDIS_URI` from the Redis
configuration, so multi-instance task routing comes for free.

### The MCP endpoint is reached through the frontend, not directly

The frontend Deployment is given the MCP service's URI
(`frontend-deployment.yml:60-64`), and the frontend's nginx proxies the MCP
routes to it: `/mcp/stream`, `/mcp/sse` and `/mcp/ws`. Clients therefore connect
to **the same hostname as Penpot itself**:

```
https://penpot.taildf6cd4.ts.net/mcp/stream?userToken=<MCP_KEY>
```

### Why there is deliberately no second Ingress

The obvious alternative — giving the MCP server its own tailnet Ingress on port
4401 — is **rejected on security grounds**, and it is worth recording why.

The MCP server has no authentication mechanism of its own. Penpot's
documentation is explicit that local-mode MCP uses "Auth: none (uses your
active Penpot browser session)": the server trusts whatever plugin is connected
to it. Exposing 4401 directly on the tailnet would publish an endpoint that can
read and modify the contents of your design files, with the design tool's full
write capability, to anything that can reach the tailnet. `PENPOT_MCP_REMOTE_MODE`
removes its filesystem access; it does not add authentication.

The frontend-proxied path is the only authenticated one. Authentication there is
the MCP key, presented as `userToken` and generated per user from Penpot's
Integrations page. Key-authenticated beats no-auth on the same tailnet, and one
hostname beats two.

The MCP server's ClusterIP Service stays cluster-internal. The NetworkPolicies
in §6 admit only the frontend on 4401/4402.

### The MCP key is a manual ceremony

The MCP key is generated in Penpot's UI, displayed exactly once, and is not
derivable — Vault cannot mint it. It is seeded into Vault at
`homelab/penpot`, key `mcp-key`, and handed to the AI client from there. Its expiry date must
be recorded in the runbook, the same lesson as the GHCR token's missing expiry
in `platform/registry/README.md`.

## Data

### PostgreSQL: shared, own role and database

Penpot's primary database is a `penpot` database owned by a `penpot` role inside
the existing PostgreSQL in `databases` (wave 23). The cluster runs PostgreSQL
18.6, comfortably above Penpot's recommended 15.

The arrangement mirrors `apps/tle-dev/config/postgres-job.yaml`: a `Sync` hook
Job in the `databases` namespace that creates the role and database if absent and
then sets the password unconditionally, so a rotated password reaches the role on
the next sync. Passwords travel through `psql` variables via `\getenv`, never
interpolated into SQL text or argv.

The chart is told about this database through
`config.postgresql.existingSecret` plus `secretKeys.passwordKey`, so the password
arrives by VSO projection rather than in `values.yaml`.

### Redis: shared, dedicated database index

The shared Redis in `databases` (wave 23) provides the cache and websocket
coordination. It runs with `requirepass` (see
`platform/databases/redis/config/redis.yaml:47`), so the chart cannot be given a
host and port alone — it needs a complete URI including the password. We
therefore set `config.redis.existingSecret` (`penpot-redis`) with
`secretKeys.redisUriKey`. A dedicated `VaultStaticSecret` reads `homelab/redis`
`password` and renders a single fully-formed
`redis://:<password>@redis.databases.svc.cluster.local:6379/3` value into Secret
`penpot-redis` through a templates transformation. It is a separate Secret
because two `VaultStaticSecret`s cannot own one destination. (Change directed
by the repository owner: the URI was originally a `homelab/penpot` key.) Database
index `3` is used so Penpot's keys cannot collide with any other consumer's.

**Risk, accepted with mitigation.** The shared Redis is configured with
`maxmemory 128mb` and `maxmemory-policy allkeys-lru`. Penpot's own guidance
recommends `volatile-lfu` and treats Redis as holding websocket coordination
state, not merely cache — under `allkeys-lru` those keys are evictable, and an
evicted coordination key degrades live collaboration rather than merely slowing
a cache. `maxmemory` and the policy are set cluster-wide and cannot be varied per
client. Mitigation: monitor for `evicted_keys` growth and for MCP/websocket
errors; if Penpot evicts under real use, the correct fix is a dedicated Valkey
instance for Penpot, not a change to the shared one. Recorded in the runbook's
known-gaps section.

### Assets: own PVC

`persistence.assets.enabled: true` provisions a `local-path` PVC for uploaded
images and SVG clips, with `config.objectsStorage.storageBackend: fs`. This is
the component's only volume.

### State classification

| Path | Class | Notes |
| --- | --- | --- |
| `penpot` database | durable | Primary state; restore via dump/restore |
| assets PVC | durable | Restore via filesystem copy |
| `apiSecretKey` | secret-derived | Re-issuable; invalidates sessions |
| MCP key | secret-derived | Re-issuable from Integrations; revokes old key |
| Redis contents | reconstructible | Cache and coordination only |
| OIDC client secret | secret-derived | Re-issuable via the registration Job |

## Sign-in: OIDC via Keycloak

Penpot supports a generic OIDC provider. We register a confidential Keycloak
client `penpot` in the `homelab` realm and enable `enable-login-with-oidc`.

Penpot's OIDC variables are set through the chart's `config.extraEnvs`, which
`backend-deployment.yml` injects:

| Variable | Value | Reached by |
| --- | --- | --- |
| `PENPOT_FLAGS` | includes `enable-login-with-oidc` | chart `config.flags` |
| `PENPOT_OIDC_CLIENT_ID` | `penpot` | non-secret, in `values.yaml` |
| `PENPOT_OIDC_CLIENT_SECRET` | from VSO | `values.yaml` via `extraEnvs` + `secretKeyRef` |
| `PENPOT_OIDC_BASE_URI` | `https://keycloak.taildf6cd4.ts.net/auth/realms/homelab/` | browser |
| `PENPOT_OIDC_AUTH_URI` | tailnet authorize endpoint | **browser** |
| `PENPOT_OIDC_TOKEN_URI` | `http://keycloak.keycloak.svc.cluster.local:.../token` | **backend** |
| `PENPOT_OIDC_USER_URI` | in-cluster userinfo | **backend** |
| `PENPOT_OIDC_JWKS_URI` | in-cluster JWKS | **backend** |
| `PENPOT_SSRF_ALLOWED_HOSTS` | both hostnames | backend |

Callback URI registered in Keycloak:
`https://penpot.taildf6cd4.ts.net/api/oauth/redirect`.

Keycloak is the only way in. Penpot 2.18.3 enables registration and password
login by default, so leaving those flags out does not turn them off: the flags
carry `disable-registration` and `disable-login-with-password` explicitly, plus
`enable-oidc-registration`, without which a first Keycloak login could not
create its Penpot account once general registration is off. There is no
password recovery route; while Keycloak is down, nobody signs in.

### The issuer-hostname risk

This is the least predictable part of the deployment and the one most likely to
need iteration.

The browser must be redirected to the **tailnet** authorization endpoint, but the
backend must reach Keycloak **in-cluster** — a pod cannot resolve
`keycloak.taildf6cd4.ts.net` through cluster DNS. Penpot's split-endpoint
variables exist for exactly this. The hazard is that Keycloak derives its token
`iss` claim from the host used to reach it, so an in-cluster `TOKEN_URI` can
produce an issuer that does not match the one advertised by the tailnet
discovery document, and ID token validation then fails.

Two candidate resolutions, to be tried in this order during implementation:

1. Keep every endpoint on the tailnet hostname and make that name resolvable
   from pods, so issuer and hostname agree. Least configuration, but requires a
   DNS or `extraHosts` change.
2. Split the endpoints as in the table above and accept explicit issuer
   validation, adjusting Keycloak's hostname configuration if the mismatch
   appears.

Penpot documents `PENPOT_SSRF_ALLOWED_HOSTS` as commonly required for exactly
this split, so the variable is set either way. **This is a live-test item, not a
paper design**; the runbook records which resolution was needed.

### Client registration

`platform/keycloak/config/client-registration.yaml` reconciles an explicit
allowlist of personal-application clients through a `PostSync` hook. `penpot`
joins that allowlist as a second client, with its secret supplied from a new
VSO projection `keycloak-penpot-client`. The existing script is generalised to
loop over allowlisted clients rather than hardcoding `homelab-portal`, preserving
its current guarantees: no secrets in argv, comparison that projects only owned
fields, separate client-scope mapping with verification, and abort on duplicate
or ambiguous client IDs.

The `keycloak-penpot-client` Secret is projected by the wave-25 `penpot`
Application, after the hook can first run, so the hook's reference to it is
`optional`: without it the hook reconciles the portal, skips `penpot` with a
warning and succeeds. The `keycloak` Application is re-synced after Penpot's
first sync to register the client.

## Sync wave

**Wave 25.** Penpot genuinely depends on:

- PostgreSQL at wave 23 (its primary database),
- Redis at wave 23 (cache and coordination),
- Keycloak at wave 24 (OIDC sign-in).

It joins `homelab-portal` and `tle-dev` at 25 rather than opening wave 26, and
nothing sits behind it, so it gates nothing. Its Ingress needs
`ingress-config` (21), already below it.

## Files

| Path | Contents |
| --- | --- |
| `bootstrap/namespaces/namespaces.yaml` | `penpot` namespace, `restricted` + v1.36 labels |
| `environments/homelab/apps/penpot.yaml` | Child Application: chart source, `$values` source, site-manifest source, wave 25, onboarding annotations, `state: durable` |
| `apps/penpot/values.yaml` | Chart values: `publicUri`, flags, shared PG/Redis `existingSecret`, assets PVC, OIDC env |
| `apps/penpot/config/kustomization.yaml` | Site manifests, deliberately **no** `namespace:` field |
| `apps/penpot/config/vault-secrets.yaml` | `ServiceAccount`, `VaultConnection`, `VaultAuth`, `VaultStaticSecret` |
| `apps/penpot/config/networkpolicy.yaml` | Default-deny both directions plus per-party flows |
| `apps/penpot/config/postgres-job.yaml` | `penpot` role + database in `databases`, `Sync` hook Job |
| `infrastructure/ingress/config/penpot-ingress.yaml` | Tailscale Ingress + portal publication annotations |
| `docs/runbooks/penpot-recovery.md` | Release record, ceremonies, backup/restore drill, rollback, known gaps |
| `platform/vault/configure-vault.sh` | Extended with `homelab/penpot` paths and `vso-penpot` roles/policies |
| `platform/keycloak/config/client-registration.yaml` | Extended allowlist with the `penpot` client |
| `README.md` | Updated layout table and wave narrative |

The Application uses three sources, following `platform/vault`:

1. `http://helm.penpot.app` chart `penpot` at `1.11.3`, `valueFiles: $values/apps/penpot/values.yaml`
2. `git@github.com:Slqzeer/homelab.git`, `ref: values`
3. `git@github.com:Slqzeer/homelab.git`, `path: apps/penpot/config`

`apps/penpot/config` is a separate kustomization because it holds objects in
three namespaces (`penpot`, `databases`, `keycloak`) and, like
`apps/tle-dev/config`, omits `namespace:` so kustomize does not rewrite the
explicit ones.

## Secrets and Vault paths

| Vault path | Keys | Consumer |
| --- | --- | --- |
| `homelab/penpot` | `postgres-username`, `postgres-password`, `api-secret-key`, `oidc-client-secret`, `mcp-key` | `penpot` namespace |
| `homelab/redis` | `password` (existing; rendered into `redis-uri` of Secret `penpot-redis`) | `penpot` namespace |
| `homelab/penpot/data` | `password` | the `databases`-namespace Job |

`postgres-username`, `postgres-password`, `api-secret-key` and `oidc-client-secret` are
generated by the extended `configure-vault.sh`. `mcp-key` is a **manual seed**,
because Penpot issues it.

Every `VaultStaticSecret` sets `excludeRaw: true` with `excludes: [".*"]` and
projects only named keys, matching the eleven existing destinations. Rotation
restarts consumers through `rolloutRestartTargets`, except the MCP key, which is
read by the client's configuration rather than by a pod — it needs no restart,
and regenerating it revokes the previous key by design.

## Network policy

Namespace-wide default-deny ingress and egress, then. Ports are the chart's
own defaults, read from `values.yaml` rather than assumed:

| Component | Service port |
| --- | --- |
| frontend | 8080 |
| backend | 6060 |
| exporter | 6061 |
| mcp | 4401 HTTP, 4402 WebSocket |

| Flow | Port | Rationale |
| --- | --- | --- |
| tailnet proxy → frontend | 8080 | the only user-facing route |
| frontend → backend | 6060 | nginx API proxy |
| frontend → MCP | 4401, 4402 | proxied MCP; never exposed directly |
| backend → PostgreSQL | 5432 | primary database |
| backend/exporter/MCP → Redis | 6379 | cache and coordination |
| backend → Keycloak | 8080 | OIDC token, userinfo, JWKS |
| all → DNS | 53 | resolution |
| backend/exporter → egress proxy | 3128 | provider and mail egress, allowlisted |
No scrape rule is written. Nothing in this namespace serves `/metrics`; see
Observability below for why the exporter's port 6061 is not one.

The frontend is the only workload the tailnet may reach, which is what makes the
"no second Ingress for MCP" decision enforceable rather than merely intended.

## Observability

**No component of Penpot 2.18.3 exposes a Prometheus endpoint, so this
component adds no ServiceMonitor.**

Chart `1.11.3` declares exactly one `http` port per component and no metrics
port anywhere — the only `metrics` keys in the chart are HPA resource specs.
Ports are frontend 8080, backend 6060, exporter 6061, MCP 4401/4402.

The exporter is the trap here: its name suggests it exports metrics, but it
does not. It is a headless-browser rendering service. Port 6061 serves
`POST /api/export` (Transit-encoded) and `/readyz`. Scraping it would produce a
permanently-down target in Grafana Cloud and teach an operator that a red target
is normal.

Penpot's issue tracker has an open question asking what `:6060/metrics`
represents, with no authoritative answer, which corroborates that no supported
metrics endpoint exists in this release.

Penpot's health is therefore judged by probe status and pod restart counts —
`kube_pod_container_status_restarts_total` and `kube_deployment_status_replicas_available`
are both already in the metric allowlist, so restarts and availability are
visible in Grafana Cloud without a single Penpot-owned series. The
`penpot-metrics` NetworkPolicy is deliberately **not** written: a policy admitting
a scrape that never happens is dead configuration that reads as if something is
being monitored.

## Portal publication

Published, as agreed. The Ingress carries the full annotation set required by the
contract:

```
portal.homelab.io/enabled: "true"
portal.homelab.io/name: Penpot
portal.homelab.io/description: Open-source design platform
portal.homelab.io/category: Design
portal.homelab.io/icon: penpot
portal.homelab.io/access: authenticated
portal.homelab.io/order: "60"
```

`order` continues past Keycloak's `50`, placing Penpot after the
infrastructure and identity entries.

`access: authenticated` is a deliberate departure from the precedent: all three
existing published Ingresses use `access: groups` with `homelab-admins`,
including Vault and Argo CD. Penpot is not an administrative UI — it is a
user-facing design tool holding real work, and its only sign-in path is
Keycloak — so gating it to the admin group would be wrong in kind rather than
merely stricter. It is deliberately **not** `public`, and it sets no
`portal.homelab.io/groups`, which the validator rejects for non-group access.

`icon: penpot` follows the lowercase product-slug convention of `keycloak`,
`argocd` and `grafana`, and `generic` is the established fallback. The portal's
icon set is not visible from this repository, so the implementation step confirms
the glyph renders and falls back to `generic` if it does not.

Publication is a separate, deliberate commit made **after** OIDC login and
denial have been verified live — the contract requires the ordering, and the
portal excludes invalid metadata rather than guessing.

## Risks

| Risk | Severity | Handling |
| --- | --- | --- |
| Keycloak issuer/hostname mismatch breaks OIDC | high | live test early; two documented resolutions; password path retained as recovery |
| Shared Redis evicts Penpot coordination keys | medium | monitor `evicted_keys`; dedicated Valkey if observed |
| Chart images not digest-pinnable | medium | documented deviation, digests recorded in runbook |
| MCP key expiry with no alarm | medium | record expiry in runbook at issuance |
| Enabling an agent with write access to design files | medium | read-only prompts first; documented in runbook |
| Single-node local-path is not a backup | known | dump/restore drill required before promotion |

## Acceptance

Before promotion:

1. `kubeconform -strict` clean and `yamllint` clean; rendered chart inspected for
   image tags.
2. All four Penpot Deployments `Available`; frontend serves the editor at
   `https://penpot.taildf6cd4.ts.net`.
3. OIDC login through Keycloak succeeds, and an unauthenticated request is
   denied.
4. Tailnet-only reachability confirmed; denied network flows observed.
5. MCP: enable in Integrations, generate key, seed Vault, connect an MCP client
   to `https://penpot.taildf6cd4.ts.net/mcp/stream?userToken=<key>`, connect the
   plugin from the file menu, and complete a read-only prompt end to end.
6. Secret rotation verified for the database password and the API secret key.
7. Resource use measured at idle and under load; requests and limits set from it.
8. Rollback to the previous chart version rehearsed.
9. `pg_dump` restore drill into a scratch database, plus an assets restore.
10. `python3 -m unittest -v tests.test_application_onboarding` passes against the
    rendered resources.

## Known gaps carried forward

- **No Penpot series reach Grafana Cloud.** No component of Penpot 2.18.3
  exposes a Prometheus endpoint, so no ServiceMonitor exists for it. Health is
  visible only through the cluster-level `kube_pod_*` and `kube_deployment_*`
  series already in the allowlist.
- The shared Redis eviction policy is not Penpot's recommended `volatile-lfu`.
- Chart-managed images are pinned by chart version and tag, not by digest.
- MCP write operations are powerful and unauthenticated at the plugin boundary;
  the MCP key is the only control, and it is per user.