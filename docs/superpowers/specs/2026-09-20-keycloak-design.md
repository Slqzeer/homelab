# Keycloak and Centralized Authentication — Design

Covers workstation-plan phase 25 (§28, "Keycloak et authentification
centralisée"), which asks for a central identity provider so that every
person has one account and every compatible application delegates its login:

```text
Utilisateur → Keycloak → clients OIDC → applications
```

Phases 16–17 built the secret plane (Vault, VSO), phase 18 the database,
phases 22–24 the observability stack. This phase builds the identity plane
and proves it against one existing consumer.

## 1. Goal

A Keycloak that owns a `homelab` realm, stores its state in the PostgreSQL
from phase 18, is reached only over the tailnet, and takes every credential
it holds from Vault — plus **one real application logging in through it**.

The consumer is **Grafana** (K1). It is the safest first client in this
cluster: it keeps its Vault-held admin form as a fallback, it has no PVC so
there is no user data to migrate, and its OIDC configuration is a block in a
`values.yaml` this repository already owns. This repository has a canary
habit — `beacon` for the image chain, `vault-canary` for the secret chain —
and an OIDC provider with no client that ever logs in proves exactly nothing.
That is this repo's own "Synced/Healthy proves only what it applied"
principle applied to identity.

The phase is finished when a human in `homelab-admins` logs into Grafana
through Keycloak and lands with the Grafana **Admin** role; when a human in
`homelab-users` lands as **Viewer**; when Grafana's local admin form still
works afterwards; when `keycloak` reports **UP** on the Prometheus Targets
page as integration 8; when Keycloak survives a pod restart with its realm
intact (proving the state really lives in PostgreSQL); when a throwaway pod
is **refused** by the new NetworkPolicy while every legitimate client still
connects; and when Keycloak's memory has been **measured**, not assumed.

**Acceptance status, corrected 2026-09-20:** Admin/Viewer OIDC logins,
temporary-password replacement, TOTP enrolment/challenge, and local
Grafana login remain pending explicit browser verification. Earlier
operator-reported completion did not establish those checks: the database
dump at 2026-09-20 15:34:32 +0200 contained zero `homelab` users and one
`master` user. The operator must repair `UPDATE_PASSWORD` in the existing
realm and rerun the browser checks in `platform/keycloak/README.md`.

## 2. Measured starting state

Measured 2026-09-20 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 17 including `root` |
| Namespaces | 14 |
| k3s | `v1.36.4+k3s1`, single node, control-plane |
| Container runtime | `containerd://2.3.4-k3s1.36` |
| **Memory** | **11Gi used of 15Gi — 3.9Gi available** |
| **Swap** | **511Mi total, 511Mi used — fully exhausted** |
| Node limits already committed | **75%** (12074Mi) |
| Node requests already committed | 28% (4476Mi) |
| PostgreSQL | 18.6, one StatefulSet in `databases`, superuser only |
| NetworkPolicy enforcement | **active** — k3s runs with no `--disable-network-policy` |
| Existing NetworkPolicies | 4, all in `argocd`, all shipped by its chart |
| `serviceMonitorSelector` | `{}` |
| `serviceMonitorNamespaceSelector` | `{}` |
| Namespace labels | `kubernetes.io/metadata.name` present on every namespace |
| Latest Keycloak | **26.7.4**, released 2026-09-16 (checked against quay.io) |
| Latest keycloak-config-cli | 6.5.1, released 2026-05-22, newest build targets Keycloak **26.5.5** |

Two of these decide design choices below rather than merely describing the
host. The memory line is why §5 exists and why the plan carries a
measurement task. The `serviceMonitorSelector: {}` pair is why integration 8
needs no change to `observability/monitoring/values.yaml` at all.

The "Existing NetworkPolicies" line also corrects a factual error in the
root README, whose Known-gaps entry reads "**No `NetworkPolicy` anywhere,
including `databases`**". There are four; they are chart-shipped and cover
Argo CD's own components. The gap is real for `databases` and for every
other namespace, and the wording needs fixing regardless of this phase.

## 3. Decisions

| # | Decision |
| --- | --- |
| K1 | Grafana is the proving OIDC client. Argo CD is deliberately **not** — a bad `oidc.config` locks you out of the tool that reconciles the fix |
| K2 | Plain manifests, not a chart and not the Keycloak Operator |
| K3 | `quay.io/keycloak/keycloak:26.7.4`, pinned by tag |
| K4 | **No PVC.** All state lives in PostgreSQL; the pod is disposable |
| K5 | Realm managed as JSON in git, applied by `--import-realm` — first start only. Drift is documented, not reconciled |
| K6 | New `keycloak` namespace, declared in `bootstrap/namespaces/namespaces.yaml` at wave 0 |
| K7 | Sync-wave **24**, because the database it needs is at 23 |
| K8 | `homelab/keycloak-grafana` is seeded with a **random placeholder** by `configure-vault.sh`, and overwritten by hand with Keycloak's generated secret |
| K9 | The `keycloak` PostgreSQL role and database are created by an Argo CD **Sync-hook Job** in `databases`, copying the phase-22 exporter Job |
| K10 | Two Vault roles, not one bound to two namespaces — `bound_service_account_names` × `bound_service_account_namespaces` is a cross-product |
| K11 | `master` realm holds one break-glass admin and nothing else; humans live in `homelab`; **no users in git** |
| K12 | `CONFIGURE_TOTP` is enabled/default realm-wide; `UPDATE_PASSWORD` is explicitly enabled/non-default for temporary passwords |
| K13 | `resetPasswordAllowed: false` — there is no SMTP in this homelab |
| K14 | `kc.sh start`, not `--optimized`: no derived image is built for one Deployment |
| K15 | Default-deny ingress on `databases`, with four explicit allow-rules. Ingress-only; egress and DNS untouched |
| K16 | Grafana's local login form stays enabled |
| K17 | No `excludeRaw` regression: every new `VaultStaticSecret` carries `excludeRaw: true` |

### 3.1 Rejected alternatives

**keycloak-config-cli reconciling the realm on every sync** (the pattern
`platform/nexus/config/bootstrap-job.yaml` uses for Nexus). This is the
option that would have made git authoritative over identity rather than
merely reproducible, and it was rejected on dependency risk, not on
principle: the tool's last release is 2026-05-22 and its newest image
targets Keycloak 26.5.5 against our 26.7.4 — two minors behind, from a
project shipping one build per Keycloak version. Phase 24 was already
forced to abandon Promtail for exactly this decay pattern, four months after
its last release. Adopting a second lagging dependency to manage the
cluster's identity is a worse trade than documenting drift. Revisit when the
tool catches up; §11 records what that would change.

**The Keycloak Operator.** A CRD and a controller, permanently resident, to
manage a single Deployment. On a host at 75% committed limits with swap
exhausted, the controller's own footprint is the whole cost and none of the
benefit — there is no fleet of Keycloaks to reconcile.

**Bitnami's Keycloak chart.** Same family phase 18 rejected for PostgreSQL:
it renders an unpinned tag, and an unpinned tag on a stateful service can
change the major version across a restart.

**A realm JSON carrying the client secret through an `${env.*}`
placeholder.** This would have made Vault the single source of truth with no
hand-run step. It depends on Keycloak's property-substitution behaviour
inside realm imports, which was **not verified** against 26.7 — and the
failure mode if substitution does not happen is that the literal string
becomes the client secret, surfacing as a silent `invalid_client` at login
with nothing naming the cause. K8 takes the verified route instead.

**A public client with PKCE and no secret at all.** Deletes the secret
problem, but Grafana's `generic_oauth` is built around a confidential
client, and a browser-deliverable client in front of an admin-capable
dashboard is a weaker posture than a confidential one — on a tailnet whose
`infrastructure/networking/policy.hujson` is still a single
`{"src": ["*"], "dst": ["*"]}` grant.

**A single realm, using `master` for everything.** `master`'s admins govern
every realm, so there is no blast-radius separation and the roadmap's
"realm dédié au homelab" requirement goes unmet.

## 4. Scope

### 4.1 What this phase deliberately does not do

- **No Argo CD OIDC.** K1. A later phase, with the bootstrap script as its
  documented escape hatch.
- **No Nexus OIDC.** Nexus Repository CE does not offer OIDC; SAML and SSO
  are paid features. Nexus keeps its Vault-held local admin.
- **No SMTP.** Without it there is no email verification, no password reset
  and no admin notification. K13 turns the affected feature off rather than
  leaving a form that silently does nothing.
- **No user federation, no LDAP, no social identity providers.**
- **No Alertmanager.** Still disabled since phase 22; any rule added here
  would go red in the Prometheus UI and nowhere else. This phase adds no
  PrometheusRule.
- **No NetworkPolicy outside `databases`.** K15 fences the database this
  phase becomes the first application consumer of. Fencing `keycloak` itself
  needs the Tailscale proxy pod's live labels confirmed first, and that is a
  larger, separate change.
- **No scheduled backups.** Manual, like phase 18. Roadmap §34 owns
  automation.

### 4.2 Architecture

```text
tailnet
   │  https://keycloak.taildf6cd4.ts.net
   ▼
ts-keycloak proxy (ProxyClass homelab)
   │  HTTP, X-Forwarded-*
   ▼
keycloak ns ──── Deployment keycloak :8080   (realm import from ConfigMap)
   │                              :9000      (health + metrics)
   │                                │
   │ 5432                           └────► ServiceMonitor (integration 8)
   ▼
databases ns ─── postgres-0  (database `keycloak`, role `keycloak`)
                     ▲
                     │ Sync-hook Job creates role + database
                     │
Vault ──► VSO ──► keycloak-db / keycloak Secrets
  │
  └─────► VSO ──► keycloak-grafana Secret ──► monitoring ns ──► Grafana
                                                                   │
                                        OIDC authorization code ───┘
```

### 4.3 Why wave 24, and the deadlock the placeholder prevents

Keycloak depends on the VSO operator (21), on Vault's configure ceremony,
and on PostgreSQL (23). The root README's rule is that a component
depending on something at wave 23 sits above it, so Keycloak joins `beacon`
and `monitoring-config` at **24** — each of the three there for its own
real dependency.

That placement creates a trap that would have fired on the next rebuild,
and K8 exists solely to defuse it.

Grafana is at wave 23 and now needs the OIDC client secret. A Secret
referenced by `envValueFrom` that does not exist leaves the pod in
`CreateContainerConfigError`; the Deployment never goes Healthy, so
`monitoring` never goes Healthy, so **nothing at wave 24 syncs — so Keycloak
never starts, so the realm is never imported, so the client secret never
comes into existence.** Grafana waits on a value only Keycloak can produce
and Keycloak waits on Grafana. Argo CD would report the cluster stuck at
wave 23 with nothing anywhere naming identity as the cause.

K8 breaks it with one block in `configure-vault.sh`, using the same
generate-if-absent idiom as every other credential there: `homelab/
keycloak-grafana` always holds *something*, so the derived Secret always
exists and Grafana always reaches Healthy. The ceremony in §8 then
overwrites the placeholder with the real value. The failure mode degrades
from a rebuild deadlock to "the Grafana SSO button returns `invalid_client`
until the paste happens" — with Grafana's admin form working throughout
(K16).

One Application, resources in two namespaces. `environments/homelab/apps/
keycloak.yaml` names `keycloak` as its destination, but
`config/database-job.yaml` writes into `databases`. That is an established
pattern here, not a new one: `monitoring-config` declares `monitoring` and
writes into `argocd`, `vault`, `kube-system` and `databases`, because
`destination.namespace` is only the default for a manifest that omits one.
Every manifest in `platform/keycloak/config/` states its own namespace
explicitly, for the same reason.

The direction of that paste is forced, and is not a preference. Keycloak
generates the client secret at import; the admin console offers
**Regenerate**, not a field to paste an arbitrary value into. So Vault
cannot be the origin without the REST API. Reading from Keycloak and pasting
into Vault is the same idiom the root README's first-install step 10 already
uses for the GHCR token: the issuer is not Vault, so a human moves it once.

## 5. Memory budget

The node carries **12074Mi of limits (75%)** against 15Gi, with swap fully
exhausted. This host has a recorded history of loss under memory pressure —
a CoreDNS outage from an unbounded Tailscale proxy, probe timeouts in
troubleshooting entries 4 and 10, and Grafana OOMKilled at a guessed 256Mi
limit in phase 23.

Keycloak is a JVM, which makes a guessed limit worse than usual: the image
defaults to `-XX:MaxRAMPercentage=70 -XX:InitialRAMPercentage=50`, so at a
768Mi limit the JVM sizes a ~537Mi maximum heap and then metaspace, code
cache and thread stacks land on top of it — arriving at the limit by
arithmetic, not under load.

So the heap is set **explicitly** rather than derived from the limit. The
result was measured on 2026-09-20:

| Setting | Value |
| --- | --- |
| `JAVA_OPTS_KC_HEAP` | `-Xms192m -Xmx448m` |
| `resources.requests.memory` | 384Mi |
| `resources.limits.memory` | 896Mi |
| `resources.requests.cpu` | 100m |
| Steady-state memory | 602Mi (`kubectl top`, 91 minutes since restart; pod age 116 minutes) |
| Observed 15-minute maximum | 632242176 bytes (603Mi, cAdvisor); login attribution unverified |
| Pod restart to Ready | 21 seconds against an existing initialized database |

Memory-only limit, no CPU limit, matching every other `limits:` block in
this repository. The observed 15-minute maximum was 78.5% of the original 768Mi
limit, above the plan's roughly 70% threshold. Raising the limit to 896Mi
puts that observation at 67.3% while adding only 128Mi to this memory-constrained
host. The node's committed limits measured **13098Mi (82%)** after the
change, compared with the 12074Mi (75%) baseline in §2.

The backup test supplied a second check on the margin: a default live
`kc.sh export` OOMKilled the 768Mi container. At 896Mi, directory export
with an export-only `-Xms64m -Xmx128m` heap completed without restarting
the server. The export process still returned non-zero after writing the
realm because its second management listener could not bind port 9000,
already held by the running server; the resulting JSON parsed as realm
`homelab`.

First installation adds schema creation and realm import to the Quarkus
augmentation (K14), so the startupProbe must allow for that extra work.
The measured 21-second pod restart against an initialized database used
10.5% of the 200-second budget; it does not measure empty-database first
installation. The budget remains unchanged pending that measurement.
The 896Mi limit is supported by the steady sample and export evidence;
an explicitly observed browser-login memory measurement remains pending.

## 6. Files

New:

| File | Contents |
| --- | --- |
| `platform/keycloak/config/keycloak.yaml` | ServiceAccount, Service (8080 + 9000), Deployment |
| `platform/keycloak/config/realm.yaml` | ConfigMap: the `homelab` realm JSON |
| `platform/keycloak/config/vault-secrets.yaml` | `keycloak` ns: VaultConnection, VaultAuth, two VaultStaticSecrets (`keycloak-admin`, `keycloak-db`) |
| `platform/keycloak/config/database-job.yaml` | `databases` ns: ServiceAccount, VaultAuth, VaultStaticSecret (`keycloak-db`), and the Sync-hook Job |
| `platform/databases/postgres/config/networkpolicy.yaml` | Default-deny ingress on `databases` + PostgreSQL client and exporter rules |
| `platform/databases/redis/config/networkpolicy.yaml` | Redis client and exporter rules |
| `platform/keycloak/README.md` | Import semantics, the paste ceremony, MFA, break-glass, backups, rotation traps |
| `environments/homelab/apps/keycloak.yaml` | Application, sync-wave 24 |
| `infrastructure/ingress/config/keycloak-ingress.yaml` | Tailscale Ingress, ProxyClass `homelab` |
| `observability/monitoring/targets/keycloak.yaml` | ServiceMonitor — integration 8. Shipped by `monitoring-config`, also at wave 24, so it may apply before Keycloak's Service exists; a ServiceMonitor with no matching Service is valid and simply has no targets until one appears |

The policies live with the databases they protect because a policy owned by
one of the database's clients would be removed with that client.

Modified:

| File | Change |
| --- | --- |
| `bootstrap/namespaces/namespaces.yaml` | `keycloak` namespace, with a comment naming this phase |
| `platform/vault/configure-vault.sh` | 3 seeds, 2 policies, 2 roles; extend `vso-grafana-read` |
| `observability/monitoring/values.yaml` | `grafana.ini` `server.root_url` + `auth.generic_oauth`; `envValueFrom` for the client secret |
| `observability/monitoring/config/vault-secrets.yaml` | Second VaultStaticSecret on the existing `grafana` VaultAuth |
| `observability/monitoring/targets/postgres-exporter.yaml` | **Pod labels on the Sync-hook Job template** — see §7 |
| `platform/databases/postgres/README.md` | The NetworkPolicy now governs who may connect, and the `pgclient` recipe is affected |
| `observability/monitoring/README.md` | Integration 8; the Grafana OIDC block and its `root_url` requirement |
| `README.md` | Layout row, wave-24 paragraph, Access section, first-install steps, Known gaps (3 new, 1 correction) |
| `docs/workstation-plan.md` | Mark §28 delivered, with measured figures |

## 7. The NetworkPolicy, and three things a naive default-deny breaks

K15. Keycloak is PostgreSQL's first *application* consumer, which is the
precise trigger the README's Known-gaps entry names: "It stops being
acceptable the moment the first consumer arrives, which is also the moment
you learn what the policy should say. Pick it up in that phase, not before."

The policy is ingress-only (`policyTypes: [Ingress]`) so egress and
therefore DNS are untouched, which bounds the blast radius of a wrong
selector. Namespace selection uses `kubernetes.io/metadata.name`, confirmed
present on this cluster.

Writing it surfaced three live dependencies that a default-deny silently
severs:

1. **Prometheus scrapes across the namespace boundary.** `postgres-exporter`
   (:9187) and `redis-exporter` (:9121) both live in `databases` and are
   scraped from `monitoring`. Without an explicit allow, `pg_up` and every
   Redis series go to zero while Argo CD, the exporters and Prometheus all
   stay green. This is the most likely way to get this phase wrong.
2. **The phase-22 Sync-hook Job has no pod labels.** The `template` in
   `observability/monitoring/targets/postgres-exporter.yaml` sets none, so
   no `podSelector` can name it. Under default-deny it can no longer reach
   5432 on its next run — and because it is a `Sync` hook, that run is the
   next time anyone syncs `monitoring-config`, arbitrarily far from this
   commit. Adding a label to that file is a required cross-cutting edit of
   this phase, not a tidy-up.
3. **Kubelet probes may or may not be exempt.** `pg_isready` runs as an
   `exec` probe, which is not network traffic and is therefore safe — but
   the redis and exporter probes should be re-checked rather than assumed,
   because a probe failure here restarts a database.

Allow-rules:

| From | To | Port |
| --- | --- | --- |
| ns `keycloak` | `app=postgres` | 5432 |
| ns `databases`, `app=postgres-exporter` | `app=postgres` | 5432 |
| ns `databases`, `app=redis-exporter` | `app=redis` | 6379 |
| ns `databases`, `job=postgres-client` | `app=postgres` | 5432 |
| ns `monitoring` | `app=postgres-exporter` | 9187 |
| ns `monitoring` | `app=redis-exporter` | 9121 |

`job=postgres-client` is the label added to both Sync-hook Job templates —
the existing exporter one and this phase's new one.

Verification must include a client that is **refused**. The `pgclient`
throwaway pod in `default` documented in
`platform/databases/postgres/README.md` is exactly that: it currently
succeeds, and after this phase it must fail. A policy proved only by
connections that still work is not proved at all.

## 8. Secrets, and the one hand-run step

Three Vault paths, two new policies, two new roles, and one extension of an
existing policy:

| Path | Keys | Policy | Role | Bound SA / ns |
| --- | --- | --- | --- | --- |
| `homelab/keycloak` | `username=admin`, generated password | `vso-keycloak-read` (also grants `keycloak-db`) | `vso-keycloak` | `keycloak` / `keycloak` |
| `homelab/keycloak-db` | `username=keycloak`, generated password | `vso-keycloak-db-read` | `vso-keycloak-db` | `keycloak-db` / `databases` |
| `homelab/keycloak-grafana` | `clientSecret=` placeholder | existing `vso-grafana-read`, extended | existing `vso-grafana` | existing `grafana` / `monitoring` |

The Secrets VSO derives from those paths:

| Namespace | Secret | Read by |
| --- | --- | --- |
| `keycloak` | `keycloak-admin` | `KC_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` |
| `keycloak` | `keycloak-db` | `KC_DB_USERNAME` / `KC_DB_PASSWORD` |
| `databases` | `keycloak-db` | the Sync-hook Job's `ALTER ROLE` |
| `monitoring` | `keycloak-grafana` | `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` |

The two `keycloak` Secrets share one VaultAuth, because one policy grants
both paths. The `databases` one needs its own ServiceAccount and VaultAuth —
a VaultAuth's ServiceAccount must reside in the consuming Secret's namespace
— but **not** its own VaultConnection: it reuses the `vault` VaultConnection
PostgreSQL already created in `databases`, exactly as `redis` and
`postgres-exporter` do.

K10: two roles rather than one role bound to both namespaces. Vault's
kubernetes auth matches the **cross-product** of
`bound_service_account_names` and `bound_service_account_namespaces`, so a
single role naming both would additionally authorize `keycloak` in
`databases` and `keycloak-db` in `keycloak` — two identities nobody
intended, and a widening that no comment in the manifest would reveal.

Every password is generated inside the pod, written to a file so only the
filename becomes an argument, and never displayed — the idiom already used
for `postgres`, `redis`, `nexus`, `grafana` and `postgres-exporter`.
Alphanumeric only: these values reach a JDBC URL and a browser login.

### 8.1 The paste ceremony

One hand-run step, and it belongs in the root README's first-install list:

1. Keycloak imports the realm on first start and generates the `grafana`
   client secret.
2. A human reads it once: admin console → `homelab` realm → Clients →
   `grafana` → Credentials.
3. Follow the hidden-input paste ceremony in `platform/keycloak/README.md`
   to overwrite the Vault placeholder without placing the secret in argv.
4. VSO refreshes the Secret in `monitoring`; Grafana picks it up on its next
   restart.

Until step 3 runs, SSO returns `invalid_client` and the local admin form is
the way in. That is the designed degradation, not a failure.

### 8.2 Two rotation traps

**`KC_BOOTSTRAP_ADMIN_*` is honoured only when no admin exists.** Changing
the value in Vault does **not** change the live password — unlike
`postgres`, whose Sync-hook Job runs `ALTER ROLE` unconditionally on every
sync precisely so a rotated Vault value reaches the database. The Keycloak
admin password must be rotated through Keycloak's own UI or admin API, and
Vault updated to match, in that order. This is the same class of trap
`platform/nexus/README.md` documents for the Nexus admin password.

**The Grafana client secret has no reconciler either.** Regenerating it in
Keycloak silently breaks SSO until Vault is updated. Both traps are Known
gaps in §11.

## 9. Keycloak's configuration

K14: `kc.sh start --import-realm`, not `--optimized`. The stock image
performs its Quarkus augmentation at boot from the build-time env vars,
which costs startup time and forbids `readOnlyRootFilesystem`, but avoids
standing up a Dockerfile, a CI pipeline and a published derived image to run
one Deployment. `beacon` owns the image-building pattern in this homelab and
lives in its own repository; this phase does not add a second one.

| Setting | Value | Why |
| --- | --- | --- |
| `KC_DB` | `postgres` | build-time; triggers the augmentation |
| `KC_DB_URL` | `jdbc:postgresql://postgres.databases.svc.cluster.local:5432/keycloak` | the database the Job creates |
| `KC_DB_USERNAME` / `KC_DB_PASSWORD` | `keycloak` / from `keycloak-db` Secret | |
| `KC_HOSTNAME` | `https://keycloak.taildf6cd4.ts.net` | hostname v2 takes a full URL |
| `KC_HTTP_ENABLED` | `true` | TLS terminates at the Tailscale proxy |
| `KC_PROXY_HEADERS` | `xforwarded` | without it Keycloak builds `http://` URLs |
| `KC_HEALTH_ENABLED` / `KC_METRICS_ENABLED` | `true` | probes and integration 8, on :9000 |
| `KC_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | from `keycloak` Secret | first start only — §8.2 |
| `JAVA_OPTS_KC_HEAP` | `-Xms192m -Xmx448m` | §5 |

Probes all target the management port 9000: `startupProbe` on
`/health/started` with a budget generous enough for the augmentation plus
the import, then readiness on `/health/ready` and liveness on
`/health/live`. The startupProbe is what keeps the first boot from becoming
a liveness restart loop.

**One consequence to accept, not fix:** with `KC_HOSTNAME` set, a
`kubectl port-forward` session gets redirected to the tailnet URL, so
port-forward is a degraded route rather than the clean fallback it is for
Prometheus and Loki. The tailnet is effectively the only way into Keycloak's
UI. This is stated in the README rather than worked around, because the
alternative — leaving the hostname unset — produces redirect URLs that
break OIDC.

### 9.1 The realm

K11: `master` holds the break-glass admin and nothing else. The `homelab`
realm holds the humans. The JSON in git declares structure, never people:

- Groups `homelab-admins` and `homelab-users`, carrying realm roles `admin`
  and `user`.
- Confidential client `grafana`, standard flow only, redirect URI
  `https://grafana.taildf6cd4.ts.net/login/generic_oauth`, with a **groups
  protocol mapper** — without it the `groups` claim is absent from the
  token and Grafana's role mapping silently falls through to its default.
- K12: `CONFIGURE_TOTP` as a realm **default required action**, so every
  account enrols TOTP at first login. The roadmap asks for MFA on admin
  accounts; a realm-wide requirement is both stricter and simpler than a
  conditional-OTP browser flow, and is deliberately over-delivering. It can
  be relaxed to admins-only later by editing the browser flow.
- `UPDATE_PASSWORD` is explicitly enabled with `defaultAction: false`.
  Declaring `requiredActions` suppresses built-in action registration, so
  TOTP alone leaves temporary passwords without a registered replacement
  action. The new seed fixes fresh imports; `IGNORE_EXISTING` requires an
  operator to register/enable Update Password in an existing realm.
- `bruteForceProtected: true`; `registrationAllowed: false`.
- K13: `resetPasswordAllowed: false`. There is no SMTP anywhere in this
  homelab, so "Forgot password" would render a form that silently never
  sends mail — a worse outcome than not offering it. Recovery is the
  break-glass admin.
- `sslRequired: external` — the default — rather than `all`, so in-cluster
  requests and port-forward are not refused outright.

K12 applies to `homelab` and to nothing else: a required action belongs to one
realm, so the break-glass admin in `master` is untouched by it. That account
deliberately carries **no** TOTP. A recovery account whose use needs a second
device fails exactly when that device is the thing lost or unavailable, and
with K13 and no SMTP it is the only recovery path there is. Its protection is
a 32-character generated password held only in Vault, on a tailnet-only
account nobody uses day to day. The asymmetry is intentional and is recorded
in the component README so it does not read as an oversight.

K5: `--import-realm` uses the `IGNORE_EXISTING` strategy, so the file is
effective on first start and ignored on every start after. That is the
chosen trade: the realm is **reproducible** from git, but not
**reconciled** — an edit made in the admin console persists in PostgreSQL
and drifts from the file with nothing reporting it. §11 records this as a
Known gap and §10 makes the realm export the mitigation.

### 9.2 Grafana

```text
grafana.ini:
  server.root_url            https://grafana.taildf6cd4.ts.net
  auth.generic_oauth.enabled true
  ...role_attribute_path     contains(groups[*], 'homelab-admins') && 'Admin' || 'Viewer'
  ...role_attribute_strict   false
  ...allow_assign_grafana_admin false
```

`root_url` is **required, not cosmetic**: Grafana builds `redirect_uri` from
it, and without it derives one from the request `Host` header — producing an
`http://` URI that Keycloak rejects as a redirect mismatch, an error that
names the URI but not the missing setting.

`role_attribute_strict: false` so a user in neither group lands as Viewer
rather than being refused, and `allow_assign_grafana_admin: false` so the
Grafana *server* admin role stays reachable only through the local account.
K16: `disable_login_form` stays at its default `false`. That form, backed by
the Vault credential from phase 23, is what makes this canary safe to try
and safe to abandon.

The client secret arrives as `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` via
`envValueFrom`, not in `grafana.ini` — an env var overrides the ini file and
keeps the secret out of a rendered ConfigMap. K17: the new VaultStaticSecret
carries `excludeRaw: true`, so this phase does not widen the two-Secret
`_raw` gap the README already tracks.

## 10. Backups

Destination `/backups/services/keycloak`, per roadmap §28. Manual in this
phase; roadmap §34 owns automation.

K4 makes this cheap: there is no volume. Two artifacts:

1. **The database** — `pg_dump -d keycloak` through `postgres-0`'s local
   socket, which `pg_hba.conf`'s `local all all trust` line covers, so no
   credential is needed. Same route as phase 18's `pg_dumpall`.
2. **A realm export** — `kc.sh export` into a file, captured alongside.
   This is what makes the roadmap's "sauvegarder la configuration des
   realms, clients, groupes et rôles" real, and it is also the mitigation
   for K5's drift: when the console is authoritative for a change the file
   never received, the export is the only record of it.

Use the component README's atomic recipes: `umask 077`, unique captured
timestamps, same-filesystem private temporary files, checked producers
and transfers, artifact validation, then a no-overwrite atomic rename.
The database pipeline uses `pipefail`. Realm exports use
`--users realm_file`; after onboarding they must contain a nonempty user
array (prefer the known user count as the minimum). The historical dump
at 2026-09-20 15:34:32 +0200 held zero `homelab` users and one `master`
user; fresh backups are required after onboarding. A known post-write
management-port conflict (exit 1) requires its specific diagnostics plus
successful transfer and JSON validation; other failures publish nothing.

Losing the Keycloak pod costs nothing. Losing the `keycloak` database costs
every account, and `/backups` is mode 0777 — the same handling-hygiene
problem the README already records for `pg_dumpall` output, now holding
password hashes and TOTP seeds. Stated in the component README; the fix is
Roadmap §33's, not this phase's.

## 11. Known gaps this phase creates

To be written into the root README, not left in this spec:

- **Realm edits in the admin console drift from git silently.** K5's
  accepted cost. The mitigation is the §10 realm export; there is no alarm
  and no diff. Revisit when keycloak-config-cli catches up to Keycloak 26.7.
- **The Keycloak admin password cannot be rotated through Vault.** §8.2.
- **The Grafana OIDC client secret has no reconciler.** §8.2.
- **The client-secret paste is a hand-run rebuild step.** §8.1; it goes in
  the first-install list beside the GHCR token, which exists for the same
  reason.
- **No NetworkPolicy outside `databases`,** including `keycloak` itself. §4.1.
- **Correction, not a new gap:** the README's "No `NetworkPolicy` anywhere,
  including `databases`" is wrong — four exist in `argocd`, chart-shipped —
  and this phase resolves the `databases` half of what it meant.

## 12. Verification

Nothing here is satisfied by `Synced`/`Healthy`. This repository has a live
instance of why: `registry` read Healthy for 40 minutes in phase 20 while no
Secret existed anywhere.

| # | Check | Passes when |
| --- | --- | --- |
| 1 | `kubectl -n databases get vaultstaticsecret keycloak-db` | SYNCED/HEALTHY/READY all true |
| 2 | `psql` into `postgres-0`: `\l` and `\du` | database `keycloak` and role `keycloak` both present |
| 3 | Keycloak pod logs | realm `homelab` imported; no augmentation error |
| 4 | `curl` :9000 `/health/ready` from inside the pod | `UP` |
| 5 | Prometheus Targets page | `keycloak` **UP** as integration 8 |
| 6 | `https://keycloak.taildf6cd4.ts.net` in a browser | real Let's Encrypt cert, login page renders |
| 7 | Log in as the break-glass admin in `master` | the admin console opens; TOTP is **not** demanded — see §9.1 |
| 8 | Grafana SSO as a `homelab-admins` member | lands with Grafana role **Admin** |
| 9 | Grafana SSO as a `homelab-users` member | lands with role **Viewer** |
| 10 | Grafana local admin form | still works |
| 11 | `kubectl delete pod` the Keycloak pod | comes back with the realm, groups and client intact |
| 12 | `pgclient` throwaway pod in `default` | **connection refused / times out** — the policy works |
| 13 | `pg_up` and Redis series in Prometheus | still 1 / still present after the policy lands |
| 14 | Re-sync `monitoring-config` | the relabelled exporter Job still reaches 5432 |
| 15 | `kubectl top pod -n keycloak` at rest and across a login | measured, and written back into §5 |
| 16 | `pg_dump -d keycloak` + `kc.sh export` | both produce non-empty artifacts in `/backups/services/keycloak` |

Checks 12–14 are the ones most likely to be skipped and most likely to
matter: 12 is the only one that proves the policy denies anything, and
13–14 are the two things the policy is most likely to have broken by
accident.

## 13. CI

`.github/workflows/validate.yaml` runs `kubeconform -strict`, which covers
the new manifests and **not** `observability/monitoring/values.yaml` — one
of the seven Helm values files the README flags as excluded by
filename pattern. So the Grafana OIDC block, the most error-prone edit in
this phase, gets no schema gate at all. The realm JSON inside a ConfigMap is
opaque to kubeconform too: a malformed realm is schema-valid YAML and fails
at import.

`NetworkPolicy` is a core type, so it validates — but semantically, a
policy whose `podSelector` matches nothing is indistinguishable from a
correct one to any offline validator. Check 12 is the gate; CI is not.

Validation must use the real CI command, not a local `kubeconform`
invocation — a local run without the CRD schema flags silently skips every
CRD in this repository.
