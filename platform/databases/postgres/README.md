# PostgreSQL

PostgreSQL 18.6, one StatefulSet, one PVC, no operator, in-cluster only —
the cluster's first stateful workload. No Ingress: the wire protocol is not
HTTP and does not belong behind the tailnet proxy.

**No Bitnami chart.** `bitnami/postgresql` renders
`registry-1.docker.io/bitnami/postgresql:latest` — Bitnami moved versioned
images to a separate, vendor-designated-legacy registry in 2025 and left
`latest` as the free default. Every other image in this cluster is pinned;
for a stateless proxy an unpinned tag is sloppy, but for a database it is
dangerous. A pod restart can pull a new **major** version, and PostgreSQL
refuses to start on a data directory written by a different major — the
failure arrives at restart, not at deploy, long after whoever picked the
chart has moved on. See
`docs/superpowers/specs/2026-09-06-postgresql-design.md` §5.

## The rotation limitation — read this before rotating anything

**The single most important thing in this file.** `POSTGRES_PASSWORD` is
read only once, at `initdb`. Once the database exists, changing the value
in Vault (`homelab/postgres`) updates the Kubernetes Secret
`postgres-credentials` — VSO still does its job — but it does **not**
update the database. The two diverge silently: nothing errors, nothing
warns, `vaultstaticsecret` still reports `Healthy`. The first sign is the
*next* client that picks up the new Secret failing to authenticate against
the password the database was actually initialised with, which looks
exactly like a broken credential and is not. See
`docs/troubleshooting.md` entry 11 for that exact symptom.

Real rotation needs an `ALTER USER` run against the running database —
there is no other path:

    sg k3s-admin -c 'kubectl -n databases exec -it postgres-0 -- psql -U postgres'

then, at the prompt, paste the new value in place of the placeholder —
the same way `platform/vault/README.md`'s init ceremony handles the unseal
keys, and for the same reason: this must never sit in shell history or a
script file:

    ALTER USER postgres WITH PASSWORD 'PASTE_NEW_PASSWORD_HERE';

The value to paste is whatever was just written to `homelab/postgres` in
Vault — the same one VSO is about to propagate into `postgres-credentials`
on its next `refreshAfter` poll. Doing the `ALTER USER` first, before that
poll lands, is what prevents the divergence window described above.

This is a real limitation of static, Vault-generated credentials, not an
oversight — the design spec calls it out explicitly (§10) rather than
letting it be discovered. Genuine rotation without a human running
`ALTER USER` by hand needs Vault's database secrets engine, which mints and
expires its own short-lived roles. That is a later phase, and only once
something actually depends on this database (design spec §11).

## Connecting

Everything below connects at `postgres.databases.svc:5432`. The password
lives only in Vault and in Secret `postgres-credentials`; it must never
become a command argument (readable in `ps`) or reach a terminal.

A throwaway pod takes the password from the Secret the same way the real
workload does — via `secretKeyRef` in the pod spec, never as a literal
value anywhere on a command line:

    sg k3s-admin -c 'kubectl -n databases run pgclient --restart=Never --image=postgres:18.6-alpine --overrides="{\"spec\":{\"containers\":[{\"name\":\"pgclient\",\"image\":\"postgres:18.6-alpine\",\"command\":[\"psql\",\"-h\",\"postgres.databases.svc\",\"-U\",\"postgres\",\"-c\",\"select version();\"],\"env\":[{\"name\":\"PGPASSWORD\",\"valueFrom\":{\"secretKeyRef\":{\"name\":\"postgres-credentials\",\"key\":\"password\"}}}]}]}}"'
    sg k3s-admin -c 'kubectl -n databases wait --for=jsonpath={.status.phase}=Succeeded pod/pgclient --timeout=60s'
    sg k3s-admin -c 'kubectl -n databases logs pgclient'
    sg k3s-admin -c 'kubectl -n databases delete pod pgclient'

Verified 2026-09-07 — the logs read:

    PostgreSQL 18.6 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit

Replace the SQL after `-c` for a different one-off query. This is the same
route `docs/superpowers/plans/2026-09-06-postgresql.md`'s Task 3 used to
prove the credential path end to end: a pod entirely separate from
`postgres-0`, authenticating over the network, not the local socket.

For a quick check from anyone who already has cluster access, `postgres-0`'s
own local socket authenticates via `trust` (see Backups, below), so no
credential is needed at all:

    sg k3s-admin -c 'kubectl -n databases exec -it postgres-0 -- psql -U postgres'

That shortcut only works from inside the pod, over the Unix socket.
Anything arriving over the network — including the throwaway pod above —
hits `pg_hba.conf`'s `scram-sha-256` catch-all and needs the real
credential.

## Backups

Manual only. This phase proves the procedure; scheduled automation is a
later phase (`docs/workstation-plan.md` §33). Destination is
`/backups/databases/postgresql`, per the roadmap's phase-18 section (§21).

    sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- pg_dumpall -U postgres' | gzip > /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz

