# Nexus Repository — Design

Covers workstation-plan phase 21. The roadmap names that phase "Artifactory"
and marks it optional — "Artifactory est optionnel au début", and §38 lists it
as "Ajouter Artifactory **si nécessaire**".

**This design fills the phase's role with Sonatype Nexus Repository Community
Edition, not with JFrog Artifactory.** §2 is the argument, and it is not a
preference: the free Artifactory editions cannot do what §24 asks for.

## 1. Goal

A running Nexus Repository CE that serves two things: a `raw-hosted` repository
holding versioned build outputs of this homelab's own applications, and a
`docker-proxy` pull-through cache for Docker Hub that k3s pulls through. Its
admin credential is generated inside Vault, delivered by the Vault Secrets
Operator, and never seen by a human. Its repositories, its credential rotation
and its cleanup policy are created by a Job that lives in git, not by hand in a
web UI.

The phase is finished when an artifact round-trips through `raw-hosted` with a
matching checksum, an image pull is served from the Docker cache, **an uncached
image still pulls with Nexus scaled to zero**, the bootstrap Job is provably
idempotent, and the memory sizing has been measured rather than assumed.

## 2. Why Nexus, when the roadmap says Artifactory

Plan §24 lists what the phase is for:

```text
proxy Maven / proxy npm / PyPI / Docker registry
repositories internes / promotion d'artefacts
gestion centralisée des dépendances
```

No free JFrog edition covers that list. JFrog's own documentation splits it in
two, and neither half is enough:

| Free edition | Supports | Does not support |
| --- | --- | --- |
| Artifactory OSS | Maven, Gradle, Ivy, SBT, Generic | **Docker, OCI, Helm** |
| JFrog Container Registry (JCR) | Docker, OCI, Helm, Helm OCI, Generic | **Maven, npm, PyPI** |

Quoted from the JCR documentation: *"Maven, npm, PyPI, and other Artifactory
package types are not available in JCR. Those formats are available in
Artifactory OSS or a commercial Artifactory subscription."*

So "Artifactory, free, does §24's list" is not an option that exists. The
choices were: run two JFrog products, buy Artifactory Pro, cut the scope to one
half, or use a product whose free edition spans both. Nexus Repository CE spans
both — Maven, npm, PyPI, Docker, Helm and raw in one deployment — and is the
only one of those four that costs neither money nor a second stateful workload.

**The roadmap's word is kept; the roadmap's product is not.** §24's heading
stays "Artifactory" because that is the name the plan gives the *role*. This
document records that the role is filled by Nexus, and `platform/artifactory/`
— currently a lone `.gitkeep` — is renamed to `platform/nexus/` so the
directory does not lie about what is in it.

### 2.1 Why the phase runs at all, and what it deliberately does not do

The "si nécessaire" gate deserves the same honesty phase 19 applied to Redis.
Nothing in this cluster builds a Maven, npm or PyPI artifact today; `apps/` is
empty and the only workload in the `apps` namespace is a canary. A dependency
proxy for language ecosystems would therefore be infrastructure with no
consumer — and unlike Redis, Nexus is **not** cheap to run.

So the language proxies are cut. What is left has consumers that exist today:

- **`raw-hosted`** serves the role actually wanted — keeping versioned build
  outputs of an application. It is format-agnostic, so it needs no decision
  about which language ecosystem matters, and it fills the one gap phase 20
  genuinely left: GHCR versions *images*, and nothing here versions jars,
  tarballs, binaries or release bundles.
- **`docker-proxy`** has a consumer right now: every image pull this cluster
  makes. It converts Docker Hub's anonymous rate limit and every cold pull into
  a local disk read.

**What this phase must not do is pretend.** It does not claim a dependency
proxy is needed, does not add a hosted Docker registry duplicating the GHCR
path phase 20 just built and wired, and does not automate a backup it has not
proven.

## 3. Measured starting state

