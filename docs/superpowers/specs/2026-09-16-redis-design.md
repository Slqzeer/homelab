# Redis — Design

Covers workstation-plan phase 19. The roadmap makes this phase conditional —
"Redis peut être ajouté si nécessaire" — and branches it in two: Redis as a
cache, where "backup souvent inutile", or Redis persistent, where a PVC and a
backup strategy are required. §37's priority list omits Redis altogether.

**This design takes the cache branch deliberately**, and §2 argues why that is
the honest reading rather than a shortcut.

## 1. Goal

A running, password-protected, deliberately ephemeral Redis whose credential is
generated inside Vault, delivered by the Vault Secrets Operator, and never seen
by a human, committed to git, or visible in `ps`.

The phase is finished when a client authenticates with the Vault-delivered
password from a separate pod, an unauthenticated client is refused, eviction is
provably configured, and the pod restarts clean with no volume of any kind.

## 2. Why the cache branch, and why the phase runs at all

Nothing in this cluster connects to Redis. Nothing connects to PostgreSQL
either — the root README's known gaps say so plainly. The roadmap's "si
nécessaire" gate is therefore, read literally, not met.

The phase still earns its place, because the two branches are not symmetric in
cost. The cache branch is cheap to build, cheap to run, and — critically —
**cheap to reverse**: there is no data, so there is no migration, no restore
drill, and no backup to verify. The persistent branch would demand a PVC, an
AOF/RDB policy, a documented dump-and-restore procedure, and evidence that the
restore actually works, all in service of a datastore with no writer.

Building the cheap branch now means the credential path, the wave placement and
the eviction tuning are already proven when a consumer does arrive. Promoting a
cache to persistent later is a contained change to one Deployment. Discovering
at that moment that none of the plumbing exists is not.

**What this phase must not do is pretend.** It does not claim Redis is needed,
does not take backups of a cache, and does not size itself for a load that does
not exist.

## 3. Measured starting state

Measured 2026-09-16 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 9, all Synced/Healthy |
| Vault | `hashicorp/vault:2.0.4`, unsealed, KV v2 at `homelab/` |
| VSO | chart 1.5.1, running; `VaultStaticSecret` CRD supports `transformation.templates` and `excludeRaw` — both verified against the live CRD |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21, `vso-config` 22, `postgres` 23 |
| PVCs | 2 (`data-vault-0` 5Gi, `data-postgres-0` 10Gi) |
| `/srv` free | 852G of 938G |
| `/backups` free | 849G of 916G |
| **Memory available** | **~10Gi of 15Gi** — ARK not running |
| `postgres-credentials` keys | 3 (`username`, `password`, `_raw`) — the known `_raw` gap, confirmed |

**Memory is no longer the binding constraint it was in phase 18** (~1.2Gi then,
~10Gi now). That is a difference in circumstance, not in policy: the sizing
below stays modest because this host is still a workstation and a game server,
and ARK reclaims ~8GB the moment it starts.

