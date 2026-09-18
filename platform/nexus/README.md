# Nexus Repository CE

Phase 21. Two repositories: `raw-hosted` for versioned build outputs, and
`docker-proxy`, a pull-through cache of Docker Hub that this node's containerd
uses as a mirror. The admin credential is generated inside Vault, delivered by
the Vault Secrets Operator as Secret `nexus-admin` in namespace `artifacts`,
and applied to the running server by the `nexus-bootstrap` Job — never typed
into the web UI.

| | |
| --- | --- |
| Image | `sonatype/nexus3:3.96.1` |
| Digest, probed 2026-09-17 | `sha256:56142f13432cf072e017aebb2025f201e42ae36ff40bb82618c702504c61f7dd` |
| Edition the server reports | `Nexus/3.96.1-01 (COMMUNITY)` |
| UI and raw | <https://nexus.taildf6cd4.ts.net> |
| Docker clients | <https://nexus-docker.taildf6cd4.ts.net> |
| containerd mirror | `http://127.0.0.1:30082` (NodePort, pinned) |

- `platform/nexus/config/` — the only directory Argo CD reconciles.
- `platform/nexus/rest-schemas.md` — every REST endpoint, body and status code
  this phase uses, each one observed against this exact image. **It is the
  authority over upstream documentation**, for the reason the "REST API" section
  below gives.
- `platform/nexus/registries.yaml` — the canonical copy of a host file Argo CD
  cannot apply. See "The mirror file git cannot apply".

## What this is, and why it is not Artifactory

The roadmap's phase 21 (`docs/workstation-plan.md` §24) names Artifactory and
asks for Maven, npm and PyPI proxies, a Docker registry, internal repositories
and centralised dependency management. **No free JFrog edition covers that
list, and that is the whole reason for the substitution:** Artifactory OSS has
no Docker, OCI or Helm support, and JFrog Container Registry has Docker but no
Maven, npm or PyPI. Filling the role with Artifactory would have meant running
two JFrog products, buying Artifactory Pro, or cutting half the list. Nexus
Repository CE spans both halves in one deployment, so the roadmap's word is
kept and the roadmap's product is not. The full argument, with JFrog's own
wording, is in
`docs/superpowers/specs/2026-09-17-nexus-repository-design.md` §2.

## Community Edition's limits, and the way it stops

Community Edition is capped at **40,000 components** and **100,000
requests/day**. Both ceilings sit below the embedded H2 database's own (100,000
and 200,000), which is why this deployment uses H2 rather than the wave-23
PostgreSQL: a CE instance cannot outgrow H2, because its licence stops it
first (spec §6.2, decision N4).

**The dangerous part is how it stops.** Sonatype's documented behaviour at the
cap is that CE's safeguards *pause the addition of new components* until usage
falls back below both thresholds. It pauses; it does not error. A Docker proxy
that has silently stopped caching looks exactly like one that is working, only
slower — and nothing in `kubectl` or Argo CD will say so.

Two things guard against that:

- **The Usage Center** in the web UI shows the live component and request
  counters. Nothing outside Nexus reports them, and no alarm watches them.
- **The `docker-proxy-cleanup` policy**, created by the bootstrap Job, evicts
  cached components not downloaded in **30 days** (`criteriaLastDownloaded: 30`
  — the unit is days, established from the shipped UI code, see
  `rest-schemas.md` §5). Docker layers are the bulk of what lands here, so
  keeping the cache pruned is what keeps the 40,000 figure out of reach.
- **The task that enforces that policy is the product's own, and it is
  running.** A policy on its own evicts nothing; something has to execute it.
  Nexus creates that something itself — verified on this build, not assumed:
  `GET /service/rest/v1/tasks` on the live instance returns a task named
  `Cleanup service`, type `repository.cleanup`, enabled, on cron
  `0 0 1 * * ?` (01:00 UTC daily); the pod logs it as
  `Task 'Cleanup service' [repository.cleanup] : state=WAITING` at every
  startup; and `CleanupBootService.doStart()` in the shipped
  `nexus-cleanup-config` jar creates it when absent and *deletes duplicates of
  it*. The bootstrap Job therefore creates no task, and must not be given one —
  a second copy would be removed by Nexus at the next restart. The full
  evidence, with the commands that produced it, is in `rest-schemas.md` §5
  ("The scheduled task that enforces the policy is built into the product").
  The one caveat is the schedule, not the mechanism: eviction runs once a day,
  so a burst that adds tens of thousands of components inside a single day is
  not held back by it.

