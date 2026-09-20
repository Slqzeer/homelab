# Redis

Redis 8.2 (`redis:8.2-alpine`, Redis 8.2.9), one Deployment, no PVC, no
persistence, no backups — a cache, not a database. No Ingress: the wire
protocol is not HTTP and does not belong behind the tailnet proxy.

## What this is, and what it deliberately is not

The roadmap always had two branches for this phase, and this is the cheaper
one: a cache that costs nothing to lose. There is no `volumeClaimTemplate`,
no `PersistentVolumeClaim` anywhere in `config/`, `save ""` and
`appendonly no` disable both of Redis's own on-disk paths, and the pod's
`/data` mount (see "BGSAVE writes nothing" below) closes the one path that
would otherwise still exist. The guarantee this adds up to is that
**nothing outlives the pod**, not that nothing is ever written anywhere: a
client holding the password could still `CONFIG SET dir /dev/shm` and make
`BGSAVE` succeed there, because `/dev/shm` and `/dev` are writable tmpfs
even with the rest of the filesystem read-only — but that write is memory,
dies with the pod exactly like the keyspace itself, and does not reopen the
durability question. A restart — deliberate, OOM, node reboot, whatever —
throws the whole keyspace away and starts empty. That is the design, not a
gap: nothing in this cluster yet depends on Redis holding anything across a
restart, and the moment something does, that is a different phase with a
different Deployment, most likely PostgreSQL's branch of the same roadmap
fork (a StatefulSet with a PVC) rather than this one grown a persistence
flag.

Because of that, this file documents no backup procedure. There is nothing
to back up by design, and writing a procedure anyway would imply a
durability guarantee this component does not have.

## Connecting

Since phase 25, the namespace's default-deny ingress policy blocks a
separate client pod unless an explicit allow rule matches it. Redis still
has no consumer besides its exporter, so `config/networkpolicy.yaml`
contains exactly two narrow policies: `redis-clients` permits local pods
labelled `app: redis-exporter` to reach Redis on 6379, and
`redis-exporter-metrics` permits the `monitoring` namespace to scrape the
exporter on 9121. The default-deny itself belongs to PostgreSQL, the
namespace's anchor. Egress is unchanged.

The next real consumer must add its namespace/pod selector to
`redis-clients` as well as obtaining the credential; possession of the
password no longer grants network reachability. The separate-pod recipe
below documents safe credential delivery, but **is now denied by policy**
until such an allow rule is deliberately added. For today's diagnostic
ping, use the in-pod command below. Live acceptance on 2026-09-20 kept
`redis_up=1` and `pg_up=1` while denying an unauthorized database client.

The Service is a normal ClusterIP, `redis.databases.svc:6379` — unlike
PostgreSQL's headless Service, this Deployment has no stable network
identity worth preserving. The ACL user is `default`; `requirepass` sets
the password for exactly that user, so a client URL looks like
`redis://:PASSWORD@redis.databases.svc:6379` with the username left out
(or `default` if the client insists on one).

From a separate pod, never use `-a` — it puts the password on the command
line, visible to anything that can read `ps` on the node for as long as the
process runs. But `REDISCLI_AUTH` set from a local `kubectl get secret ...`
substitution is **not** the safe alternative it looks like: that resolves
the password on this host, as argv of the local `kubectl` process (visible
to `ps` here), and hands it to `kubectl run` as a literal value, which lands
in the created Pod's `spec.containers[].env` — readable from etcd, from
`kubectl get pod -o yaml`, from the Argo CD resource view, and from the
audit log. That is strictly worse than the `-a` flag it was meant to avoid:
`-a` at least confines the leak to this node's `ps`.

The actual safe form uses `--overrides` with `valueFrom.secretKeyRef`, so
the kubelet resolves the password from the Secret when the container
starts and it never passes through this host or through the Pod spec as a
plain value:

    cat > /tmp/redis-ping.sh <<'EOF'
    kubectl -n databases run redis-client --rm -i --restart=Never --image=redis:8.2-alpine --overrides='{"spec":{"containers":[{"name":"redis-client","image":"redis:8.2-alpine","stdin":true,"command":["redis-cli","-h","redis.databases.svc","-p","6379","ping"],"env":[{"name":"REDISCLI_AUTH","valueFrom":{"secretKeyRef":{"name":"redis-credentials","key":"password"}}}]}]}}'
    EOF
    sg k3s-admin -c 'sh /tmp/redis-ping.sh'

Writing the command to a file first and having `sg` run the file sidesteps
the quoting collision between the outer `sg k3s-admin -c '...'` and the
JSON `--overrides` value's own quotes; the heredoc body above is
deliberately one long line, not wrapped, because a line a human is meant to
copy must never depend on a trailing backslash surviving the paste. This
returned `PONG` on 2026-09-16, before the phase-25 network fence; do not use
that historical result as evidence that this pod is allowed today.