## 4. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| R1 | **Cache branch: no PVC, no persistence, no backups** | §2. `save ""`, `appendonly no`. Note the pod does carry three non-persistent volumes — config, credential, and the `/data` shadow — none of which outlive it |
| R2 | **Deployment, not StatefulSet** | No volume and no stable network identity to preserve. A StatefulSet here would be cargo-culted from phase 18 |
| R3 | Official `redis:8.2-alpine`, pinned to an exact minor | Same reasoning phase 18 applied to Postgres (P2). Bitnami is rejected for the same `:latest` reason recorded in the phase-18 spec §5 |
| R4 | Password required, generated in Vault, delivered by VSO | The cluster has no `NetworkPolicy` anywhere (known gap), so the password is the only thing in the way |
| R5 | **Password reaches Redis via a config-file `include`, never a flag or env var** | §6. `configure-vault.sh` already establishes the rule: a password on a command line is visible in `ps` |
| R6 | Tuning in a ConfigMap; only `requirepass` in the Secret | §6. Keeps knobs readable in git and the credential in Vault |
| R7 | `maxmemory 128mb` + `maxmemory-policy allkeys-lru` | §7. Without eviction, a cache under a container limit is OOM-killed instead of evicting |
| R8 | `readOnlyRootFilesystem: true`, **plus an empty `emptyDir` mounted `readOnly` at `/data`** | §7. `readOnlyRootFilesystem` covers `/` only; the image's `VOLUME /data` gets an implicit writable volume that the shadowing mount closes. Enforced by the filesystem, but **not** announced by Redis — see §7 on `BGSAVE` |
| R9 | Namespace `databases`, reusing its existing `VaultConnection` | §8 |
| R10 | Sync-wave **23**, the same wave as `postgres` | §9. Same wave means parallel, and neither gates the other |
| R11 | `excludeRaw: true` from the start | Avoids introducing a third instance of a known gap. Fixing the existing two is explicitly out of scope (§12) |
| R12 | No Ingress | The Redis wire protocol is not HTTP, the same reason phase 18 gave for Postgres |
| R13 | **Probes match `PONG\|NOAUTH`, never a bare `redis-cli ping`** | §7.1. A bare `ping` exits 0 even when it fails with `NOAUTH`, making the obvious probe one that can never fail |

## 5. Architecture

```
  configure-vault.sh  ── generates a random password inside vault-0
          │
          ▼
   Vault KV v2:  homelab/redis
          ▲
          │ read, authorised by policy vso-redis-read
          │
   Vault kubernetes auth, role vso-redis
          ▲
          │ ServiceAccount redis, namespace databases
          │
   Vault Secrets Operator
          │
          ▼ templates ONLY the line "requirepass <pw>"
   Secret redis-credentials  (namespace databases)
          │
          ▼ mounted at /etc/redis/secret/requirepass.conf
   Deployment redis ◄── ConfigMap redis.conf (tuning, ends with `include`)
          │
          ▼
   Service redis.databases.svc:6379   — no volume, no disk, no backup
```

The credential exists only in Vault and in the Secret VSO derives from it. It is
absent from the pod spec, from the process list, and from any volume that
outlives the pod — because no volume outlives the pod.

## 6. Three facts probed from the image

A throwaway probe against `redis:8.2-alpine` on 2026-09-16 established the
following. The first two are commonly assumed wrong.

**The `redis` user is uid 999, gid 1000.** Asymmetric. Phase 18 recorded the
same class of surprise from the other direction — Postgres is uid 70, gid 70,
not the 999 usually assumed. Neither image matches folklore; both were measured.

**The image ships no configuration file.** There is no
`/usr/local/etc/redis/redis.conf`. `redis-server` with no argument runs on
built-in defaults, which include **no password, no `maxmemory`, and RDB
snapshotting enabled** — every one of which this design overrides.

**Idle RSS is ~22 MiB, not the ~3 MiB Redis is famous for.** Redis 8 auto-loads
four modules (`search`, `bloom`, `timeseries`, `ReJSON`). They are not needed
here, and the image exposes no surface for switching them off: it contains no
Redis configuration file at all, and `redis-server --help` offers no module
flag. The sizing in §7 therefore accounts for them rather than wishing them
away.

### The `include` mechanism, verified end to end

`redis-server` is given exactly one argument: the path to the ConfigMap's
`redis.conf`. That file's final line is:

```
include /etc/redis/secret/requirepass.conf
```

VSO renders the Secret key `requirepass.conf` with a template whose entire
output is `requirepass <password>`.

**The `include` must stay the last line.** Redis applies directives in the
order it reads them and a later one wins, so an `include` placed mid-file could
have `requirepass` overridden by a subsequent line — silently, and in the
direction of no password. Last means nothing can follow it. Kubernetes mounts Secret keys as **symlinks
into a `..data/` directory**, not as plain files, so this design depends on
Redis's `include` following a symlink. It was verified against that exact
layout, not against a plain file:

- server reaches `Ready to accept connections tcp`
- `redis-cli ping` without the password → `NOAUTH Authentication required.`
- `redis-cli -a <password> ping` → `PONG`
- `ps -o args` inside the container → `redis-server *:6379` **only**
- `config get maxmemory maxmemory-policy` → `134217728`, `allkeys-lru`