Sonatype also documents that CE refuses to fetch or upload any component until
its EULA is accepted. The Job accepts it over REST on every run (`204`), so it
is not a human step and not a browser wizard.

## What the bootstrap Job configures, and the seven repositories it did not create

`nexus-bootstrap` is an Argo CD `Sync` hook with
`hook-delete-policy: BeforeHookCreation`, so a sync that reaches the hook
deletes the previous Job and creates a fresh one instead of failing on the
immutable object already there, and leaves the completed Job behind so its log
can be read. That is what the policy guarantees — *not* that every sync
re-executes the script: a sync can skip the hook entirely and still report
`Synced`, which is the trap the "Re-running the bootstrap Job" section below
covers.

When it does run, the Job accepts the EULA, rotates the admin password to the
Vault value on first run, activates the `DockerToken` realm, enables anonymous
access, and reconciles the cleanup policy and both repositories — probing each
object with a GET and then POSTing or PUTing, so a second run updates rather
than duplicating.

`raw-hosted` and `docker-proxy` sit alongside **seven stock repositories that
Nexus 3 ships with**, which this phase neither created nor removed:
`maven-releases`, `maven-snapshots`, `maven-central`, `maven-public`,
`nuget-hosted`, `nuget.org-proxy`, `nuget-group`. A repository listing showing
nine entries is the correct, expected state — do not read the seven defaults as
a failed or duplicated bootstrap.

## `raw-hosted` rejects unrecognised file extensions with HTTP 400

This is the first thing that will bite anyone uploading a build output, so it
is here rather than in a footnote.

`raw-hosted` is created with `strictContentTypeValidation: true`. For the `raw`
format Nexus derives the content type from the **filename extension** and
refuses the upload outright when it cannot, before storing any bytes:

```
PUT .../repository/raw-hosted/probe/1.0.0/probe.bin
  -> 400  Content type could not be determined: /probe/1.0.0/probe.bin
```

**An explicit `Content-Type` request header does not override this.** The same
bytes sent as `probe2.bin` with `Content-Type: application/octet-stream` were
rejected identically; the same bytes as `probe.gz` returned **201** and read
back byte-for-byte (1,048,754 bytes out, 1,048,754 back, md5 identical).
A filename with no extension leaves the same derivation with nothing to work
from, so it must be refused by the same mechanism — that is a consequence of
how the check works, not something that was PUT and observed: the only rejected
uploads on record are the two `.bin` ones above.

What to do about it:

- **Give every upload an extension Nexus maps.** Only `.txt` and `.gz` have
  been proven against this instance (201 both, with `.gz` round-tripping 1 MiB
  byte-for-byte). For an opaque binary, package it as `.tar.gz` rather than
  publishing a bare `.bin`. Anything else — `.zip`, `.jar`, `.whl` — is likely
  accepted but unverified here, so prove it with one throwaway PUT before
  wiring a build to it. This is the recommended path: the strict posture is a
  reasonable one for a curated artifact store, and it is what the repository
  was deliberately configured with.
- If a build genuinely must publish an extension Nexus does not recognise, the
  alternative is to set `strictContentTypeValidation: false` on `raw-hosted`
  **in the bootstrap Job's body**, not in the UI — the Job reconciles the
  repository on every sync and would put a hand-made UI change straight back.
- One quirk worth knowing: a rejected upload leaves an **assetless component
  shell** behind in the repository. Nexus registers the component row before
  the 400 and does not clean it up. Removing one is a single REST `DELETE` on
  the component id.

## Rotating the admin password, and the ordering trap

Nexus keeps its own password hash inside its H2 database. **It will not notice a
changed value in Vault.** Changing Vault alone leaves VSO publishing a new
Secret while the server still accepts only the old password — and if the old
value has been discarded at that point, the `admin` account is stranded, because
the Job's reconcile path needs a working credential to authenticate with.

