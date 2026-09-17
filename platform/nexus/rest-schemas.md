# Nexus Repository: probed image, sizing and REST payloads

Everything below was measured or returned by a real `sonatype/nexus3` container
on this host. Nothing here is reasoned from documentation. Later tasks read this
file instead of guessing; if a value here is wrong, correct it by re-probing, not
by editing the number.

- **Probed on:** 2026-09-17
- **Probe:** `docker run --name nexus-probe -p 18081:8081 -p 18082:8082 --memory=2500m -e INSTALL4J_ADD_VM_PARAMS='-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m' -v <probe data dir>:/nexus-data sonatype/nexus3:3.96.1`
  (data dir pre-owned by uid 200, mirroring `fsGroup: 200`)
- **Edition reported by the running instance:** `Nexus/3.96.1-01 (COMMUNITY)`

## 1. The pinned image

| | |
|---|---|
| Tag | `sonatype/nexus3:3.96.1` |
| Digest | `sha256:56142f13432cf072e017aebb2025f201e42ae36ff40bb82618c702504c61f7dd` |
| Published | 2026-09-12 |

Chosen as the newest plain three-part version tag on Docker Hub at probe time.
`latest` is rejected on principle; `3.96.1-alpine`, `3.96.1-ubi` and every other
suffixed variant are rejected by the plan's tag rule. Older plain tags available
the same day were `3.96.0`, `3.95.4`, `3.95.3`, `3.94.2`, `3.90.5`.

**Every later task uses `sonatype/nexus3:3.96.1`.** Pin the digest too if the
manifest allows it.

## 2. What is actually inside the image

The unsuffixed `3.96.1` tag is **Alpine/BusyBox-based**, not a glibc distro:

```
NAME="Alpine Linux"   VERSION_ID=3.24.1
java: openjdk 21.0.12 (build 21.0.12+8-alpine-r0)
```

