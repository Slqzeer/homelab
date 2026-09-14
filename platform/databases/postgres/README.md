# PostgreSQL

PostgreSQL 18.6, one StatefulSet, one PVC, no operator, in-cluster only —
the cluster's first stateful workload. No Ingress: the wire protocol is not
HTTP and does not belong behind the tailnet proxy.

**No Bitnami chart.** `bitnami/postgresql` renders
`registry-1.docker.io/bitnami/postgresql:latest` — Bitnami moved versioned
images to a separate, vendor-designated-legacy registry in 2025 and left
`latest` as the free default. Every other image in this cluster is pinned;
for a stateless proxy an unpinned tag is sloppy, but for a database it is
dangerous. A pod restart can pull a new **major** version — and because this
image's `PGDATA` is version-namespaced (`/var/lib/postgresql/18/docker`, see
`config/postgres.yaml`'s volume mount comment), the new major does
not even fail to start: it finds no `PG_VERSION` at its own path, runs
`initdb` there, and comes up Ready, healthy, Synced — and empty, with
`POSTGRES_PASSWORD` applied to the new, empty cluster so authentication
works too. Nothing errors. The old data is still on disk, untouched, at the
old version's path — which is also the way back: reverting the image tag
recovers everything. There is no major-version upgrade procedure
(`pg_upgrade`, or dump/restore) in this repository yet; writing one is a
later phase. See
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
there is no other path. `\password` is the way to do it:

    sg k3s-admin -c 'kubectl -n databases exec -it postgres-0 -- psql -U postgres'

then, at the prompt:

    \password postgres

`\password` is a `psql` meta-command, not SQL — confirmed present on this
image's `psql` (`\?` lists it as "securely change the password for a
user"). It prompts twice with echo off, hashes the value client-side, and
sends the server only the resulting SCRAM verifier via `ALTER USER` under
the hood. The plaintext never becomes a command argument (readable in
`ps`), never reaches `~/.psql_history` — which on this pod is
`/var/lib/postgresql/.psql_history`, on the PVC, surviving restarts
indefinitely — and never appears in a log line if mistyped. Use whatever
was just written to `homelab/postgres` in Vault; doing this before VSO's
next `refreshAfter` poll lands is what prevents the divergence window
described above.

**Do not use the SQL form instead** —
`ALTER USER postgres WITH PASSWORD 'PASTE_NEW_PASSWORD_HERE';` typed
directly at the prompt, pasting the new value in place of the placeholder —
except as a last resort. It writes the plaintext into
`~/.psql_history` on the database's own PVC, permanently, and a mistyped
statement (unbalanced quote, wrong role name) logs the full statement text,
password included, to the container log via `log_min_error_statement =
error`, readable with `kubectl logs` by anyone or anything that ships logs
later. `\password` exists specifically to avoid both.

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

    mkdir -p /backups/databases/postgresql
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
already records one documented *verification method* that could never have
worked — see `docs/troubleshooting.md` entry 6, on testing the Argo CD
deploy key by exec'ing into repo-server, a check that fails regardless of
whether the credential is correct. Don't repeat that shape here — after
taking a dump, look inside it:

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

**After restoring, re-assert the password.** The dump contains one
SCRAM-SHA-256 verifier — the `postgres` superuser's, emitted by
`pg_dumpall` as `ALTER ROLE postgres … WITH PASSWORD 'SCRAM-SHA-256$…'` (see
"What else is in the dump" above) — and replaying the dump through `psql`
replays that `ALTER ROLE` along with everything else. If the cluster being
restored into was initialised against a *newer* Vault credential than the
one current when the dump was taken, the restore silently reverts the
database's password to the old one, and the Secret and the database
disagree again — the exact divergence this file opens by calling "the
single most important thing in this file," now arriving by way of its own
recovery procedure. Immediately after a restore, run `\password postgres`
against the current `homelab/postgres` value (see the rotation section
above), or take the dump in the first place with
`pg_dumpall -U postgres --no-role-passwords` when it will only ever be
replayed into a cluster that gets its password from Vault regardless.

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

Measured **at first start**, 2026-09-06, minutes after `initdb` and before
`shared_buffers` had actually been touched, against the same host's
"before" figures captured just before this phase started:

| Pod | CPU | Memory |
| --- | --- | --- |
| `postgres-0` | 7m | 20Mi |

That first-start figure understates steady state. Measured again
2026-09-14, one week into normal (light) use: `postgres-0` at 3m / 41Mi —
roughly double the memory, because `shared_buffers=64MB` is actually
allocated by then. Use the later figure, not the first-start one, when
sizing a second database off this component's footprint.

Both are trivially inside its 192Mi/50m requests and 512Mi limit, so
nothing here indicates a problem — only that the first measurement was
taken too early to be a steady-state baseline. Host-level, 2026-09-06 only:
pod count went from 19 to 20 (+1, exactly `postgres-0`), available memory
from 1.1Gi to 2.1Gi, buff/cache from 1.4Gi to 2.4Gi. That memory swing is
larger than one 192Mi-request pod can account for; host memory moved for
reasons this measurement does not isolate, so the before/after host
comparison is not attributable to this component either way — only the pod
row above is.