Measured 2026-09-17 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 12 including `root`, all Synced/Healthy |
| k3s | `v1.36.4+k3s1` |
| containerd | `v2.3.4-k3s1.36` — **containerd 2.x**, which matters in §8 |
| `/etc/rancher/k3s/registries.yaml` | **Does not exist**; this phase creates it |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `ingress-config` 21, `vso-operator` 21, `vso-config` 22, `postgres` 23, `redis` 23, `registry` 23, `beacon` 24 |
| PVCs | 2 — `data-vault-0` 5Gi, `data-postgres-0` 10Gi |
| `/srv` free | 851G of 938G |
| `/backups` free | 849G of 916G |
| **Memory available** | **7.7Gi of 15.5Gi** |
| Namespaces from `namespaces.yaml` | `cert-manager`, `vault`, `tailscale`, `vault-secrets-operator-system`, `databases`, `apps` |

Memory is the binding constraint of this phase, the way it was in phase 18 and
was not in phase 19. Nexus is a JVM application whose shipped defaults reserve
roughly 5.4Gi — see §7.

## 4. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| N1 | **Nexus Repository CE, not JFrog Artifactory** | §2. No free Artifactory edition covers §24's list |
| N2 | **Two repositories only: `raw-hosted` and `docker-proxy`** | §2.1. Both have a consumer today; language proxies do not |
| N3 | **No hosted Docker registry** | GHCR already holds this homelab's images and its pull credential is wired in (phase 20). A second copy earns nothing |
| N4 | **Embedded H2, not the wave-23 PostgreSQL** | §6.2. CE's own caps bind before H2's on both axes, so an external database buys zero headroom while costing a per-app DB user, a second VSO path and a two-source backup |
| N5 | Deployment with `strategy: Recreate`, not a StatefulSet | One RWO volume and no stable network identity to preserve. `Recreate` because a rolling update would deadlock on the RWO claim. Also sidesteps the `volumeClaimTemplates` atomic-list trap documented in `postgres.yaml` |
| N6 | Standalone PVC, not `volumeClaimTemplates` | Follows N5; the PVC survives the Deployment |
| N7 | `sonatype/nexus3` pinned to an exact version | Same reasoning phase 18 gave for Postgres. The exact tag is selected and recorded at implementation time (§15), never `latest` |
| N8 | `runAsUser: 200`, `runAsGroup: 200`, `fsGroup: 200` | The image runs as UID 200 with data at `/nexus-data`. This is precisely troubleshooting entry 1's trap on `/srv`-backed volumes; `fsGroup` is the mechanism phase 16 proved on this cluster |
| N9 | **JVM sized far below the shipped default** | §7. Default is ~5.4Gi reserved against 7.7Gi free |
| N10 | Admin password generated in Vault, delivered by VSO | The doctrine every other credential here follows |
| N11 | **Bootstrap Job reads `/nexus-data/admin.password` rather than setting `randompassword=false`** | §9. The alternative opens a known-credential window that stays open forever if the Job fails |
| N12 | Bootstrap Job is idempotent, keyed on that file's absence | §9. The file's presence *is* the "first run" signal, because Nexus deletes it on the first password change |
| N13 | Namespace `artifacts`, new, with its own four Vault objects | §10. The `VaultAuth` CRD requires the ServiceAccount to live in the consuming Secret's namespace |
| N14 | Sync-wave **23**, the same wave as `postgres`, `redis` and `registry` | §11. It depends on `vso-config` (22) and on nothing at 23 |
| N15 | Both Ingresses live in `ingress-config` at wave 21 | The repo's standing rule that nothing waits on ingress health. §11.1 covers the new wrinkle this creates |
| N16 | **Two tailnet hostnames, one per endpoint** — not Docker path-based routing | §6.3. Sonatype's own docs warn that a context path in a registry URL can break authentication; one hostname per endpoint is what this repo already does for `argocd`, `vault` and `beacon` |
| N17 | k3s reaches the mirror via **NodePort on `127.0.0.1`**, not cluster DNS or a tailnet hostname | §8. containerd runs on the host, does not use CoreDNS, and must not depend on the Tailscale operator for every layer pull |
| N18 | `registries.yaml` mirrors **`docker.io` explicitly**, with no `rewrite:` rules | §8. `"*"` breaks fallback on containerd 2.x (k3s#11857); rewrites break it too (k3s#7007) |
| N19 | `registries.yaml` is versioned in this repo but **not reconciled** | Same arrangement as `infrastructure/networking/policy.hujson` |
| N20 | Bootstrap Job creates a **scheduled cleanup task** | §6.2. CE stops accepting components at 40,000 and says nothing; a Docker proxy accrues one per cached tag |
| N21 | `excludeRaw: true` on the `VaultStaticSecret` | Same reasoning as phase 19's R11: do not add a fourth instance of a known gap |
| N22 | Backups documented, **not automated** | §13. Automation is phase 34. Stating the gap beats implying coverage |

## 5. Architecture

```
  configure-vault.sh  ── generates a random admin password inside vault-0
          │
          ▼
   Vault KV v2:  homelab/nexus
          ▲
          │ read, authorised by policy vso-nexus-read
          │
   Vault kubernetes auth, role vso-nexus
          ▲
          │ ServiceAccount nexus, namespace artifacts
          │
   Vault Secrets Operator
          │
          ▼
   Secret nexus-admin  (namespace artifacts)
          │
          ├────────────────► Job nexus-bootstrap ──(REST)──┐
          │                    mounts the PVC to read      │
          │                    /nexus-data/admin.password  │
          ▼                                                ▼
   Deployment nexus  ◄──────── PVC nexus-data ──────► Nexus REST API
          │                     (H2 + blobs + config)      │
          │                                                │ creates
          │                                       raw-hosted, docker-proxy,
          │                                       docker connector :8082,
          │                                       cleanup task
          ▼
   Service nexus (artifacts)
     ├── 8081  ──► Ingress nexus.taildf6cd4.ts.net         (UI + raw)
     ├── 8082  ──► Ingress nexus-docker.taildf6cd4.ts.net  (docker clients)
     └── 8082  ──► NodePort 30082
                        ▲
                        │  http://127.0.0.1:30082
                        │
            /etc/rancher/k3s/registries.yaml  ── NOT reconciled by Argo CD
                        │
                        ▼
                   containerd ──(on failure, always)──► docker.io
```

The admin credential exists only in Vault, in the Secret VSO derives from it,
and inside Nexus's own H2 store. It is absent from the pod spec, from the
process list, and from this repository.

## 6. Facts verified from vendor documentation

Everything in this section was read from vendor docs on 2026-09-17, not
recalled. Where a fact is reasoned rather than cited, §7 says so explicitly.

### 6.1 The edition split

Covered in §2. Artifactory OSS has no Docker; JCR has no Maven/npm/PyPI.

### 6.2 CE's caps bind before H2's — on both axes

| Limit | Community Edition | Embedded H2 |
| --- | --- | --- |
| Components | **40,000** | 100,000 |
| Requests/day | **100,000** | 200,000 |

CE is the stricter ceiling on both. Sonatype does recommend external PostgreSQL
for all deployments, but that recommendation addresses deployments outgrowing
H2 — and **a CE deployment cannot outgrow H2**, because it is stopped by its
own licence first. N4 follows directly.

The behaviour at the cap is the dangerous part, and it is why N20 exists:
*"Community Edition's built-in safeguards will pause the addition of new
components until usage returns below both thresholds."* It pauses. It does not
error. A Docker proxy that has silently stopped caching looks exactly like one
that is working, only slower.

CE also requires accepting its EULA — via the onboarding wizard or the EULA
REST API — **before components can be fetched or uploaded**. That is a
ceremony, and §9 puts it in the Job rather than in a human's browser.

### 6.3 Docker routing, and why path-based is the wrong one here

Nexus offers port connectors, subdomain routing, and path-based routing (added
in 3.83.0). Only one may be active at a time. Sonatype's own guidance on the
path-based option: *"Do not include a repository context path in the registry
URL (for example, hostname/repository-name), as requests are made to the
registry root (for example, /v2/), and using a context path can result in
authentication failures."*

A port connector fronted by its own tailnet hostname avoids the question
entirely: the registry answers at the host root, the Tailscale proxy supplies a
real Let's Encrypt certificate per hostname, and no wildcard certificate or
path rewriting is involved. Hence N16.

The tailnet hostname and the NodePort of §8 are not redundant, and neither
replaces the other: the NodePort is plain HTTP, host-local, and exists for
containerd, which cannot use cluster DNS; the hostname carries real TLS and
exists for `docker` clients, on this machine or any other tailnet device, which
should not be configured to trust an insecure registry. Cutting either one
costs a consumer.


### 6.4 containerd always falls back — which is what makes a rebuild possible

The obvious objection to N17 is circular: if k3s pulls `docker.io` through
Nexus, and Nexus runs inside k3s, a cold cluster cannot pull Nexus's own image.

It can. k3s documents that *"the default endpoint is always tried as a last
resort, even if there are other endpoints listed for that registry"*, disabled
only by `--disable-default-registry-endpoint`, which this host will not set. A
cold rebuild therefore pulls everything directly from Docker Hub until Nexus
exists, then starts using it.

Two documented k3s bugs constrain how the mirror is written, and both apply to
this host's containerd 2.3.4:

- **k3s#11857** — fallback is broken when the mirror is the `"*"` wildcard on
  containerd 2.0+. Use an explicit `docker.io` key.
- **k3s#7007** — `rewrite:` rules break fallback to the original repository.
  Use none.

This is the single most load-bearing claim in the design, so §16 tests it
directly rather than trusting it.

### 6.5 Image facts

`sonatype/nexus3` runs as **UID 200**, stores state at **`/nexus-data`**, and
listens on **8081**. JVM options are passed through `INSTALL4J_ADD_VM_PARAMS`;
the documented example is `-Xms2703m -Xmx2703m -XX:MaxDirectMemorySize=2703m`,
and Sonatype advises setting `-Xms` equal to `-Xmx` to avoid heap resizing.
Nexus requires Java 21, bundled in the image since 3.78.0.

## 7. Sizing, and the honest confidence level

The shipped default reserves ~5.4Gi before JVM overhead, against 7.7Gi free on
a host that is also a workstation, a KVM host and a future game server, and
that has a recorded history of memory incidents — troubleshooting entries 2 and
4, and the CoreDNS swap death spiral the `homelab` ProxyClass exists to prevent.
The default is therefore not an option.

Proposed starting point:

```text
-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m

requests:  cpu 200m, memory 1.5Gi
limits:    memory 2.5Gi          # memory only — no CPU limit, as in postgres.yaml
```

**These numbers are reasoned, not cited.** What was verified is the 2703m
default and Sonatype's smallest published profile — 8GB RAM / 2 CPU, sized for
20,000 requests per hour, which is orders of magnitude beyond one developer and
one single-node cluster. No documented *minimum* heap was found. The numbers
above are therefore a starting point that §16.9 requires to be measured under
real load before this phase is called done.

The failure mode to plan for: heap plus direct memory plus metaspace, code
cache and thread stacks are all counted by the cgroup, and the first two alone
are 1.75Gi of the 2.5Gi ceiling. **If it OOM-kills, the fix is raising the
limit, not the heap** — raising the heap inside an unchanged limit moves the
cliff closer.

## 8. The Docker mirror, and the file Argo CD cannot see

containerd runs on the host. It does not use cluster DNS, and it must not
depend on the Tailscale operator to pull an image. That rules out
`nexus.artifacts.svc` (CoreDNS may not be up when containerd pulls) and rules
out the tailnet hostname (an extra proxy hop, extra memory, and a dependency
cycle through a workload that is itself pulled by containerd). A pinned
ClusterIP would work but is fragile across a rebuild.

A NodePort is none of those things:

```yaml
# /etc/rancher/k3s/registries.yaml
mirrors:
  docker.io:
    endpoint:
      - "http://127.0.0.1:30082"
```

`http://` is required — containerd assumes HTTPS otherwise. The Docker
connector on 8082 serves the repository at its own root, so no path and no
rewrite are needed, which is also what keeps k3s#7007 out of play.

**Applying this file restarts the cluster.** k3s reads it at startup, so the
procedure is: copy it into place, `systemctl restart k3s`. That restarts every
pod on this node and is the most disruptive step in the phase — done once,
deliberately, at a quiet moment, and never as a side effect of something else.

**The operational cost, stated plainly:** once configured, every `docker.io`
pull tries Nexus first. A dead Nexus does not break pulls — §6.4 — but it does
make them slower, because each one waits for the mirror attempt to fail before
falling back. The escape hatch is deleting the file and restarting k3s.

The canonical copy lives at `platform/nexus/registries.yaml`, versioned but not
reconciled (N19), exactly as the tailnet ACL policy is.

## 9. The bootstrap Job

Nexus's repositories, users, connectors and tasks are rows in its own database,
not Kubernetes objects. Deployed naively, the entire substance of phase 21
would live inside a PVC where git cannot see it and a rebuild could only be a
restore. The Job is what keeps the phase in git.

It runs after the Deployment is Ready and does, in order:

1. Accept the CE EULA via the EULA REST API (§6.2 — without this, nothing can
   be fetched or uploaded).
2. **Authenticate.** If `/nexus-data/admin.password` exists, this is a first
   run: log in with it and change the admin password to the value in Secret
   `nexus-admin`. If it does not exist, the rotation already happened: log in
   with the Vault password.
3. Create `raw-hosted` and `docker-proxy` if absent.
4. Create the Docker connector on 8082 if absent.
5. Confirm anonymous read works against the Docker proxy. If it answers 401,
   the fix is enabling Nexus's **Docker Bearer Token realm** — a realm change,
   not a repository setting, and easy to misdiagnose as a permissions problem.
6. Create the scheduled cleanup task purging unused proxy components (N20).

### 9.1 How the Job is sequenced, and how it re-runs

Two mechanics the phase depends on, neither of which is the default.

**Sequencing.** "After the Deployment is Ready" is not something a plain Job
waits for. Nexus takes a minute or more to come up on this host, and a Job that
starts at the same time as the Deployment fails on connection-refused. The Job
therefore carries an Argo CD resource-level sync-wave above the Deployment's
*and* polls `/service/rest/v1/status` in an init step until it answers, with a
bounded timeout. The wave alone is not enough: Argo CD advances a resource wave
on the Deployment being Healthy, and a Nexus pod reports Ready before its REST
API accepts writes.

**Re-running.** A Job is immutable, so re-applying an unchanged one does
nothing at all — which would make §17.8's idempotency check untestable and
would mean a changed repository definition never reaches the cluster. The Job
is therefore an Argo CD `Sync` hook with
`hook-delete-policy: BeforeHookCreation`, so every sync recreates and re-runs
it. That is safe precisely because of N12: on every run after the first, the
password file is absent, the Job authenticates with the Vault password and
reconciles the repositories rather than creating them.

The Deployment's own readiness deserves the same tolerance phase 18 gave
Postgres: a `startupProbe` with a generous `failureThreshold` against
`/service/rest/v1/status`, so a slow start on a loaded host is not mistaken for
a broken one. This host has a recorded history of probe timeouts under memory
pressure — troubleshooting entries 4 and 10.

**Why the password file rather than `randompassword=false` (N11).** Setting
`nexus.security.randompassword=false` makes the first-boot credential a
publicly documented `admin/admin123`. That is convenient and it is what most
automation does. Its failure mode is bad: if the Job crashes or never runs, the
known credential stays live indefinitely on a service published to the tailnet.
Reading the boot-generated file has no such window.

It works here because **RWO is a per-node constraint, not a per-pod one**, and
this is a single-node cluster, so the Job and the Deployment can both mount the
claim. This is the one design element that would break on a second node, and
N12's idempotency signal comes free with it: Nexus deletes the file when the
password changes, so *file present* means first run and *file absent* means
reconcile.

## 10. Namespace

New namespace `artifacts`, added to `bootstrap/namespaces/namespaces.yaml` at
wave 0 so that every namespace in this cluster keeps exactly one origin.

Not `databases`: Nexus is not one, and that namespace's comment scopes it to
"stateful data services". Not `apps`: that is scoped to workloads built by this
homelab's own CI. A new namespace also means a new ServiceAccount,
VaultConnection, VaultAuth and VaultStaticSecret rather than reused ones —
required, not stylistic, because the `VaultAuth` CRD states its ServiceAccount
"must reside in the consuming secret's namespace".

Note the standing hazard recorded in that file: `namespaces` has `prune: true`,
so removing the `artifacts` entry deletes the namespace and cascades to the
PVC. Retire the component first.

## 11. Sync wave

**Wave 23.** Nexus cannot start without Secret `nexus-admin`, which VSO creates
at wave 22, so 23 is the floor. It has no dependency on `postgres`, `redis` or
`registry`, which is exactly why it shares their wave instead of stacking above
it — the mistake both `postgres.yaml` and `redis.yaml` warn about in comments.
Sharing a wave means reconciling in parallel with neither gating the other.

Nothing sits behind wave 23 that depends on Nexus, so an unhealthy Nexus gates
nothing in Argo CD.

### 11.1 A new wrinkle: an Ingress that precedes its backend

Every Ingress in this repository lives in `ingress-config` at wave 21, so that
nothing ever waits on ingress health. Argo CD's Ingress points at a Service
from wave -1 and Vault's at one from wave 10 — both *earlier* than 21. Nexus's
Service arrives at 23, making these the first Ingresses here whose backend
comes later.

This is harmless and should be documented rather than "fixed". `ingress-config`
goes Healthy when the Tailscale operator populates `status.loadBalancer.ingress`,
which depends on the proxy being provisioned, not on the backend existing. The
two hostnames simply return 502 for the seconds between wave 21 and wave 23.
Moving these Ingresses into the Nexus Application to avoid that would break the
rule that keeps ingress health off every other component's critical path — a
much worse trade, and the exact failure the `argocd-ingress.yaml` comment
describes.

## 12. The Vault extension

`platform/vault/configure-vault.sh` gains a fourth credential, following the
shape the existing three already prove:

- `vault kv put homelab/nexus username=admin password=@"$PWFILE"` — seeded
  **only when absent**, the guard every existing path uses, so a re-run never
  clobbers a live credential.
- Policy `vso-nexus-read` on `homelab/data/nexus` — with `data/` in the path.
  The script's own comments already flag this as the most common KV v2 error.
- Role `vso-nexus` with `bound_service_account_names=nexus` and
  `bound_service_account_namespaces=artifacts`, both of which must match
  `vault-secrets.yaml` exactly. A mismatch in either is a permission denial
  that names neither side, while Argo CD reports everything Synced.

Like the other three, the password is never printed.

**Rotation** has the same shape as phase 19's: changing the value in Vault
updates the Secret within `refreshAfter`, but Nexus stores its own password
hash in H2 and will not notice. Rotation therefore means changing Vault and
re-running the bootstrap Job — which, because the password file is long gone,
takes the "already rotated" branch and needs the *old* password to authenticate.
The README must say this plainly: **rotate by updating Vault and re-running the
Job before the old value is discarded**, not after.

## 13. Backups

Plan §32 asks for database, filestore and important configuration. Under N4 all
three are the single `nexus-data` PVC, which is the main operational payoff of
choosing H2 over an external PostgreSQL.

**The trap: rsyncing a live H2 file is not a backup.** It is a copy of a
database mid-write, and it restores as corruption. The correct procedure is:

1. Run Nexus's "Export databases for backup" task.
2. Copy `/nexus-data/backup/` (the consistent export) **and**
   `/nexus-data/blobs/` (the artifacts themselves) to `/backups/`.

Both halves are required: the export without the blobs restores an index
pointing at nothing.

Phase 21 documents this in `platform/nexus/README.md`. It does **not** automate
it and does **not** perform a restore drill — that is phase 34's work, and this
is recorded here as a known gap rather than left to be assumed covered.

## 14. Out of scope

- Maven, npm and PyPI proxies — no consumer exists (§2.1); adding one later is
  a repository definition in the bootstrap Job, not a redesign
- A hosted Docker registry (N3) — GHCR holds this homelab's images
- Authenticated Docker Hub proxying for the higher pull limit — would need a
  fifth Vault secret; anonymous proxying is enough at this volume
- Backup automation and restore drills (§13, N22)
- `NetworkPolicy` — a known cluster-wide gap, written when traffic is known
- High availability, external blob storage, and anything CE's caps make moot
- Migration to external PostgreSQL — deliberately excluded by N4, and the
  documented migration path remains available if CE is ever outgrown
- Retrofitting `excludeRaw: true` onto the existing `VaultStaticSecret`s

## 15. Chosen at implementation time

One value is deliberately not fixed here: **the exact `sonatype/nexus3` tag**
(N7). It is selected at implementation, verified to start on this host, and
recorded in the manifest with the date it was probed — the practice phase 18
used for the `postgres` UID and phase 19 used for the Redis module list. It is
never `latest`, and the REST API calls in the bootstrap Job are validated
against that specific version, since Nexus's API shape has changed across minor
releases.

## 16. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **JVM sized by reasoning, not measurement** | **High — and it is the one number that can take the host down** | §7: conservative starting point, `limits.memory` well above heap+direct, and §16.9 measures it. If it OOM-kills, raise the limit, not the heap |
| **CE's 40,000-component cap pauses caching silently** | **Medium, and invisible** | N20/§6.2: scheduled cleanup task from day one; Usage Center shows the counters |
| `registries.yaml` makes every pull depend on Nexus | Medium | §6.4: containerd's default-endpoint fallback, tested directly in §16.6. Cost is latency, not failure |
| Wildcard mirror or rewrite silently breaks fallback | **High — turns a slow pull into a failed one** | N18/§6.4: explicit `docker.io`, no rewrites. Both are documented k3s bugs against containerd 2.x, which this host runs |
| k3s restart disrupts the whole node | Medium, one-off | §8: a deliberate, scheduled step, never a side effect |
| Known-credential window if bootstrap fails | **High if `randompassword=false` were used** | N11/§9: read the boot-generated file instead; no window exists |
| Job not idempotent → duplicate repos or hard failure on re-run | Medium | N12/§9: keyed on the password file's absence; §16.8 re-runs it |
| Live H2 copied as a "backup", restores as corruption | **High — and the naive procedure is the wrong one** | §13: export task first, then blobs *and* export |
| Rotation leaves Vault and Nexus disagreeing | Medium | §12: rotate by updating Vault and re-running the Job **before** discarding the old value |
| Job mounting the RWO PVC alongside the Deployment | Low here, **fatal on a second node** | §9: documented as single-node-dependent, not silently relied upon |
| UID 200 cannot write its own volume on `/srv` | High if unguarded — it is troubleshooting entry 1 verbatim | N8: `fsGroup: 200`, the mechanism phase 16 proved here |
| Rolling update deadlocks on the RWO claim | Medium | N5: `strategy: Recreate` |
| Docker proxy answers 401 to containerd | Medium — easily misdiagnosed | §9 step 5: the fix is the Docker Bearer Token realm, a realm setting, not a repository one |
| H2 deviates from Sonatype's recommendation | Low | §6.2: CE cannot reach the threshold that recommendation addresses; migration stays available |
| Unpinned image tag changing versions under a stateful workload | Eliminated | N7/§15 |
| Ingress at wave 21 with a wave-23 backend | Low | §11.1: 502 for seconds; `ingress-config` health does not depend on the backend |

## 17. Verification

The phase is complete when all of these hold, each producing evidence rather
than an assertion:

1. `nexus` Application Synced/Healthy, and its `status.resources` lists the
   Deployment, Service, PVC, Job and all four Vault objects — not merely a
   green Application.
2. `https://nexus.taildf6cd4.ts.net` reachable over the tailnet, and login
   succeeds with the password read from `homelab/nexus`. Record the HTTP status
   actually observed — phase 19 found Vault answers `307`, not `200`, and an
   earlier draft there expected the wrong code.
3. `/nexus-data/admin.password` is **gone** from the running pod, proving the
   rotation happened rather than that the Job merely exited 0.
4. `configure-vault.sh` re-run leaves the existing password unchanged, and
   prints no password at any point.
5. A file uploaded to `raw-hosted` downloads again with a **matching
   checksum** — round-trip, not upload-succeeded.
6. `crictl pull` of an image not previously cached succeeds, and the image
   appears as a component in `docker-proxy`. A second pull is served from the
   cache.
7. **With `kubectl scale deploy/nexus --replicas=0`, an image that is *not*
   cached still pulls successfully.** This is §6.4's fallback claim tested
   rather than trusted, and it is the proof that a cold rebuild works.
8. The bootstrap Job deleted and re-run completes without error and creates no
   duplicate repository, connector or task.
9. **Memory measured under load, not estimated** — `kubectl top pod` while
   pulling several images through the proxy and uploading to `raw-hosted`, with
   the before/after host figures recorded. If the pod is OOM-killed, §7's
   sizing is revised and re-measured before the phase is called done.
10. The pod deleted and recreated returns Ready with both repositories, the
    connector, the cleanup task and the rotated credential intact — the PVC
    doing its job.
11. `nexus-admin` contains **no `_raw` key** — N21 verified, not assumed.
12. All 13 Applications Synced/Healthy afterwards, and both pre-existing tailnet
    URLs still reachable. A phase that quietly breaks Argo CD's or Vault's
    ingress has not passed.