Rotate in this order:

1. **Update Vault** — from `vault-0`, the same `password=@file` form
   `configure-vault.sh` uses so the value never becomes a command argument:

   ```
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh'
   ```

   then, inside the pod — the same generation and same `password=@file` form
   `configure-vault.sh` uses, so the value is never displayed and never becomes
   a command argument visible in `ps`:

   ```
   vault login
   umask 077
   TMPF=$(mktemp)
   head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$TMPF"
   vault kv put homelab/nexus username=admin password=@"$TMPF"
   rm -f "$TMPF" /home/vault/.vault-token
   exit
   ```

   Alphanumeric only, for the reason `configure-vault.sh` gives: this password
   is sent in a `change-password` request body and pasted into browser logins,
   and a `/` or `@` survives neither round trip predictably. Read it back with
   `vault kv get homelab/nexus` when a browser login is needed.

2. **Wait for the Secret to carry the new value.** `refreshAfter` on the
   `VaultStaticSecret` is `60s`:

   ```
   sg k3s-admin -c 'kubectl -n artifacts get vaultstaticsecret nexus-admin'
   ```

3. **Re-run the bootstrap Job while the old password is still known** — see the
   next section for how, because the obvious way can silently do nothing. The
   Job's `already provisioned` branch authenticates with the value currently in
   the Secret; that is what applies the change to the running server.

**Do not discard the old password until step 3 has demonstrably run.** If the
account is ever stranded, recovery is not from Vault: it is Nexus's own reset
path against the H2 store in the PVC, which this phase has not written or
rehearsed. Be clear about what that path costs before relying on it as a safety
net: Nexus holds the credential in an embedded H2 database it keeps open, so a
reset means **stopping Nexus and editing that store inside the PVC** — see
"Taking Nexus down" for why even stopping it is not a `--replicas=0` away. It
is not a live operation, and it is not a `kubectl exec` one-liner.

Nothing about this rotation is automated, and nothing alarms on it. The
credential exists only in Vault, in the Secret VSO derives from it, and inside
Nexus's own database — not in this repository, not in the pod spec, and not in
any process argument.