Interpreter inventory (`docker exec nexus-probe sh -c 'command -v ...'`, run as
the image's own `uid=200(nexus)`):

| Tool | Present | Path | Version |
|---|---|---|---|
| `bash` | **NO** | — | — |
| `sh` | yes | `/bin/sh` | BusyBox v1.37.0 (ash) |
| `ash` | yes | `/bin/ash` | BusyBox v1.37.0 |
| `python3` | **NO** | — | — |
| `python` | **NO** | — | — |
| `jq` | **NO** | — | — |
| `curl` | yes | `/usr/bin/curl` | 8.22.0 (OpenSSL 3.5.8) |
| `wget` | yes | `/usr/bin/wget` | BusyBox v1.37.0 |
| `mktemp` | yes | `/bin/mktemp` | BusyBox v1.37.0 |
| `sed` | yes | `/bin/sed` | BusyBox v1.37.0 (**not GNU sed**) |
| `awk` | yes | `/usr/bin/awk` | BusyBox v1.37.0 |
| `grep` | yes | `/bin/grep` | BusyBox v1.37.0 |

`docker exec nexus-probe bash -c '...'` fails outright:

```
OCI runtime exec failed: exec: "bash": executable file not found in $PATH   (exit 127)
```

### Consequences for the bootstrap job

1. **`command: ["/bin/bash", "/script/bootstrap.sh"]` will not start.** Use
   `command: ["/bin/sh", "/script/bootstrap.sh"]`. Write the script to POSIX
   `sh`: no arrays, no `[[ ]]`, no `${var,,}`, no `<( )`, no `local -n`.
2. **Nothing may be piped through `python3` or `jq`.** Parse with `sed`/`awk`,
   and remember `sed` is BusyBox, so use `[[:space:]]` rather than GNU classes
   and avoid `-E`-only GNU extensions.

### Proven `python3` substitute for the EULA flip

The bootstrap must `GET /service/rest/v1/system/eula`, flip `"accepted"` to
`true` and POST the whole document back unchanged. This `sh` + BusyBox `sed`
form was executed inside the probe and verified end to end:

```sh
T=$(mktemp)
curl -sf -u "admin:$PW" "$NX/service/rest/v1/system/eula" > "$T"
sed 's/"accepted"[[:space:]]*:[[:space:]]*false/"accepted" : true/' "$T" > "$T.new"
curl -s -o /dev/null -w '%{http_code}' -X POST -u "admin:$PW" \
  -H 'Content-Type: application/json' -d @"$T.new" "$NX/service/rest/v1/system/eula"
rm -f "$T" "$T.new"
```

Observed: POST returned **204**, and a subsequent GET returned
`"accepted" : true`. The `disclaimer` string was emitted byte-for-byte
unchanged — note it contains the literal text `accepted:false`, which the
anchored `"accepted"` pattern deliberately does not match.

EULA acceptance is **one-way**: POSTing the document back with
`"accepted": false` returns **500**, so the probe could not establish whether
repository creation is gated on acceptance. Accept the EULA first regardless;
it costs one call.

## 3. Startup and memory

| Measurement | Value |
|---|---|
| Cold start to `GET /service/rest/v1/status` = 200 | **38 s** |
| Log line `Started Sonatype Nexus COMMUNITY 3.96.1-01` | 37.6 s after container start |
| Warm restart (existing `/nexus-data`) to REST 200 | 33 s |
| Steady-state container memory | 1.204 GiB |
| Peak container memory (`/sys/fs/cgroup/memory.peak`) | 1,330,425,856 B = **1.24 GiB** |
| `--memory=2500m` ceiling (2.441 GiB) | **held** |
| `docker inspect … .State.OOMKilled` | `false` |

The proposed JVM settings (`-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m`)
ran to steady state at roughly **51 % of the 2500m ceiling**, including a full
image pull through the Docker proxy. The ceiling does not need raising.

### `startupProbe` `failureThreshold`

38 s measured ÷ 10 s `periodSeconds` = 3.8 → 4, doubled for this host's history
of probe timeouts under memory pressure:

> **`failureThreshold: 8`** (with `periodSeconds: 10`)

Measured startup is far below the 240 s mark, so a 300 s REST wait loop in a
later task is ample and does not need raising.

Caveat worth carrying: 38 s was measured on a warm host with the image already
pulled and `/nexus-data` on local disk. `failureThreshold: 8` allows 80 s. On a
first in-cluster boot against a PVC this is tighter than it looks; if the pod
ever fails its startup probe, raise `failureThreshold`, not the memory.

## 4. Repository payloads — verified against 3.96.1

Schemas were read from the running instance's own OpenAPI document
(`GET /service/rest/swagger.json`, `openapi: 3.0.1`, `info.version: 3.96.1-01`,
254 schemas under `components.schemas` — note **`components.schemas`**, not the
Swagger-2 `definitions` key). Both bodies below were POSTed to the probe and
**each returned `201`**.

### 4.1 Raw hosted — `POST /service/rest/v1/repositories/raw/hosted` → **201**

```json
{"name":"raw-hosted","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true,"writePolicy":"ALLOW"},"cleanup":{"policyNames":[]},"component":{"proprietaryComponents":false},"raw":{"contentDisposition":"ATTACHMENT"}}
```

### 4.2 Docker proxy — `POST /service/rest/v1/repositories/docker/proxy` → **201**

Create the cleanup policy first; a reference to a policy that does not exist is
rejected.

```json
{"name":"docker-proxy","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true},"cleanup":{"policyNames":["docker-proxy-cleanup"]},"proxy":{"remoteUrl":"https://registry-1.docker.io","contentMaxAge":1440,"metadataMaxAge":1440},"negativeCache":{"enabled":true,"timeToLive":1440},"httpClient":{"blocked":false,"autoBlock":true},"docker":{"v1Enabled":false,"forceBasicAuth":false,"httpPort":8082},"dockerProxy":{"indexType":"HUB","cacheForeignLayers":false}}
```

`"httpPort":8082` makes Nexus open a **second HTTP connector on container port
8082** serving the Docker Registry v2 API at the root path. The Deployment and
Service in a later task must expose 8082 as well as 8081 if anything is to use
the root-level registry endpoint; path-based access on 8081
(`/repository/docker-proxy/v2/…`) works without it.

Both repositories are **not idempotent**. Verified on the probe:

| Repeat call | Result |
|---|---|
| duplicate `POST …/repositories/raw/hosted` | `400` — `[{"id":"PARAMETER name","message":"Name is already used, must be unique (ignoring case)"}]` |
| duplicate `POST …/repositories/docker/proxy` | `400` |
| `PUT /service/rest/v1/repositories/raw/hosted/raw-hosted` (same body) | **`204`** |

A bootstrap that may run twice should `GET` the repository first and skip, or
use the `PUT …/{format}/{type}/{name}` update form, which is idempotent.

## 5. Cleanup policy — endpoint and unit

**The endpoint in the plan does not exist in this build.**

```
POST /service/rest/v1/cleanup-policies   -> 404 NOT_FOUND
GET  /service/rest/v1/cleanup-policies   -> 404 NOT_FOUND
```

`swagger.json` for 3.96.1 documents **no** cleanup-policy path and **no**
cleanup-policy request schema (the only related schema,
`CleanupPolicyAttributes`, is just the `{"policyNames":[…]}` block a repository
embeds). The API that the product's own UI uses, and the one that works, is the
internal one:

### `POST /service/rest/internal/cleanup-policies` → **200** (not 201)

```json
{"name":"docker-proxy-cleanup","format":"docker","notes":"Phase 21: keeps the CE 40000-component cap out of reach. Spec section 6.2.","criteriaLastDownloaded":30}
```

Response body echoes the stored policy. Companion operations, all verified:

| Operation | Result |
|---|---|
| `GET /service/rest/internal/cleanup-policies` | `200`, JSON array |
| `GET /service/rest/internal/cleanup-policies/docker-proxy-cleanup` | `200` |
| `DELETE /service/rest/internal/cleanup-policies/{name}` | `204` |
| duplicate `POST` of an existing name | `400` — `[{"id":"PARAMETER name","message":"Name is already used, must be unique (ignoring case)"}]` |
| `PUT /service/rest/internal/cleanup-policies/{name}` (same body) | `200` — idempotent update |

So a re-runnable bootstrap must treat `200` (not `201`) as success here, and
must tolerate or pre-check the duplicate `400`.

### `criteriaLastDownloaded` is in **DAYS**, and the correct value is `30`

The plan never established the unit, and a successful write proves nothing: the
server accepted both `30` and `2592000` verbatim with no range validation, and
echoed each back unchanged. Evidence for days:

1. The instance's own UI strings (served from
   `/static/nexus-coreui-bundle.js`) label this field:
   `LAST_DOWNLOADED_SUB_LABEL: "Components downloaded in “x” amount of days (e.g 1-9999)"`,
   `PLACEHOLDER: "e.g 100 days"`.