**`requirepass` applies to loopback as well.** Unlike PostgreSQL, which
trusts its own Unix socket unconditionally (see
`platform/databases/postgres/README.md`'s Connecting section), Redis has no
local-socket exemption of that kind — even `kubectl exec` into the pod
itself needs the password. The in-pod form reads it straight from the
mounted credential file instead of typing it:

    sg k3s-admin -c 'kubectl -n databases exec deploy/redis -- sh -c '\''REDISCLI_AUTH=$(sed -n "s/^requirepass //p" /etc/redis/secret/requirepass.conf) redis-cli ping'\'''

That command is not a suggestion to memorize — it is the one every other
in-pod example in this file and in `docs/troubleshooting.md` reuses.

## Rotation, and why it is easy here

Redis reads its configuration once, at startup. Rotating `homelab/redis`
in Vault updates `redis-credentials` and the mounted
`/etc/redis/secret/requirepass.conf` file — VSO still does its job — but
the running `redis-server` process keeps the old password until it is
restarted. Clients using the newly-rotated Secret get `NOAUTH` (see
"Why the probe is not `redis-cli ping`" below for why that is a
distinguishable failure and not a false positive). The fix is one line:

    sg k3s-admin -c 'kubectl -n databases rollout restart deploy/redis'

`platform/databases/postgres/README.md` documents the identical
startup-only-read mechanism for `POSTGRES_PASSWORD` and calls it "the
single most important thing in this file" — a hard limitation, because
`\password` against a live database is the only real fix and getting it
wrong risks logging the plaintext. Here the same mechanism costs a cold
cache and nothing else: `rollout restart` is the actual fix, not a
workaround, because there is no data a restart could lose that the design
does not already expect to lose on any restart. The contrast is the point —
identical cause, opposite stakes, because of what each component is asked
to hold.

## The same applies to the ConfigMap

`redis.conf` (in `redis-config`, this component's ConfigMap) is read the
same way — at startup only. Editing it and pushing updates the ConfigMap
object immediately, and Argo CD reports `Synced`/`Healthy` throughout, but
the running server keeps whatever settings it started with until the pod
is recreated. A `maxmemory` change that looks live in git is not live in
the process until:

    sg k3s-admin -c 'kubectl -n databases rollout restart deploy/redis'

Nothing in Argo CD's health check for this Deployment reads Redis's own
runtime configuration, so there is no signal anywhere that the ConfigMap
and the running server have diverged — the divergence is silent by
construction, not a bug in the health check.

## `BGSAVE` writes nothing — but says it started

**`readOnlyRootFilesystem: true` was not enough on its own**, and this is
worth being precise about because the gap was live on this cluster before
it was found. That setting covers `/` only. The `redis:8.2-alpine` image's
own Dockerfile declares `VOLUME /data`, and containerd honours that
declaration by creating an anonymous **writable** volume at `/data` —
invisible in the pod spec, in `kubectl get pod -o yaml`, and in a
`docker run` test of the image alone. The first Redis deployed here
answered `BGSAVE` by actually writing an 88-byte `/data/dump.rdb` — a file
that would have outlived the pod's own restart, quietly defeating the
"nothing outlives the pod" guarantee while the manifest looked correct end
to end.

The fix is the explicit `nodata` volume in `config/redis.yaml`: an
`emptyDir` mounted `readOnly: true` at `/data`, which shadows the implicit
one the image would otherwise get. After it, the mount table inside the
running pod shows the difference plainly:

    sg k3s-admin -c 'kubectl -n databases exec deploy/redis -- cat /proc/1/mountinfo'

`/data` now reads `... ro,relatime ...` against
`kubernetes.io~empty-dir/nodata`, and:

    sg k3s-admin -c 'kubectl -n databases exec deploy/redis -- touch /data/x'

returns `Read-only file system`. `BGSAVE` itself now leaves
`rdb_last_bgsave_status:err`, and the pod log records the refusal:

    75:C 16 Sep 2026 16:18:28.280 # Failed opening the temp RDB file temp-75.rdb (in server root dir /data) for saving: Read-only file system

Two things follow from this that are easy to get backwards:

1. **`BGSAVE` still replies `Background saving started` even when it is
   about to fail.** The command is asynchronous by design — the reply
   only means the fork happened, not that the save succeeded. Reading the
   reply as the result reaches the wrong conclusion every time; the real
   answer is `INFO persistence`'s `rdb_last_bgsave_status` field:

       sg k3s-admin -c 'kubectl -n databases exec deploy/redis -- sh -c '\''REDISCLI_AUTH=$(sed -n "s/^requirepass //p" /etc/redis/secret/requirepass.conf) redis-cli bgsave; sleep 1; REDISCLI_AUTH=$(sed -n "s/^requirepass //p" /etc/redis/secret/requirepass.conf) redis-cli info persistence'\'''

   Look for `rdb_last_bgsave_status:err`, not for the word "started".

2. **Neither the pod spec nor a standalone `docker run` of the image would
   have shown the implicit volume.** Both look identical whether or not
   `/data` ends up writable — the volume comes from the image's own
   Dockerfile, applied by the container runtime at container-creation
   time, not from anything Kubernetes or Argo CD renders. The only way to
   settle the question is to read the mount table inside the actual
   running pod, as above. Treat that as a reusable lesson for any other
   image with a declared `VOLUME`, not a Redis-specific quirk — see
   `docs/troubleshooting.md` entry 13.

## The three strings that must agree

Role, ServiceAccount and audience must be identical across
`platform/vault/configure-vault.sh` and this component's own
`config/vault-secrets.yaml`:

| String | Value |
| --- | --- |
| Role | `vso-redis` |
| ServiceAccount | `redis` |
| Audience | `vault` |

A mismatch in any one of them is a permission denial that names neither
side, while Argo CD reports everything Synced — the same failure shape
`platform/databases/postgres/README.md` and
`platform/vault-secrets-operator/README.md` both describe for their own
three-strings tables. Unlike PostgreSQL, Redis does **not** carry its own
`VaultConnection`: it reuses the one PostgreSQL created in `databases`,
because a `VaultConnection` is namespace-scoped state rather than
per-consumer — see `platform/vault-secrets-operator/README.md`'s "Three
consumers, not one" section for why that is allowed even though the
`ServiceAccount` and `VaultAuth` still cannot be shared.

## Why the probe is not `redis-cli ping`

`redis-cli ping` against a server with `requirepass` set prints
`NOAUTH Authentication required.` and **still exits 0** — measured on this
image, 2026-09-16. A probe built on the bare command can therefore never
fail: Kubernetes marks the pod Ready and keeps restarting nothing, even
against a server that answers but does not speak RESP correctly. Both
probes in `config/redis.yaml` instead match the response text directly:

    redis-cli ping 2>&1 | grep -qE 'PONG|NOAUTH'

Be precise about what this buys and what it does not. `NOAUTH` is treated
as success here so the probe can tell a live, responding server from a
dead port or a wedged process **without needing the password itself** —
that is a liveness signal, not an auth-correctness one. If the config-file
`include` silently failed and the server holds no password at all, it
answers plain `PONG`, and this probe passes exactly as it would with auth
correctly enforced — it cannot tell the two apart, because it never
authenticates. Whether auth is genuinely enforced is a question for a
manual check, not for this probe: run `redis-cli -e -h redis.databases.svc
-p 6379 ping` from a separate pod, with no `REDISCLI_AUTH` set (`-e` is
required — without it `redis-cli` exits 0 even on `NOAUTH`, the same trap
this whole section is about). Expect `NOAUTH Authentication required.` and
a **non-zero** exit; a `PONG` means auth is not actually enforced no matter
what this probe reports. `docs/superpowers/plans/2026-09-16-redis.md`'s
Task 3 Step 2 runs exactly this check. Someone will eventually look at this
and want to "simplify" it back to a bare `redis-cli ping`; this paragraph,
and the probe comment in `config/redis.yaml`, are what should stop them —
that change would produce a probe that reports Ready regardless of whether
Redis is actually answering at all.

## Measured resource usage

Measured 2026-09-16, minutes after first start:

| Pod | CPU | Memory (RSS) |
| --- | --- | --- |
| `redis-*` | 10m | ~22MiB |

That RSS is higher than the ~1MiB of actual keyspace at the time because
Redis 8 auto-loads five modules (`bf`, `timeseries`, `vectorset`, `search`,
`ReJSON`) on startup regardless of whether anything uses them — allocator
and module overhead, not a growing dataset. `maxmemory` is set to `134217728` (128MiB)
and bounds the dataset; the container's `256Mi` limit leaves headroom above
that so eviction (`allkeys-lru`), not the OOM killer, is what reacts first
as the cache fills. Requests are `64Mi` / `25m`.

Host memory was unaffected: 10Gi available of 15Gi both immediately before
and immediately after this phase — the cache's footprint does not register
against a host this size. No PVC was created anywhere as a side effect;
the cluster's PVC list still holds only `data-vault-0` and
`data-postgres-0`.