The same probe ran with a read-only root filesystem and no writable volume,
confirming R8.

## 7. Sizing, and why eviction is the safety mechanism

`maxmemory 128mb`, `maxmemory-policy allkeys-lru`. Requests 64Mi, limit 256Mi.

The ratio matters more than either number. **`maxmemory` bounds the dataset;
RSS runs higher** — module overhead and allocator fragmentation sit on top of
it. A cache configured with a container memory limit but *no* `maxmemory` does
not start evicting as it fills; it grows until the kernel OOM-kills it, and
Kubernetes restarts it into the same trajectory. `allkeys-lru` is what converts
that crash loop into the eviction a cache is supposed to perform. The 2x gap
between 128mb and 256Mi is the headroom that keeps eviction, not the OOM killer,
as the thing that reacts first.

### 7.1 The probe must not be a bare `redis-cli ping`

Measured 2026-09-16: with `requirepass` set, `redis-cli ping` prints
`NOAUTH Authentication required.` and **exits 0**. A probe spelled
`exec: ["redis-cli", "ping"]` therefore passes unconditionally — it reports
Ready as long as the binary exists, which is close to no probe at all.

`redis-cli -e ping` does propagate the failure (exit 1), but unauthenticated it
fails *always*, so it is equally useless. Supplying the password to fix that
would put it in the probe's own command line, defeating R5 on a timer.

The probe used instead needs no password:

```sh
redis-cli ping 2>&1 | grep -qE 'PONG|NOAUTH'
```

`NOAUTH` is a *success* condition here: it proves the server is listening,
speaking the Redis protocol, and enforcing authentication. Verified to exit 0
against a healthy server and 1 against a dead port, where `redis-cli` prints
`Could not connect to Redis ...: Connection refused` and matches neither
alternative.

`save ""` and `appendonly no` disable both persistence paths. With R8's
read-only root filesystem, a manual `BGSAVE` cannot write anything — verified
2026-09-16, with the log reporting `Failed opening the temp RDB file
temp-24.rdb (in server root dir /data) for saving: Read-only file system` and
no file produced.

**But it does not report that failure to the client.** `BGSAVE` is
asynchronous: it replies `Background saving started` and forks, so the reply is
not the result. The outcome surfaces only in
`INFO persistence` → `rdb_last_bgsave_status:err`, and in the log. Anyone
checking whether this cache can be dumped by reading `BGSAVE`'s reply will
conclude that it can.

This was measured, and it corrects an earlier draft of this section that
claimed `BGSAVE` returns an error. The protection is real — nothing is
written — but it is enforced by the filesystem, not announced by Redis.

> **Corrected 2026-09-16, after deployment — this paragraph was wrong.** It
> said Kubernetes creates no implicit volume for the image's Dockerfile
> `VOLUME /data`, so `readOnlyRootFilesystem: true` alone would leave `/data`
> read-only. It does not. On this k3s/containerd runtime, `/proc/1/mountinfo`
> in the running pod shows `/data rw,noatime - ext4 /dev/sdb1`, an anonymous
> writable volume under `containerd/.../volumes/<hash>` — **invisible in the
> pod spec**. `readOnlyRootFilesystem` covers `/` only.
>
> The consequence was measured, not theorised: the first deployed Redis
> answered `BGSAVE` by writing a real 88-byte `/data/dump.rdb`. The property
> this section claimed did not hold.
>
> **The fix is an explicit `emptyDir` mounted `readOnly: true` at `/data`**,
> which shadows the implicit volume. Verified before being mandated: the pod
> stays Running/Ready and serves, `touch /data/x` returns `Read-only file
> system`, and `BGSAVE` leaves `rdb_last_bgsave_status:err` with the log line
> quoted above. R8 is amended accordingly — `readOnlyRootFilesystem` is
> necessary here but **not sufficient**, and the shadowing mount is what
> actually enforces the guarantee.
>
> Worth keeping as a general lesson: a Docker probe could not have caught
> this, and neither could reading the pod spec. `docker run --read-only`
> reproduces the writable `/data` for a different reason (Docker's own
> anonymous volume), which is why the original draft mistook it for a
> Docker-only artifact. The mount table inside the running pod was the only
> thing that would have settled it.