**No password required.** This connects over the pod's local Unix socket,
which `pg_hba.conf`'s `local all all trust` line covers unconditionally.
The `scram-sha-256` catch-all further down that file governs everything
that is not the local socket or loopback TCP — a boundary confirmed by
reading the live file on 2026-09-06 and again on 2026-09-07, not assumed
from the presence of `trust` alone.

Measured size: 1035 bytes on 2026-09-06, 1034 bytes reproduced on
2026-09-07 — the `phase18` table's one row does not grow, so the dump does
not either. Both are gzip-compressed plain SQL, restorable with `psql`, not
`pg_restore` (that tool is for the custom/directory archive formats;
`pg_dumpall` only ever produces plain SQL).

**Never copy a live data directory instead** — restated from the roadmap's
own warning (§21, "ne pas simplement copier un répertoire PostgreSQL
actif"). A `pg_dump`/`pg_dumpall` is a consistent snapshot from inside the
engine; a filesystem copy of a running database's PGDATA is not, because
files can be mid-write across the copy. The failure from that mistake does
not appear when the copy is taken — it appears at restore time, against
data nobody can go back and re-copy correctly.

**A backup nobody has opened is not a verified backup.** This repository
already has one documented backup method for a different component that
turned out never to have been checked for actual contents. Don't repeat
that here — after taking a dump, look inside it:

    zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -c "CREATE TABLE public.phase18"
    zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -A3 "COPY public.phase18"

Expect `1` for the first, and the row itself for the second — the table
definition and the actual data, not merely a non-empty file. Both verified
2026-09-07, identical to the 2026-09-06 dump's contents.

### What else is in the dump

One password hash — the SCRAM-SHA-256 verifier for the `postgres`
superuser, the same credential VSO delivers into the cluster:

    zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -icE "PASSWORD 'SCRAM|PASSWORD 'md5"

Two things are both true about this, and neither alone is the right way to
read it:

- **The exposure is real and already live.** `/backups` is mode 0777 with
  `/backups/databases` at 3775 and `/backups/databases/postgresql` at 1775;
  the dump file itself is 0664. Any local account on this host can already
  read it and extract the verifier today. That is a genuine, low-effort
  finding worth fixing — by tightening permissions on the backup tree, or
  by knowingly accepting the risk — independent of anything below.
- **It is not an imminent-compromise exposure.** The Vault-generated
  password is 32 random alphanumeric characters, roughly 190 bits of
  entropy. Offline recovery of the plaintext from a SCRAM verifier at that
  entropy is not realistically feasible with any credible computing
  budget. This is a handling-hygiene and blast-radius concern, not a
  password that is about to be cracked.

Neither half excuses the other: the file being world-readable is worth
fixing on its own terms, and the password's entropy is why it is not
worth panicking over today.

## Restore

**Status: not exercised.** Testing it means destroying the live database,
which this phase does not do. What follows is transcribed from
PostgreSQL's own documentation on restoring a `pg_dumpall` output
(plain SQL, piped through `psql`, not `pg_restore`) — a starting point, not
a rehearsed procedure:

    zcat /backups/databases/postgresql/all-20260907.sql.gz | sg k3s-admin -c 'kubectl -n databases exec -i postgres-0 -- psql -U postgres'

(substitute whichever dated file is the one being restored)

`pg_dumpall`'s plain-SQL output is designed to be replayed exactly this
way — it recreates roles, databases, and every object in them from scratch,
which is also why restoring into a database that already has conflicting
objects (including the very `phase18` table this phase created) produces
errors on every object that already exists rather than silently overwriting
it. A real restore targets an empty, freshly initialised database — the
same `postgres-0` this file was dumped from, still holding `phase18`, is
deliberately not the place to test this.

## The three strings that must agree

Role, ServiceAccount and audience must be identical across
`platform/vault/configure-vault.sh` and this component's own
`config/vault-secrets.yaml`:

| String | Value |
| --- | --- |
| Role | `vso-postgres` |
| ServiceAccount | `postgres` |
| Audience | `vault` |

A mismatch in any one of them is a permission denial that names neither
side, while Argo CD reports everything Synced. See
`platform/vault-secrets-operator/README.md` for why this shape is forced —
`VaultAuth` requires its ServiceAccount to live in the consuming Secret's
own namespace, so `databases` carries a full second copy of the objects
`vault` already has for the canary, under these names instead.

## Measured resource usage

Measured 2026-09-06, against the same host's "before" figures captured
just before this phase started:

| Pod | CPU | Memory |
| --- | --- | --- |
| `postgres-0` | 7m | 20Mi |

Well within its 192Mi/50m requests and 512Mi limit. Host-level: pod count
went from 19 to 20 (+1, exactly `postgres-0`), available memory from 1.1Gi
to 2.1Gi, buff/cache from 1.4Gi to 2.4Gi — no sign of memory pressure
attributable to this component.
