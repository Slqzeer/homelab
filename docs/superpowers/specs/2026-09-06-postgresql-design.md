# PostgreSQL — Design

Covers workstation-plan phase 18. This is the cluster's first stateful
application and the first real consumer of the Vault path phase 17 built.

## 1. Goal

A running, persistent PostgreSQL whose credential is generated inside Vault,
delivered by the Vault Secrets Operator, and never seen by a human or committed
to git — with a verified `pg_dump` procedure into `/backups`.

The phase is finished when data survives a pod deletion, a client authenticates
with the Vault-delivered password, and a dump has actually been taken and its
contents inspected.

## 2. Measured starting state

Measured 2026-09-06 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Vault | 2.0.4, unsealed, KV v2 at `homelab/`, `kubernetes` auth enabled |
| VSO | chart 1.5.1, running, 47Mi; canary path proven |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21, `vso-config` 22 |
| Applications | 8, all Synced/Healthy |
| PVs | 1 (`data-vault-0`, 5Gi) |
| `/srv` free | **852G of 938G** |
| `/backups` free | **850G of 916G**, mode 0777, `/backups/databases` exists |
| **Memory available** | **~1.2Gi of 15Gi — ARK running at 7.95GB, swap exhausted** |
| Cluster-only secrets | 3 |

**Storage is abundant; memory is the binding constraint.** PostgreSQL's defaults
assume a dedicated machine. This one is also a workstation and a game host.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| P1 | **Plain StatefulSet, official `postgres:18.6-alpine`** | §4. The Bitnami chart resolves to `bitnami/postgresql:latest` — see §5 |
| P2 | Image pinned to an exact minor | A `latest` tag on a database can change the **major** version across a restart, and PostgreSQL refuses to start on a data directory from another major |
| P3 | No operator | CloudNativePG's failover is meaningless on one node and its PITR exceeds what the roadmap asks; the operator costs memory that is scarce |
| P4 | Static password in Vault KV, delivered by VSO | User's explicit choice. Extends the proven phase-17 path with one KV path, one policy, one role |
| P5 | The password is **generated in-pod, never displayed** | No human types or sees it; it exists only in Vault and in the Secret VSO derives |
| P6 | Alphanumeric password only | A `/` or `@` in a password breaks connection URLs in ways that surface far from the cause |
| P7 | Sync-wave **23** | It cannot start before the Secret exists, and that Secret comes from `vso-config` at 22 |
| P8 | PVC mounted at `/var/lib/postgresql` | §6. Makes `PGDATA` a subdirectory automatically, which `initdb` requires |
| P9 | `runAsUser: 70`, `fsGroup: 70` | §6. Verified from the image; avoids a root container |
| P10 | Backups are `pg_dumpall` to `/backups`, run by hand | Phase 27 schedules them. This phase proves the procedure works |
| P11 | No `/dev/shm` sizing yet | §11. Nothing queries this database; adding it now is complexity for a load that does not exist |

## 4. Architecture

```
  configure-vault.sh  ── generates a random password inside vault-0
          │
          ▼
   Vault KV v2:  homelab/postgres
          ▲
          │ read, authorised by policy vso-postgres-read
          │
   Vault kubernetes auth, role vso-postgres
          ▲
          │ ServiceAccount postgres, namespace databases
          │
   Vault Secrets Operator
          │
          ▼ writes
   Secret postgres-credentials  (namespace databases)
          │
          ▼ env POSTGRES_PASSWORD
   StatefulSet postgres ──► PVC ──► /srv/kubernetes/storage
```

The credential never exists outside Vault and the Secret VSO derives from it.

## 5. Why not the Bitnami chart

`bitnami/postgresql` chart 18.8.17 renders `image: registry-1.docker.io/bitnami/postgresql:latest`. Bitnami moved versioned Debian
images to a separate `bitnamilegacy` registry in 2025, leaving `latest` as the
free default.

Every image in this cluster is pinned. For a stateless proxy an unpinned tag
would be sloppy; for a database it is dangerous, because a pod restart can pull
a new **major** version and PostgreSQL will not start on a data directory
written by a different major. The failure arrives at restart, not at deploy.