**This rotation has not been performed on this deployment.** The first-run
rotation has: the Job changed the boot-generated password to the Vault value,
`/nexus-data/admin.password` is gone from the pod, and the Vault value
authenticates as `admin` against the running server (see "Measured behaviour").
What is untested is a *second* rotation — a new value written to Vault and
carried to the server by a re-run. The steps above are assembled from the parts
that are individually proven (the Job's `already provisioned` branch, which has
run and authenticated; and `configure-vault.sh`'s own seeding form), not from a
rehearsed end-to-end run. Treat the ordering warning as the load-bearing part.

## Re-running the bootstrap Job — the obvious way can silently no-op

The recipe that looks right is to delete the Job and patch the Application to
sync:

```
kubectl delete job -n artifacts nexus-bootstrap
kubectl -n argocd patch app nexus --type merge -p '{"operation":{"sync":{}}}'
```

**Observed result: no Job, no error, and the Application still reading
`Synced`/`Healthy`.** Deleting the Job makes the Application drift, so Argo CD's
own self-heal starts a sync within about two seconds — racing the manual patch.
That in-flight operation evaluates the hook against live state it has already
cached, skips it, and on completion clears `.operation`, taking the freshly
patched operation with it. Hook objects are not tracked desired state, so a
missing Job produces no drift and nothing re-creates it.

This is a false pass for anyone who checks the Application's status instead of
the Job. **Assert the Job's `creationTimestamp` advanced**, not that the
Application is green:

```
sg k3s-admin -c 'kubectl -n artifacts get job nexus-bootstrap -o jsonpath="{.metadata.creationTimestamp}{\"\n\"}"'
sg k3s-admin -c 'kubectl -n argocd patch app nexus --type merge -p "{\"operation\":{\"sync\":{\"syncStrategy\":{\"hook\":{}}}}}"'
```

then re-read the same `creationTimestamp` until it is newer than the first
reading, and only then read the log:

```
sg k3s-admin -c 'kubectl logs -n artifacts job/nexus-bootstrap'
```

The `syncStrategy.hook` form is what worked on the second attempt; the Job was
created and completed in **4 seconds** (against 73 s for the first run, the
whole difference being the cold start the first run absorbed). Waiting for
`.status.operationState.phase` to leave `Running` before patching is the other
way to avoid the race. Either way, the `creationTimestamp` check is the part
that makes the result trustworthy.

A clean re-run reads `already provisioned: using the Vault password`, has no
`==> rotating` section at all, and turns all three creates into updates —
cleanup policy `updated (200)`, `raw-hosted: updated (204)`,
`docker-proxy: updated (204)`. Verified: two runs, no duplicate repository and
no duplicate connector. Tasks are not in that list because the Job creates
none — the only cleanup task is the product's own (see "Community Edition's
limits"), so there was never a task for a re-run to duplicate.

## Taking Nexus down — `--replicas=0` does not work

```
kubectl scale deploy/nexus -n artifacts --replicas=0     # DOES NOT WORK
```

It returns `deployment.apps/nexus scaled` and then nothing happens. The `nexus`
Application has `syncPolicy.automated.selfHeal: true`, so `replicas=0` is drift
from git's `replicas: 1` and Argo CD reverts it within seconds — before the
ReplicaSet ever removes the pod. Observed: 180 s after the scale command the
same pod was still running, older than the wait, with `spec.replicas` back at
1. Patching the Application to disable `selfHeal` does not help either, because
`root` is also `selfHeal: true` and reverts the Application's own spec in turn.

This matters because an earlier version of the plan told people to do it, and
one verification step was built on it.

Two things that do work:

- **Delete the pod.** `kubectl delete pod -n artifacts -l app=nexus`. Measured
  across four trials: the Service endpoint is gone and NodePort 30082 **refuses
  connections within 0.2-0.5 s** (kube-proxy REJECT, not a timeout — so a
  client falls back immediately rather than waiting), and recovers at **61-63 s
  every time**, never under a minute. `selfHeal` does not fight pod deletion;
  the ReplicaSet simply recreates it.
- **Change `replicas` in git** and let Argo CD apply it. That is the only way to
  keep it down for longer than a restart.

## Every pod replacement costs exactly one container restart

Known behaviour, observed **6 of 6 times**, and not a fault to chase:

The ReplicaSet creates the replacement pod as soon as the old one is marked for
deletion. Both briefly hold the same ReadWriteOnce volume — legal on a single
node — and ehcache's lock file under `/nexus-data` conflicts, so the first
container start fails with reason **`Error`** (never `OOMKilled`) and the retry
succeeds about **20 s** later. It is self-healing, and the cost is that ~20 s.

`strategy: Recreate` does not govern this: `Recreate` governs *rollouts*, not
deletions. A `preStop` hook or a longer termination grace period would not help
for the same reason.

So: **a `restartCount` of 1 after a pod replacement is expected.** The check
that still means something is that the previous container's log names the
ehcache lock, and that `OOMKilled` is false. A restart with `OOMKilled: true`,
or a restart with no pod replacement behind it, is a real signal.

It is a candidate for a later phase, not a bug to fix now: removing it means a
StatefulSet, and the spec chose a Deployment deliberately (decisions N5 and N6
— a rolling update would deadlock on the RWO claim, and `volumeClaimTemplates`
carries the atomic-list server-side-apply trap documented at length in
`platform/databases/postgres/config/postgres.yaml`). Re-architecting against
two explicit spec decisions to remove a self-healing 20-second hiccup is not a
trade worth making here.

## The mirror file git cannot apply: `registries.yaml`

`platform/nexus/registries.yaml` is the canonical copy of
`/etc/rancher/k3s/registries.yaml`. **It is not reconciled by Argo CD** — k3s
reads it from the host filesystem at startup and nothing in the cluster can
apply it. Same arrangement as `infrastructure/networking/policy.hujson`.

Its substance is four lines: one explicit `docker.io` mirror with a single
endpoint, `http://127.0.0.1:30082`. The comments in the file record why each
choice is what it is — an explicit key rather than `"*"` (k3s#11857 breaks
fallback with the wildcard on containerd 2.x, which this host runs), no
`rewrite:` rules (k3s#7007, same), `http://` mandatory, and a NodePort rather
than a Service name because containerd runs on the host and does not use
cluster DNS.

### Installing it

**`sudo` requires a TTY on this host** — `sudo -n true` answers `interactive
authentication is required` — so this cannot be automated from an agent or any
non-interactive context. It is an operator step at a real terminal:

```
sudo cp /srv/projects/homelab/platform/nexus/registries.yaml /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
```

**The restart cycles every pod on this node.** It is the most disruptive step in
the phase: do it once, deliberately, at a quiet moment, and never as a side
effect of something else. After the restart, check the node, all Applications
and the pre-existing tailnet URLs, not just Nexus. That check has been run: the
node came back Ready, 13/13 Applications Synced/Healthy, `argocd` answered 200,
`vault` answered 307 followed to 200, and `nexus` answered 200 (spec §17.12).

### The escape hatch

If the mirror ever misbehaves, **delete the file and restart k3s**:

```
sudo rm /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
```

That returns containerd to pulling Docker Hub directly. Nothing else needs
changing, and nothing in the cluster depends on the mirror existing.

### What the mirror costs, and the fallback it depends on

Once the file is in place, every `docker.io` pull tries Nexus first. The design
depends on containerd's documented behaviour that **the default endpoint is
always tried as a last resort, even when a mirror is configured** — which is
also what makes a cold rebuild possible at all, since Nexus's own image is
pulled through containerd. If that holds, the cost of a dead Nexus is latency
rather than failure, and a small one: each pull waits for the mirror attempt to
fail before falling back, and that wait is short because NodePort 30082
*refuses* connections rather than hanging — measured at 0.2-0.5 s, above,
against 2-5 s of pull work.

**That fallback has not been measured on this host, and must not be described
as verified.** Pulling an *uncached* image with the mirror down is spec §17.7;
it is a step a later operator runbook phase exercises, and it has not been
performed. The runbook that attempted it aborted before measuring rather than
producing a false pass — correctly, because `--replicas=0` had not actually
taken Nexus down (see above). So it is the property this design depends on and
that a pending verification step exercises, not something observed here.
`--disable-default-registry-endpoint` is the flag that would break it, and this
host must never set it.

What *has* been verified is that the mirror is genuinely in the path (spec
§17.6): `hello-world` pulled through it **appears as a cached component in
`docker-proxy`**. The component appearing is the proof — a successful pull on
its own is equally consistent with containerd quietly falling back to Docker
Hub, which is exactly why the check is the cache and not the exit code.

## Backups, and the trap

**rsyncing a live H2 database is not a backup; it restores as corruption.**

All three things plan §32 asks to back up — database, filestore, configuration —
live inside the single `nexus-data` PVC, which is the main operational payoff of
choosing embedded H2 over an external PostgreSQL. That also makes the naive
procedure look sufficient, and it is not: a copy of an H2 file taken mid-write
is a copy of a database mid-write.

The correct procedure:

1. **Run Nexus's "Export databases for backup" task** — one of its own task
   types, run on demand or scheduled from the admin UI. It writes a consistent
   export into `/nexus-data/backup/`.
2. **Copy both `/nexus-data/backup/` and `/nexus-data/blobs/`** to
   `/backups/services/nexus` — the path the plan's own backup tree names
   (`docs/workstation-plan.md` §32), not the top of `/backups/`.

Both halves are required. The export without the blobs restores an index
pointing at nothing.

**Phase 21 does not automate this, and has performed no restore drill.** That is
phase 34's work. It is recorded here as a known gap rather than left to be
assumed covered — an untested restore procedure is not a backup either.

## The REST API: re-probe, do not trust upstream docs

Several endpoints Sonatype documents **do not exist on this build**, and one
field's unit could not be established from the API at all — it had to be read
out of the shipped UI code. Every call the
bootstrap Job makes was observed against `sonatype/nexus3:3.96.1` and is
recorded with its status code in `platform/nexus/rest-schemas.md`. **That file
is the authority. Do not "correct" a payload from upstream documentation without
re-probing.** The findings that cost the most time:

- **`POST /service/rest/v1/cleanup-policies` returns 404** and is absent from
  the instance's own OpenAPI document entirely. The path that works is the
  internal one the product's own UI uses,
  `/service/rest/internal/cleanup-policies`, and its success code is **200**,
  not 201. It returns **403 to an anonymous caller**, so reading it back needs a
  credential.
- **`criteriaLastDownloaded` is in days.** Sending `2592000` would mean ~7,000
  years and nothing would ever be evicted. The server accepts either number
  without validation and echoes it back unchanged, so a 200 proves nothing about
  the unit.
- **`NexusAuthorizingRealm` is not a valid id for
  `PUT /security/realms/active`** — sending it 400s the whole call. The body
  that works is `["NexusAuthenticatingRealm","DockerToken"]`. The same string
  *is* correct in `PUT /security/anonymous`, where it names the anonymous
  subject's realm. Two different meanings, one name.
- **Duplicate creates return 400, not 409.** That is why the Job probes with a
  GET and then POSTs or PUTs, rather than treating a conflict status as success:
  a helper that treats 400 as "already exists" cannot tell a duplicate from a
  malformed body, and a helper that treats 400 as fatal breaks every re-run.
- **A 401 from `/v2/` is correct, not a fault.** With the `DockerToken` realm
  active, the registry answers an anonymous ping with a bearer challenge,
  exactly as Docker Hub does — `www-authenticate: Bearer realm="…/v2/token"`.
  Every endpoint that carries content serves anonymous clients: a manifest
  fetch through `nexus-docker.taildf6cd4.ts.net` returns **200**. Never gate a
  readiness check on `/v2/` being 200.

Editing the Job's script has one more constraint worth knowing up front: the
unsuffixed `3.96.1` image is **Alpine/BusyBox**. There is no `bash`, no
`python3` and no `jq`. The script is POSIX `sh` run by `/bin/sh`, its `sed` is
BusyBox rather than GNU, and its JSON is manipulated with `sed`/`awk`.

## Measured behaviour

Recorded so that a later change has something to compare against. Probe figures
are from a local Docker container on the pinned image (2026-09-17); live figures
are from the deployed pod (2026-09-18).

| | Value |
| --- | --- |
| Cold start to REST 200 (probe) | 38 s |
| Peak cgroup memory (probe) | 1.24 GiB against a **2.44 GiB** ceiling, never OOM-killed |
| `startupProbe` | `failureThreshold: 8`, `periodSeconds: 10` — the measured 38 s doubled |
| Pod Ready (live, first boot on the PVC) | 60 s |
| Bootstrap Job completion (live, first run) | 75 s after the sync started; the Job's own `DURATION` 73 s |
| Bootstrap Job completion (re-run) | 4 s |
| Steady-state memory (live) | ~1249Mi against the 2.5Gi limit, CPU 8m idle |
| Endpoint-dead window on pod deletion | 61-63 s across four trials |

That 2.44 GiB probe ceiling was `docker run --memory=2500m`, where docker reads
`m` as mebibytes: 2500 MiB = 2.44 GiB. **It is not a Kubernetes quantity** — in
a manifest `2500m` means 2.5 of a unit, which is why it is written out here in
GiB rather than copied across. The pod's own limit is the `2.5Gi` in the
steady-state row, set independently.

The probe's memory figures are **cgroup accounting** (`memory.current` and
`memory.peak`), which include page cache attributable to the container. They are
an upper bound on process resident memory, not a measurement of it — the
conservative direction for sizing. If the pod is ever OOM-killed, raise
`limits.memory`, not the heap: a bigger heap under an unchanged limit moves the
cliff closer.

Also verified live, each with its own evidence rather than an assertion:

- **Secret `nexus-admin` carries exactly `password` and `username`** — no `_raw`
  key, so the credential is not duplicated in verbatim KV JSON (spec §17.11).
- **`/nexus-data/admin.password` is gone from the running pod**, which proves
  the rotation reached the server. A Job exiting 0 would not have proven that.
- **The Vault password authenticates as `admin` against the live server**, with
  two controls: anonymous gets **403**, the Vault password gets **200**, and a
  deliberately wrong password gets **401** — so the 200 is neither anonymous
  access succeeding regardless nor an endpoint ignoring its credentials.
- **Both tailnet hostnames answer**: `nexus.taildf6cd4.ts.net/` → 200;
  `nexus-docker.taildf6cd4.ts.net/v2/` → 401 with the bearer challenge; a
  manifest fetch through it → 200, served from cache.
- **The k3s restart broke nothing**: node Ready, 13/13 Applications
  Synced/Healthy, `argocd` 200, `vault` 307→200, `nexus` 200.

### What is not measured, and must not be claimed

- **The fallback with the mirror down (spec §17.7) has not been performed.** See
  "What the mirror costs" above.
- **Memory under load (spec §17.9) has not been measured.** The figures above
  are steady state, plus a probe peak that included one real image pull through
  the proxy. There is no measurement of the pod while several images are pulled
  and an artifact is uploaded concurrently.
- **A second admin-password rotation has never been performed.** Only the
  first-run rotation has (boot-generated value → Vault value, proven by the
  disappearance of `/nexus-data/admin.password`). The procedure in "Rotating
  the admin password" is assembled from individually proven parts, not from a
  rehearsed end-to-end run, and neither is the stranded-account recovery path
  it points at. Stated here as well as there because this is the section a
  sceptical reader checks.
- **There has been no formal state-survives-restart measurement (spec §17.10).**
  What can be said is that the pod has been deleted and recovered repeatedly —
  four deliberate deletions, plus the replacements around them — and came back
  Ready with both repositories intact every time. That is weaker than the
  itemised check the spec asks for, which also names the connector, the cleanup
  task and the credential, and it is stated as what it is.

## Known gaps

- **No Maven, npm or PyPI proxies.** Nothing in this cluster builds a Maven, npm
  or PyPI artifact today, so a dependency proxy would be infrastructure with no
  consumer — and unlike Redis, Nexus is not cheap to run. Adding one later is a
  repository definition in the bootstrap Job, not a redesign. Spec §2.1.
- **No hosted Docker registry.** GHCR holds this homelab's images and its pull
  credential is already wired in (phase 20, `platform/registry/`). A second copy
  earns nothing. Decision N3.
- **No `NetworkPolicy`** — for Nexus or for anything else in this cluster. Every
  pod can reach `nexus.artifacts.svc` on 8081 and 8082, and anonymous read is
  enabled on purpose so containerd can pull. This is the cluster-wide gap
  recorded in the root README, not a Nexus-specific one.
- **The bootstrap Job mounts the same RWO PVC as the Deployment.** That is legal
  only because this is a single node: `ReadWriteOnce` is a per-node constraint,
  not a per-pod one. On a second node the Job would be unschedulable or the
  Deployment would be, depending on which landed first. It is the one element of
  this design that would break on a second node, and it is documented rather
  than silently relied upon.
- **Backups are not automated and no restore has been drilled** — see "Backups,
  and the trap". Phase 34.
- **Nothing alarms on the CE component cap.** The cleanup policy and the
  product's own daily `Cleanup service` task keep it out of reach, but nothing
  watches the counter — reading it is a manual visit to the Usage Center, and
  eviction only runs at 01:00 UTC.

## Related

- `docs/superpowers/specs/2026-09-17-nexus-repository-design.md` — the design,
  including §2 (why not Artifactory), §6.2 (CE's caps), §8 (the mirror) and §13
  (backups).
- `platform/nexus/rest-schemas.md` — the probed REST contract. The authority for
  every endpoint, body and status code.
- `platform/vault/configure-vault.sh` — seeds `homelab/nexus`, and writes the
  `vso-nexus-read` policy and `vso-nexus` role the `VaultAuth` needs.
- `docs/troubleshooting.md` — entry 1 covers the `fsGroup` trap this Deployment
  guards against with `runAsUser`/`fsGroup` 200.