## 8. Namespace

`databases`, alongside PostgreSQL, rather than a new `cache` namespace.

The namespace's comment in `bootstrap/namespaces/namespaces.yaml` currently
reads "Stateful data services", and a deliberately stateless cache strains that
description — the comment is updated rather than the namespace multiplied.

Reuse buys one concrete simplification. Phase 17 established that a `VaultAuth`'s
ServiceAccount must reside in the consuming Secret's namespace, so Redis needs
its own ServiceAccount, `VaultAuth` and `VaultStaticSecret` regardless. But the
`VaultConnection` in `databases` is namespace-scoped state that already exists
and can simply be referenced. A new namespace would mean a fourth copy of six
identical lines for no gain.

## 9. Sync wave

| Wave | Application |
| --- | --- |
| 10 | `vault` |
| 21 | `vso-operator`, `ingress-config` |
| 22 | `vso-config` |
| **23** | **`postgres`, `redis`** |

Wave 23, shared with `postgres`, not 24. Redis depends on the VSO operator
(21) and on the Vault configuration the ceremony applies — it does **not**
depend on PostgreSQL. The phase-18 spec §8 warns precisely against this:
"anything added later that does not depend on Postgres belongs at a wave below
23, not above it out of habit."

Sharing a wave means the two reconcile in parallel and neither gates the other.
Nothing sits behind wave 23, so an unhealthy Redis gates nothing, and — like
`vso-config` and `postgres` — it sits after `ingress-config` (21), so a failure
here costs no tailnet URL.

## 10. The Vault extension

`configure-vault.sh` gains three steps, guarded exactly as the Postgres ones
are:

1. Seed `homelab/redis` with a generated password — **only if absent**. A re-run
   on a rebuild must never clobber a credential a running cache is using.
2. Policy `vso-redis-read` on **`homelab/data/redis`** — the `data/` segment KV
   v2 requires, which the script's existing comments already warn about.
3. Role `vso-redis`, binding `system:serviceaccount:databases:redis`, audience
   `vault`.

The password is generated from `/dev/urandom`, filtered to alphanumerics, and
written to a temp file so only the filename is ever an argument — the identical
mechanism the Postgres seeding already uses, for the identical reason.

Redis stores `requirepass` verbatim, so the alphanumeric filter is about the
connection URLs clients will build (`redis://:pw@host`), not about Redis itself.

## 11. Rotation — and why it is not the problem it was in phase 18

Redis reads its configuration **at startup only**. Rotating `homelab/redis` in
Vault updates the Secret, and the kubelet updates the mounted file, but the
running server keeps enforcing the old password. This is mechanically the same
divergence phase 18 documented for `POSTGRES_PASSWORD`.

**The consequence is not the same, and the difference is the entire argument for
the cache branch.** For PostgreSQL the divergence is dangerous: the database
holds data, the Secret and the database disagree silently, and reconciling them
requires an `ALTER USER` against a live system. For an ephemeral Redis the fix
is `kubectl delete pod` — it holds nothing, so restarting it costs nothing but a
cold cache.

So phase 18's worst limitation is, here, a one-line runbook entry. That is worth
stating in the README rather than leaving a reader to assume Redis inherited the
hard version of the problem.

(`CONFIG SET requirepass` would apply a new password to the running server
without a restart, but it is imperative state that nothing reconciles and it
would drift from the mounted file. The pod restart is the documented route.)

## 12. Out of scope

- Any consumer of the cache — nothing connects to it yet
- Persistence, AOF/RDB tuning, and therefore backups (§2, R1)
- Replication, Sentinel, Cluster — single node
- Exposure beyond the cluster (R12)
- `NetworkPolicy` — a known cluster-wide gap; it is written when the first
  consumer arrives and its traffic is known, exactly as the README argues