Overriding the image to a `bitnamilegacy` tag would work but fights the chart's
own direction and depends on a registry its vendor has designated legacy.

## 6. The manifests, and three facts verified from the image

A throwaway probe (`kubectl run --rm`) against `postgres:18.6-alpine` on
2026-09-06 established three things this design depends on. All three are
commonly assumed wrong.

**`PGDATA` is `/var/lib/postgresql/18/docker`.** PostgreSQL 18's official image
changed the layout; it is no longer `/var/lib/postgresql/data`. Mounting the PVC
at **`/var/lib/postgresql`** therefore leaves `PGDATA` a *subdirectory* of the
mount, which is exactly what `initdb` needs — it refuses to initialise into a
non-empty directory, and a freshly provisioned volume is not reliably empty.
No `PGDATA` override is required; the default is already correct given this
mount point.

**The `postgres` user is uid 70, gid 70**, not the 999 usually assumed.

**The image's entrypoint starts as root** and drops privileges itself. Setting
`runAsUser: 70`, `runAsGroup: 70`, `fsGroup: 70`, `runAsNonRoot: true` makes it
skip the root path and run unprivileged from the start; `fsGroup` makes the
volume writable, the mechanism phase 16 proved on this cluster.

**Sizing:** `shared_buffers` 64MB and `max_connections` 50, both below defaults;
requests 192Mi, limit 512Mi. Deliberately modest against ~1.2Gi available.

**Service:** ClusterIP on 5432, in-cluster only. No Ingress — exposing a
database on the tailnet is not something this phase needs, and a Postgres wire
protocol behind an HTTP proxy would not work anyway.

## 7. Repository layout

**New:**

| Path | Contents |
| --- | --- |
| `platform/databases/postgres/config/postgres.yaml` | Service, StatefulSet |
| `platform/databases/postgres/config/vault-secrets.yaml` | ServiceAccount, VaultConnection, VaultAuth, VaultStaticSecret |
| `platform/databases/postgres/README.md` | Connecting, backups, restore, the rotation limitation |
| `environments/homelab/apps/postgres.yaml` | Application, sync-wave `23` |

**Modified:** `bootstrap/namespaces/namespaces.yaml` (add `databases`);
`platform/vault/configure-vault.sh` (extended, not replaced); `README.md`;
`docs/troubleshooting.md`.

**The VSO objects live with Postgres, not with VSO.** That is forced, not
stylistic: phase 17 established that `VaultAuth`'s ServiceAccount must reside in
the consuming secret's namespace, so `databases` needs its own ServiceAccount,
`VaultAuth` and `VaultStaticSecret`. It gets its own `VaultConnection` too,
rather than referencing across namespaces — six lines, self-contained, and no
cross-namespace resolution semantics to verify.

## 8. Sync waves

| Wave | Application |
| --- | --- |
| 10 | `vault` |
| 21 | `vso-operator`, `ingress-config` |
| 22 | `vso-config` |
| **23** | **`postgres`** |

Wave 23 because Postgres cannot start without `postgres-credentials`, which VSO
creates at 22. On a rebuild the database therefore comes up last, after the
Vault ceremony — correct, since its credential does not exist until then.

Nothing sits behind Postgres, so an unhealthy database gates nothing. That is
the same property wave 22 was chosen for and it should be preserved: **anything
added later that does not depend on Postgres belongs at a wave below 23**, not
above it out of habit.

## 9. The Vault extension

`configure-vault.sh` gains four steps, all guarded the same way the existing
ones are:

1. Seed `homelab/postgres` with a generated password — **only if absent**, the
   same guard the canary uses. A re-run on a rebuild must never clobber the
   credential a live database is already using.
2. Policy `vso-postgres-read`, granting `read` on **`homelab/data/postgres`** —
   the `data/` segment that KV v2 requires.
3. Role `vso-postgres`, binding `system:serviceaccount:databases:postgres` with
   audience `vault`.
4. Nothing else. The script stays one ceremony with one purpose.