2. The UI posts that same form value straight to
   `service/rest/internal/cleanup-policies` with **no unit conversion** — the
   bundle contains no `86400` multiplier anywhere on this path; the payload
   builder passes `criteriaLastDownloaded` through untouched.
3. There is no `description` for the field anywhere in `swagger.json`, because
   the endpoint is undocumented there. This is the whole reason the unit had to
   be established empirically.

Server read-back after creating the real policy:

```json
{"name":"docker-proxy-cleanup","format":"docker","notes":"Phase 21: keeps the CE 40000-component cap out of reach. Spec section 6.2.","criteriaLastBlobUpdated":null,"criteriaLastDownloaded":30,"retain":null,"sortBy":null,"criteriaReleaseType":null,"criteriaAssetRegex":null,"inUseCount":1,"repositories":["docker-proxy"]}
```

**Send `30`.** Sending `2592000` would mean 2,592,000 days (~7,000 years) and the
proxy would never evict anything.

## 6. Security realms

Active realms on a fresh instance are **only** `["NexusAuthenticatingRealm"]`,
so the Docker Bearer Token realm **must be added**. The realm PUT **is needed**.

The body in the plan is **rejected**:

```
PUT /service/rest/v1/security/realms/active
["NexusAuthenticatingRealm","NexusAuthorizingRealm","DockerToken"]
-> 400  ValidationErrorXO{id='*', message='"Unknown realmIds: [NexusAuthorizingRealm]"'}
```