- **Retrofitting `excludeRaw: true` onto the two existing `VaultStaticSecret`s.**
  Redis ships with it (R11), but the existing pair is a separate cross-cutting
  change and folding it in here would mix two concerns in one phase
- Disabling the four auto-loaded Redis 8 modules — not separately controllable
  in the official image (§6)

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Password visible in `ps` or in the pod spec | **High** — and the default spelling causes it | R5/§6: config-file `include`; verified `ps` shows only `redis-server *:6379` |
| `include` fails on Kubernetes' symlinked Secret mount | High — would fail closed at startup | §6: verified against a `..data/` symlink layout, not a plain file |
| OOM-kill loop instead of eviction | **High, and self-sustaining** | R7/§7: `maxmemory` well below the container limit, `allkeys-lru` |
| Cache silently treated as durable storage | Medium | R8: the read-only filesystem plus the `/data` shadowing mount block the write. Note `BGSAVE` still *replies* `Background saving started` — §7 — so the README must point at `rdb_last_bgsave_status`, not at the reply |
| **An implicit volume defeats `readOnlyRootFilesystem`** | **Medium — invisible in the pod spec, and it shipped** | §7: the image's `VOLUME /data` became a writable ext4 mount; caught only by reading `/proc/1/mountinfo` in the running pod. Closed by the shadowing `emptyDir` |
| Rotation diverges from the running server | Low **here** | §11: documented; the fix is a pod restart, which costs nothing |
| Script re-run clobbers a live credential | High if unguarded | §10: seed only when absent — the guard both existing paths already prove |
| KV v2 policy written without `data/` | Medium — the most common KV v2 error | §10; the script's existing comments warn about it |
| **A probe that can never fail** | **High — it looks correct and hides every fault** | R13/§7.1: match `PONG\|NOAUTH`; verified to fail against a dead port |
| Unpinned image changing major version | Eliminated | R3 — exact minor pinned |
| Wave stacked above 23 out of habit | Low, but compounding | R10/§9: same wave as `postgres`, per the phase-18 spec's own warning |

## 14. Verification

The phase is complete when all hold:

1. `redis` Application Synced/Healthy, and its `status.resources` lists the
   Deployment, Service, ConfigMap and all three Vault objects — not merely a
   green Application.
2. The pod is Running and Ready, with **no PVC created** in `databases` —
   `kubectl get pvc -n databases` still shows only `data-postgres-0`.
3. A throwaway pod, given the password from Secret `redis-credentials`,
   authenticates over the Service and runs `SET`/`GET`. **A separate pod
   matters**: `redis-cli` from inside the Redis pod could connect over
   localhost and prove nothing about the credential path — the same reasoning
   phase 18 applied to `psql`.
4. The same throwaway pod **without** the password is refused with `NOAUTH`.
   A password that is set but not enforced is the failure this checks for.
5. `ps` inside the running pod shows no password. **Read the full `ps`
   output rather than grepping for the password** — a `grep <password>` places
   that password in its own argv, so `ps` matches the grep itself and reports a
   leak that does not exist. This was hit while probing the design.
6. The liveness probe **fails** when Redis is not answering — confirmed against
   a dead port, not assumed from the fact that it passes when healthy.
7. `config get maxmemory maxmemory-policy` returns `134217728` and
   `allkeys-lru`, read from the live pod rather than from the ConfigMap.
8. `redis-credentials` contains **no `_raw` key** — R11 verified, not assumed.
9. Data does **not** survive a pod deletion, and the pod returns Ready with an
   empty keyspace. This is the one phase where that is the passing result.
10. `BGSAVE` writes nothing: `INFO persistence` reports
    `rdb_last_bgsave_status:err`. **Do not assert on `BGSAVE`'s reply** — it
    answers `Background saving started` regardless, because it is asynchronous.
11. `configure-vault.sh` re-run leaves the existing password unchanged.
12. All Applications Synced/Healthy; both tailnet URLs 200.
13. Memory measured before and after and recorded, not estimated.