The password is generated from `/dev/urandom`, filtered to alphanumerics (P6),
and never echoed.

## 10. What static credentials cannot do

**`POSTGRES_PASSWORD` is only read at `initdb`.** Once the database exists,
changing the value in Vault updates the Kubernetes Secret but **not** the
database. The two diverge silently, and the next client to pick up the new
Secret fails to authenticate against the old password.

This is a real limitation of the chosen approach, not an oversight, and it must
be stated in `platform/databases/postgres/README.md` rather than discovered.
Genuine rotation requires an `ALTER USER` against the running database, which is
where Vault's database secrets engine would eventually earn its place — it mints
short-lived users and rotates them properly. That is a later phase, and only
once something actually connects.

## 11. Out of scope

- Any consumer of the database — nothing connects to it yet
- Vault dynamic database credentials (§10)
- Scheduled backups — phase 27; this phase proves the manual procedure
- Replication, failover, connection pooling — single node
- Exposure beyond the cluster
- `/dev/shm` sizing for parallel query workers (P11). Kubernetes gives it 64MB
  by default, which constrains parallel workers under real load. The standard
  fix is a memory-backed `emptyDir`, and it is the first thing to revisit when a
  workload arrives — but adding it for a database nothing queries is complexity
  without a cause
- Tuning beyond `shared_buffers` and `max_connections`

## 12. Backups

    kubectl -n databases exec postgres-0 -- pg_dumpall -U postgres | gzip > /backups/databases/postgresql/all-YYYYMMDD.sql.gz

`/backups` is mode 0777 on its own ext4 disk, so no `sudo` is needed — the same
finding that simplified phase 16's snapshot procedure.

**The roadmap's warning is worth restating in the docs: never copy a live data
directory.** A `pg_dump` is internally consistent; a filesystem copy of a
running PostgreSQL is not, and the failure appears at restore time, when it is
too late to take a better backup.

Whether `pg_dumpall` needs a password over the local socket depends on the
image's `pg_hba.conf`; implementation must confirm rather than assume, and
document whichever is true.

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **`initdb` fails on a non-empty volume** | High if the mount point is wrong | §6 — mount at `/var/lib/postgresql` so `PGDATA` is a subdirectory |
| Password rotated in Vault, database unchanged | **High, and silent** | §10 — documented explicitly as a limitation of static credentials |
| Memory pressure during `initdb` or a dump | Medium | §6 sizing; ~1.2Gi available, and VSO has already lost a leader-election lease under this load |
| Script re-run clobbers a live credential | High if unguarded | §9 — seed only when absent, the guard the canary already proves |
| KV v2 policy written without `data/` | Medium — the most common KV v2 error | §9; the existing script comments already warn about it |
| Backup taken as a directory copy | High at restore time | §12 states it; the roadmap warns of it independently |
| An unpinned image changing major version | **Eliminated** | P2 — exact minor pinned, which is why the Bitnami chart was rejected |

## 14. Verification

The phase is complete when all hold:

1. `postgres` Application Synced/Healthy, and its `status.resources` lists the
   StatefulSet, Service and all four Vault objects — not merely a green
   Application.
2. `postgres-0` is Running and Ready; the PVC is Bound with its directory under
   `/srv/kubernetes/storage`.
3. A client authenticates using the password from the Vault-delivered Secret.
   Specifically: a throwaway `psql` pod, given `PGPASSWORD` from Secret
   `postgres-credentials`, connects over the Service and runs a query. Using a
   *separate* pod matters — connecting from inside `postgres-0` could succeed
   over the local socket without the password ever being checked, which would
   prove nothing about the credential path.
4. **Data survives**: create a table, insert a row, delete the pod, and read the
   row back after it returns.
5. `pg_dumpall` produces a non-empty gzip in `/backups/databases/postgresql/`,
   and its decompressed contents contain the test table — verified by looking,
   not by checking the file exists.
6. `configure-vault.sh` re-run leaves the existing password unchanged.
7. All Applications Synced/Healthy; both tailnet URLs 200; the Argo CD Ingress
   not recreated.
8. Memory measured before and after and recorded, not estimated.