`NexusAuthorizingRealm` is **not** in `GET /service/rest/v1/security/realms/available`
and must not be sent. Nexus adds it to the active configuration itself. The body
that works:

### `PUT /service/rest/v1/security/realms/active` → **204**

```json
["NexusAuthenticatingRealm","DockerToken"]
```

- **Exact realm id: `DockerToken`** (display name "Docker Bearer Token Realm").
- Read-back afterwards: `["NexusAuthenticatingRealm","DockerToken"]`.

## 7. Anonymous access and the anonymous pull

**Anonymous access is DISABLED on a fresh 3.96.1 instance.** `GET
/service/rest/v1/security/anonymous` returns `{"enabled": false, …}`. The
bootstrap must turn it on:

### `PUT /service/rest/v1/security/anonymous` → **200**

```json
{"enabled":true,"userId":"anonymous","realmName":"NexusAuthorizingRealm"}
```

(`realmName` here is the anonymous *subject's* realm and is unrelated to the
available-realms list, which is why `NexusAuthorizingRealm` is valid in this
call and invalid in §6.) The stock `anonymous` user already carries the
`nx-anonymous` role with `nx-repository-view-*-*-read` and
`nx-repository-view-*-*-browse`, so no role editing is needed.

### The Step 7 check returns 401, and that is correct

```
GET /repository/docker-proxy/v2/   (no credentials)  -> 401
```

**401 here is not a misconfiguration.** With `forceBasicAuth: false` the Docker
Bearer Token realm answers the registry ping with a token challenge, exactly as
Docker Hub does:

```
WWW-Authenticate: Bearer realm="…/v2/token", service="…/v2/token"
```

The ping returns `200` only for an authenticated identity (verified: `200` with
admin basic auth, and `200` with an admin-issued `DockerToken`). Anonymous
clients get `401` on `/v2/` whether or not they present an anonymous token —
but every endpoint that actually carries content serves them:

| Anonymous request through the probe | Status |
|---|---|
| `GET /repository/docker-proxy/v2/` (ping) | 401 (by design) |
| `GET /repository/docker-proxy/v2/token` | 200, issues `DockerToken.…` |
| `GET …/v2/library/alpine/manifests/3.20` (index) | **200** |
| `GET …/v2/library/alpine/manifests/sha256:c64c687c…` (child) | **200** |
| `GET …/v2/library/alpine/blobs/sha256:25f1d6b1…` | **200**, 3,630,321 bytes |
| `GET /service/rest/v1/search?repository=docker-proxy` | 200 |

And the decisive end-to-end proof, an unauthenticated real client pulling
through the proxy's root connector:

```
$ docker pull 127.0.0.1:18082/library/alpine:3.20
3.20: Pulling from library/alpine
Digest: sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
Status: Downloaded newer image for 127.0.0.1:18082/library/alpine:3.20
```

So a later mirror task should **not** treat a `401` on `/v2/` as a failure, and
must not gate its readiness check on that endpoint. Check
`/service/rest/v1/status` for liveness and an actual manifest fetch for the
registry path.

## 8. Order of operations for the bootstrap

Derived from what the probe rejected. Each step's verified success code:

1. Accept the EULA — `POST /service/rest/v1/system/eula` → `204`
2. Add the Docker realm — `PUT /service/rest/v1/security/realms/active` → `204`
3. Enable anonymous — `PUT /service/rest/v1/security/anonymous` → `200`
4. Create the cleanup policy — `POST /service/rest/internal/cleanup-policies` → `200`
5. Create `raw-hosted` — `POST /service/rest/v1/repositories/raw/hosted` → `201`
6. Create `docker-proxy` — `POST /service/rest/v1/repositories/docker/proxy` → `201`

Step 4 must precede step 6. Steps 4-6 are not idempotent and return `400` on a
second run.
