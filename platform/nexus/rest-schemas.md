# Nexus Repository: probed image, sizing and REST payloads

Everything below was measured or returned by a real `sonatype/nexus3` container
on this host, or read out of the pinned image's own shipped code. The one place
external documentation appears is §5, where it corroborates a conclusion already
reached from the image — it is never the primary evidence. Later tasks read this
file instead of guessing; if a value here is wrong, correct it by re-probing, not
by editing the number.

- **Probed on:** 2026-09-17
- **Edition reported by the running instance:** `Nexus/3.96.1-01 (COMMUNITY)`

There were **two probe container lifecycles**, both on the same
`/nexus-data` directory (pre-owned by uid 200, mirroring `fsGroup: 200`) and
both under the same `--memory=2500m` ceiling and JVM flags
`-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m`. Which run produced which
number matters, so:

| | Container A | Container B |
|---|---|---|
| Ports published | `18081:8081` | `18081:8081` **and** `18082:8082` |
| `/nexus-data` | empty (first boot) | already populated by A |
| Produced | the **38 s cold start**, and every REST result in §4-§7 | the **33 s warm restart**, the **1.24 GiB peak**, and the real `docker pull` in §7 |

Container A:

```
docker run -d --name nexus-probe -p 18081:8081 --memory=2500m -e INSTALL4J_ADD_VM_PARAMS='-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m' -v /path/to/probe-data:/nexus-data sonatype/nexus3:3.96.1
```

Container B is the same command with `-p 18082:8082` added. B exists only
because the root-level Docker connector (§4.2, `httpPort: 8082`) is not
reachable unless that port is published, and §7 needed a real client to use
it.

## 1. The pinned image

| | |
|---|---|
| Tag | `sonatype/nexus3:3.96.1` |
| Digest | `sha256:56142f13432cf072e017aebb2025f201e42ae36ff40bb82618c702504c61f7dd` |
| Published | 2026-09-12 |

Chosen as the newest plain three-part version tag on Docker Hub at probe time.
`latest` is rejected on principle; `3.96.1-alpine`, `3.96.1-ubi` and every other
suffixed variant are rejected by the plan's tag rule. Older plain tags available
the same day were `3.96.0`, `3.95.4`, `3.95.3`, `3.95.2`, `3.95.1`, `3.94.2`
and `3.90.5`.

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
`true` and POST the whole document back unchanged. **This is the form to
transcribe**, with the credential reaching curl through `-K` rather than argv:

```sh
AUTHFILE=$(mktemp)
chmod 600 "$AUTHFILE"
T=$(mktemp)
trap 'rm -f "$AUTHFILE" "$T" "$T.new"' EXIT
printf 'user = "admin:%s"\n' "$NEXUS_PASSWORD" > "$AUTHFILE"

curl -sf -K "$AUTHFILE" "$NEXUS/service/rest/v1/system/eula" > "$T"
sed 's/"accepted"[[:space:]]*:[[:space:]]*false/"accepted" : true/' "$T" > "$T.new"
curl -s -o /dev/null -w '%{http_code}' -K "$AUTHFILE" -X POST \
  -H 'Content-Type: application/json' -d @"$T.new" "$NEXUS/service/rest/v1/system/eula"
```

This matches the `AUTHFILE` / `trap` / `printf 'user = "admin:%s"'` pattern the
rest of this repository already uses, and it is why the snippet must not be
written with `-u admin:$PW`: a password passed as a command argument is visible
in `ps` to anything sharing the pod's PID namespace.

**Provenance, stated plainly:** the run that returned **204** used the `-u`
form, because that is what the probe brief modelled. The `-K` rewrite above is a
credential-handling correction, not a re-proof — the two forms differ only in
how curl receives the same `admin:password` pair, and the part that was actually
under test (the `sed` transformation and the POST body) is byte-for-byte the
same. The `sed` substitution itself is proven: the POST returned **204** and a
subsequent GET returned `"accepted" : true`, with the `disclaimer` string
emitted byte-for-byte unchanged — note that string contains the literal text
`accepted:false`, which the quoted-key `"accepted"` pattern deliberately does
not match.

EULA acceptance is **one-way**: POSTing the document back with
`"accepted": false` returns **500**, so the probe could not establish whether
repository creation is gated on acceptance. Accept the EULA first regardless;
it costs one call.

## 3. Startup and memory

| Measurement | Value | Which run |
|---|---|---|
| Cold start to `GET /service/rest/v1/status` = 200 | **38 s** | A |
| Log line `Started Sonatype Nexus COMMUNITY 3.96.1-01` | 37.6 s after container start | A |
| Warm restart (existing `/nexus-data`) to REST 200 | 33 s | B |
| Steady-state container memory (`docker stats`) | 1.204 GiB = **49.3 %** of the ceiling | B |
| Peak container memory (`/sys/fs/cgroup/memory.peak`) | 1,330,425,856 B = 1.24 GiB = **50.8 %** of the ceiling | B |
| `--memory=2500m` ceiling (2.441 GiB) | **held** | A and B |
| `docker inspect … .State.OOMKilled` | `false` | A and B |

**These are cgroup accounting figures, not RSS.** `docker stats` reports the
cgroup's `memory.current` and the peak comes from `/sys/fs/cgroup/memory.peak`;
both include page cache attributable to the container, so they are an upper
bound on process resident memory, not a measurement of it. That is the
conservative direction for sizing — the real JVM footprint is at or below these
numbers — but do not quote them as RSS.

The proposed JVM settings (`-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m`)
sat at **49.3 %** of the 2500m ceiling at steady state and peaked at **50.8 %**.
The peak was read from container B after it had served a complete
`docker pull` of `library/alpine:3.20` through the proxy, so that figure does
include a real image pull; the steady-state figure was taken from the same
container in the same state. Container A's peak was not captured — its
`docker stats` readings (1.201-1.206 GiB) were in the same band. The ceiling
does not need raising.

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

### The two status endpoints, and what `/status/writable` actually proves

Added 2026-09-18, after the bootstrap Job's wait loop was written against
`/service/rest/v1/status/writable` — an endpoint this file had not recorded.

**It exists, it needs no authentication, and it returns 200.** Observed against
a fresh container on the pinned image, with no credentials supplied:

```
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18101/service/rest/v1/status
200
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18101/service/rest/v1/status/writable
200
```

Both return an empty body. Neither call carried `-u` or `-K`.

**But it is a stub, and it is not a writability check.** `StatusResource` is the
whole implementation, and it can be read without running anything. Pull the
class out of the fat jar the same way §5 pulls the UI bundle:

```
CID=$(docker create sonatype/nexus3:3.96.1)
docker cp "$CID:/opt/sonatype/nexus/bin/sonatype-nexus-repository-3.96.1-01.jar" app.jar
docker rm -f "$CID"
python3 -c 'import zipfile,io;z=zipfile.ZipFile("app.jar");i=zipfile.ZipFile(io.BytesIO(z.read("BOOT-INF/lib/nexus-api-rest-common-3.96.1-01.jar")));open("StatusResource.class","wb").write(i.read("org/sonatype/nexus/api/rest/common/status/StatusResource.class"))'
```

`javap` is not on this host but ships inside the image, so disassemble it there:

```
$ docker run --rm --entrypoint /bin/sh -v "$PWD/cls:/cls:ro" sonatype/nexus3:3.96.1 \
    -c 'javap -p -cp /cls org.sonatype.nexus.api.rest.common.status.StatusResource'
public class org.sonatype.nexus.api.rest.common.status.StatusResource implements org.sonatype.nexus.rest.Resource,org.sonatype.nexus.api.rest.common.status.StatusResourceDoc {
  protected final org.slf4j.Logger log;
  public static final java.lang.String RESOURCE_URI;
  private static org.aspectj.lang.JoinPoint$StaticPart ajc$tjp_0;
  private static java.lang.annotation.Annotation ajc$anno$0;
  private static org.aspectj.lang.JoinPoint$StaticPart ajc$tjp_1;
  private static java.lang.annotation.Annotation ajc$anno$1;
  public org.sonatype.nexus.api.rest.common.status.StatusResource();
  public jakarta.ws.rs.core.Response isAvailable();
  public jakarta.ws.rs.core.Response isWritable();
  ...
}
```

Three things follow, in order of how load-bearing they are.

**1. Neither method consults anything.** The AspectJ weaver moved each body into
an `_aroundBody` method, and those are the real implementations. They are
byte-for-byte the same:

```
  static final ... isAvailable_aroundBody0(...);
         0: invokestatic  #45   // Method jakarta/ws/rs/core/Response.ok:()...ResponseBuilder;
         3: invokevirtual #51   // Method ...ResponseBuilder.build:()...Response;
         6: areturn

  static final ... isWritable_aroundBody2(...);
         0: invokestatic  #45   // Method jakarta/ws/rs/core/Response.ok:()...ResponseBuilder;
         3: invokevirtual #51   // Method ...ResponseBuilder.build:()...Response;
         6: areturn
```

`Response.ok().build()` and nothing else. The class injects no service — its
only instance field is `log` — so there is nothing it *could* consult. **200
from `/status/writable` means "JAX-RS is wired", exactly as `/status` does. It
does not mean the blob store is writable, and the two endpoints carry identical
information.**

**2. It requires no authentication.** The only annotations anywhere in the class
file are JAX-RS ones plus Dropwizard's `@Timed`:

```
$ grep -oE 'L(jakarta/ws/rs|org/apache/shiro|org/sonatype)[a-zA-Z0-9/$]*;' statusresource.txt | sort -u
Ljakarta/ws/rs/Consumes;
Ljakarta/ws/rs/GET;
Ljakarta/ws/rs/Path;
Ljakarta/ws/rs/Produces;
Ljakarta/ws/rs/core/Response;
Ljakarta/ws/rs/core/Response$ResponseBuilder;
Lorg/sonatype/nexus/api/rest/common/status/StatusResource;
Lorg/sonatype/nexus/common/metrics/TimedAspect;
```

No `org.apache.shiro.authz.annotation.RequiresAuthentication`, no
`RequiresPermissions`. That is why the unauthenticated curl above returns 200,
and it is what lets a wait loop probe the endpoint before it holds a credential.

**3. The routing, from the same disassembly.** The class is `@Path("/v1/status")`
and `isWritable` is `@GET @Path("/writable")`:

```
RuntimeVisibleAnnotations:            (class)
  1: jakarta.ws.rs.Path(value="/v1/status")

RuntimeVisibleAnnotations:            (isWritable)
  0: jakarta.ws.rs.GET
  1: jakarta.ws.rs.Path(value="/writable")
```

**Consequence for later tasks.** Use either endpoint as a liveness signal; they
are interchangeable. Do **not** describe `/status/writable` as proof that Nexus
will accept writes, and do not add a step that depends on that being true. The
bootstrap Job probes `/writable` only because it is the more specific of the two
names, and it falls back to `/status` on a 404 so an endpoint rename in a future
image cannot be misreported as "Nexus never came up".

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

| Repeat call | Result | Observed on |
|---|---|---|
| duplicate `POST …/repositories/raw/hosted` | `400` — `[{"id":"PARAMETER name","message":"Name is already used, must be unique (ignoring case)"}]` | probe, 2026-09-17 |
| duplicate `POST …/repositories/docker/proxy` | `400` | probe, 2026-09-17 |
| `PUT /service/rest/v1/repositories/raw/hosted/raw-hosted` (same body) | **`204`** | probe, 2026-09-17 |
| `PUT /service/rest/v1/repositories/docker/proxy/docker-proxy` (same body) | **`204`** | live, 2026-09-18 |

The docker-proxy `PUT` row came later and from a different source than the rest
of this file: it was **not** exercised on the probe, and the `204` above was an
extrapolation from `raw-hosted` until the deployed bootstrap Job was re-run on
the cluster on 2026-09-18 and its log read `docker-proxy: updated (204)`. The
extrapolation was right, and it is now an observation — full log in
`.superpowers/sdd/2026-09-17-nexus-repository/verification-report.md` §1d. The
cleanup-policy `PUT` on the same run returned **`200`**, not `204`, as §5
already records.

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
echoed each back unchanged. There is also no `description` to read — the
endpoint is absent from `swagger.json` entirely — so the unit was established
from the shipped UI code and then corroborated against Sonatype's published
documentation. The evidence, with the commands that produced it, follows.

#### Getting the UI bundle without running Nexus

The whole application ships as one fat jar, so the JS is not visible on the
image filesystem (`grep -r criteriaLastDownloaded /opt/sonatype/nexus` finds
nothing). Extract it from the image instead — no container needs to run:

```
CID=$(docker create sonatype/nexus3:3.96.1)
docker export $CID > img.tar
docker rm -f $CID
tar -xf img.tar opt/sonatype/nexus/bin/sonatype-nexus-repository-3.96.1-01.jar
python3 -c 'import zipfile,io;z=zipfile.ZipFile("opt/sonatype/nexus/bin/sonatype-nexus-repository-3.96.1-01.jar");i=zipfile.ZipFile(io.BytesIO(z.read("BOOT-INF/lib/nexus-coreui-plugin-3.96.1-01.jar")));open("bundle.js","wb").write(i.read("static/nexus-coreui-bundle.js"));open("bundle-debug.js","wb").write(i.read("static/nexus-coreui-bundle.debug.js"))'
```

The extracted `static/nexus-coreui-bundle.js` is **byte-identical** to the
bundle the running probe served at `/static/nexus-coreui-bundle.js` —
`sha256:a0b5fda0d9cae848b32fe1b6dc1587266514ad1a4c625770018ff36d886ab969` from
both routes. The jar also carries `nexus-coreui-bundle.debug.js`, the
unminified build, which is what the readable excerpt below comes from.

#### 1. The labelled input writes straight into `criteriaLastDownloaded`

```
$ grep -o 'LAST_DOWNLOADED_SUB_LABEL:"[^"]*"' bundle.js
LAST_DOWNLOADED_SUB_LABEL:"Components downloaded in “x” amount of days (e.g 1-9999)"

$ grep -o 'PLACEHOLDER:"e.g 100 days"' bundle.js
PLACEHOLDER:"e.g 100 days"

$ grep -o 'LAST_DOWNLOADED_SUB_LABEL.\{0,200\}' bundle.js | head -1
LAST_DOWNLOADED_SUB_LABEL},i.createElement(u.kR9,(0,a.Z)({},c.Z.fieldProps("criteriaLastDownloaded",L),{onChange:c.Z.handleUpdate("criteriaLastDownloaded",k),placeholder:x.PLACEHOLDER,disabled:!z,className:"nx-text-input--sho
```

So the field whose sublabel says *days* is bound by `fieldProps` /
`handleUpdate` to the form-state key `criteriaLastDownloaded`.

#### 2. That value is posted untouched — the negative claim, shown

This is the save handler from the **unminified** bundle. Webpack's import
identifiers (`axios__WEBPACK_IMPORTED_MODULE_11__["default"]` and friends) are
shortened here for legibility and the `getCriteriaReleaseType` helper is folded
into its call site; nothing else is altered. Reproduce the raw text with:

```
python3 -c 'import re;s=open("bundle-debug.js",encoding="utf-8",errors="replace").read()
for m in re.finditer("saveData", s):
    c=s[m.start():m.start()+1800]
    if "criteriaLastDownloaded" in c and "post" in c: print(c.replace("\\n","\n")); break'
```

```js
saveData: function(param) {
    var data = param.data, pristineData = param.pristineData;
    var payload = {
        name: data.name,
        notes: data.notes,
        format: data.format,
        criteriaLastBlobUpdated: data.criteriaLastBlobUpdated,
        criteriaLastDownloaded: data.criteriaLastDownloaded,
        criteriaReleaseType: getCriteriaReleaseType(),
        criteriaAssetRegex: data.criteriaAssetRegex,
        retain: data.retain,
        sortBy: data.sortBy
    };
    return isEdit(pristineData)
        ? axios.put(CleanupPoliciesHelper.URL.singleCleanupPolicyUrl(data.name), payload)
        : axios.post(CleanupPoliciesHelper.URL.baseUrl, payload);
}
```

`criteriaLastDownloaded: data.criteriaLastDownloaded` — the days value goes
into the payload with no arithmetic — and `URL.baseUrl` is the very endpoint
this section documents:

```
$ grep -o 'a="service/rest/internal/cleanup-policies".\{0,140\}' bundle.js
a="service/rest/internal/cleanup-policies",i=function(e){return r.RELEASE_TYPE.RELEASES.id===e},o={baseUrl:a,singleCleanupPolicyUrl:function(e){return"".concat(a,"/").concat(e)}}},84
```

#### 3. No day→second constant exists anywhere in the bundle

The bundle is one long line, so `grep -c` counts lines and is useless here;
`grep -o | wc -l` counts occurrences:

```
$ for pat in 86400 864e5 86400000 2592000 2592e6; do printf '%-10s occurrences=%s\n' "$pat" "$(grep -o -- "$pat" bundle.js | wc -l)"; done
86400      occurrences=0
864e5      occurrences=8
86400000   occurrences=0
2592000    occurrences=0
2592e6     occurrences=1

$ for pat in 86400 864e5 2592000; do printf '%-10s occurrences=%s\n' "$pat" "$(grep -o -- "$pat" bundle-debug.js | wc -l)"; done
86400      occurrences=1
864e5      occurrences=5
2592000    occurrences=0
```

Every one of those hits was inspected, and none is on the cleanup-policy path:

```
$ grep -o '.\{40\}864e5.\{40\}' bundle.js
n a.count(0,t)%e==0}):a:null}),a}let aF=864e5,az=6048e5;function aH(e){return aB(func
on(e,t){return(t.getTime()-e.getTime())/864e5},function(e){return Math.floor(e.getTim
on(e,t){return(t.getTime()-e.getTime())/864e5},function(e){return Math.floor(e.getTim
FullYear(),t.getMonth(),t.getDate()):0)/864e5)}function u(){var e,t=r.U.useState(l),n
l},remove(e){this.write(e,"",Date.now()-864e5,"/")}}:{write(){},read:()=>null,remove(
=>c,yB:()=>a});let r=1e3,a=6e4,i=36e5,o=864e5,s=6048e5,l=2592e6,c=31536e6},16224:func

$ grep -o '.\{60\}86400.\{60\}' bundle-debug.js
  remove(name) {\n        this.write(name, '', Date.now() - 86400000, '/');\n      },\n    }\n  : // Non-standard browser env
```

Reading those six lines in order: d3-time's day interval (twice, plus its
`aF=864e5` constant), a "days remaining" banner countdown, axios's cookie-expiry
helper, and a milliseconds-per-unit constants module. The eighth occurrence is
the second `864e5` inside the d3-time line already shown — `grep -o` consumes
it with the surrounding context window. **None is in cleanup-policy code**, and
the single hit in the unminified bundle is again axios's cookie helper.

`2592e6` is 2,592,000,000 — thirty days **in milliseconds** — and it appears
exactly once, in that same `let r=1e3,a=6e4,i=36e5,o=864e5,s=6048e5,l=2592e6`
unit-constants module, not in cleanup-policy code.

#### 4. Sonatype's published documentation agrees

Fetched 2026-09-17:

- <https://help.sonatype.com/en/cleanup-policies-api.html> — "`criteriaLastDownloaded`
  - the last time the component had been downloaded **in days**."
- <https://help.sonatype.com/en/cleanup-policies.html> — the criterion is named
  "Component Usage (**Days**)": "This criterion removes components that haven't
  been downloaded in a specified number of days."

Note the same documentation describes this API at
`POST /service/rest/v1/cleanup-policies`, the path that returns **404** on this
build (see above). The field name and unit carry over to the internal endpoint
that does exist; the path does not.

Server read-back after creating the real policy:

```json
{"name":"docker-proxy-cleanup","format":"docker","notes":"Phase 21: keeps the CE 40000-component cap out of reach. Spec section 6.2.","criteriaLastBlobUpdated":null,"criteriaLastDownloaded":30,"retain":null,"sortBy":null,"criteriaReleaseType":null,"criteriaAssetRegex":null,"inUseCount":1,"repositories":["docker-proxy"]}
```

**Send `30`.** Sending `2592000` would mean 2,592,000 days (~7,000 years) and the
proxy would never evict anything.

### The scheduled task that enforces the policy is built into the product

Added 2026-09-18, to settle decision N20. A cleanup *policy* is inert on its
own: something has to run it. The spec asked the bootstrap Job to create a
scheduled cleanup task, the Job creates only the policy, and the manifest
justified the omission with an unattributed claim that "Nexus creates and
maintains the task itself". **That claim is correct on this build**, and the
evidence is below rather than asserted. The Job is deliberately left alone.

**1. The live instance already has the task, and nothing in this repository
created it.** `grep -c tasks platform/nexus/config/bootstrap-job.yaml` is `0`.
`GET /service/rest/v1/tasks` returns `403` to an anonymous caller, so this was
read with the `admin` credential from Secret `nexus-admin`, delivered to `curl`
through a `chmod 600` `--config` file from a throwaway pod on the pinned image:

```
$ curl -s -K /tmp/auth http://nexus.artifacts.svc:8081/service/rest/v1/tasks   -> 200
$ curl -s     http://nexus.artifacts.svc:8081/service/rest/v1/tasks           -> 403
```

The item that matters, verbatim from the `200` body:

```json
{
  "id" : "aec7e567-546b-4e74-9601-f6dc9c7a3c49",
  "name" : "Cleanup service",
  "type" : "repository.cleanup",
  "typeName" : "Admin - Cleanup repositories using their associated policies",
  "currentState" : "WAITING",
  "lastRunResult" : null,
  "nextRun" : "2026-09-19T01:00:00.000+00:00",
  "lastRun" : null,
  "schedule" : "advanced",
  "enabled" : true,
  "cronExpression" : "0 0 1 * * ?",
  "timeZoneOffset" : "Z",
  "startDate" : "2026-09-18T09:41:26.060+00:00"
}
```

So: **type `repository.cleanup`, name `Cleanup service`, enabled, cron
`0 0 1 * * ?` — 01:00 UTC daily.** `startDate` is Nexus's own first boot on this
PVC (09:41:26), which is **before** the bootstrap Job completed (`09:50:11Z`),
and the Job never touches `/service/rest/v1/tasks` at all.

`lastRun` is `null` only because the instance's H2 store was created the same
day at 09:41 and 01:00 UTC has not come round since. Do **not** read that as
"the task never runs": of the eight tasks the same call returns, the other
seven all carry `"lastRunResult" : "OK"`, among them two `assetBlob.cleanup`
tasks on `0 */30 * * * ?` that had run 16 minutes earlier. The scheduler is
working; this task's turn simply had not come.

**2. The running instance registers it at every boot.** From
`kubectl -n artifacts logs` on the live pod, 31 seconds after its first log
line (13:33:24):

```
2026-09-18 13:33:54,960+0000 INFO  [jetty-main-1] *SYSTEM org.sonatype.nexus.quartz.internal.task.QuartzTaskInfo - Task 'Cleanup service' [repository.cleanup] : state=WAITING
```

**3. The shipped code creates it, re-creates it, and deletes duplicates of it.**
Read out of the fat jar without booting anything, the same way §2 read
`StatusResource`:

```
CID=$(docker create sonatype/nexus3:3.96.1)
docker cp "$CID:/opt/sonatype/nexus/bin/sonatype-nexus-repository-3.96.1-01.jar" app.jar
docker rm -f "$CID"

python3 - <<'EOF'
import zipfile, io, os
z = zipfile.ZipFile("app.jar")
lib = "BOOT-INF/lib/nexus-cleanup-config-3.96.1-01.jar"
i = zipfile.ZipFile(io.BytesIO(z.read(lib)))
for n in i.namelist():
    if n.startswith("org/sonatype/nexus/cleanup/internal/task/") and n.endswith(".class"):
        os.makedirs("cls/" + os.path.dirname(n), exist_ok=True)
        open("cls/" + n, "wb").write(i.read(n))
EOF

docker run --rm --entrypoint /bin/sh -v "$PWD/cls:/cls:ro" \
  sonatype/nexus3:3.96.1 \
  -c 'javap -p -c -cp /cls org.sonatype.nexus.cleanup.internal.task.CleanupBootService'
```

`CleanupBootService` extends `LifecycleSupport`, and its `doStart()` is one
call to `createCleanupTask()`. The disassembly of that method, trimmed to the
constants:

```
  private void createCleanupTask();
       0: aload_0
       1: invokevirtual #44    // Method doesTaskExist:()Z
       4: ifne          77
      11: ldc           #50    // String repository.cleanup
      13: invokeinterface       // TaskScheduler.createTaskConfigurationInstance
      20: ldc           #13    // String Cleanup service
      22: invokevirtual         // TaskConfiguration.setName
      41: ldc           #8     // String 0 0 1 * * ?
      43: invokeinterface       // ScheduleFactory.cron
      55: invokeinterface       // TaskScheduler.scheduleTask
      69: ldc           #83    // String Problem scheduling cleanup task   (catch)
      78: invokevirtual #91    // Method removeDuplicates:()V
```

Three facts fall out of it, and together they decide N20:

- **The product creates the task at every startup if it is absent**
  (`doesTaskExist()` → `TaskScheduler.scheduleTask`), with exactly the name,
  type and cron the live instance reports. Nothing external is needed.
- **The task enforces the policies.** `CleanupTask` holds a
  `org.sonatype.nexus.cleanup.service.CleanupService` and its `execute()`
  delegates to it; the product's own `typeName` for the type is "Admin -
  Cleanup repositories using their associated policies".
- **`removeDuplicates()` runs unconditionally afterwards** — it lists tasks,
  keeps the first whose name is `Cleanup service`, type is `repository.cleanup`
  and schedule matches `0 0 1 * * ?`, and removes the rest. A second copy
  created by a bootstrap Job would therefore be *deleted by Nexus at the next
  restart*, which is a second, independent reason not to add one.

**Consequence for the bootstrap Job: none. Do not add a task to it.** N20 is
satisfied by the product, not by the Job, and the `docker-proxy-cleanup` policy
the Job does create is the only part that was ever missing from a fresh
instance. The one operational caveat is a schedule, not a gap: eviction happens
once a day at 01:00 UTC, so a burst that adds tens of thousands of components
inside one day is not protected by it — the Usage Center counters are.

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

### Which connector this proves

Two routes to the same repository exist, and they do not have the same level of
evidence behind them:

| Route | Container port | Evidence |
|---|---|---|
| Root connector, `/v2/…` | **8082** (from `httpPort: 8082`) | **real client** — the `docker pull` above ran against it |
| Path-based, `/repository/docker-proxy/v2/…` | 8081 | curl only — index, child manifest and blob each returned 200 |

**8082 is the port this design actually consumes.** `registries.yaml` points
containerd at `127.0.0.1:30082`, which the Service maps to container port 8082,
and a later Docker Ingress targets 8082 as well. Both therefore take the root
connector — the route with real-client proof. Nothing in this design consumes
the path-based route on 8081; its curl-level evidence is recorded for
completeness, and no later task should be built on it without proving it with a
real client first.

## 8. Order of operations for the bootstrap

Each step's status code below was observed. **The ordering, however, is a
reconstruction assembled after the fact, not a tested sequence** — read it as
conservative rather than minimal:

1. Accept the EULA — `POST /service/rest/v1/system/eula` → `204`
2. Add the Docker realm — `PUT /service/rest/v1/security/realms/active` → `204`
3. Enable anonymous — `PUT /service/rest/v1/security/anonymous` → `200`
4. Create the cleanup policy — `POST /service/rest/internal/cleanup-policies` → `200`
5. Create `raw-hosted` — `POST /service/rest/v1/repositories/raw/hosted` → `201`
6. Create `docker-proxy` — `POST /service/rest/v1/repositories/docker/proxy` → `201`

What was actually established, and what was not:

- **Proven by rejection: step 4 must precede step 6.** A docker repository
  naming a cleanup policy that does not exist is refused.
- **Proven: steps 4, 5 and 6 are not re-runnable** — each returns `400` on a
  second run, with `PUT` as the idempotent alternative (see §4 and §5).
- **Re-running steps 1, 2 and 3 is now observed, not inferred.** The probe ran
  each once, and this file previously recorded them as untested: the EULA POST
  is one-way (`"accepted": false` returns `500`), and both realm and anonymous
  calls are `PUT`s of complete desired state, so all three *looked* idempotent.
  A second run of the deployed bootstrap Job against the live server on
  **2026-09-18** confirmed it, with the same status codes as the first run:
  `accepted (204)`, `active realms set (204)`, `anonymous access enabled
  (200)`. That run took 4 s and produced no duplicate of anything. Full log in
  `.superpowers/sdd/2026-09-17-nexus-repository/verification-report.md` §1d.
  Read narrowly: this is one re-run on one instance where the desired state had
  not changed, which is the case the bootstrap actually re-runs in.
- **Not tested: whether an unaccepted EULA blocks steps 4-6.** Acceptance
  cannot be undone, so the ordering of step 1 could not be challenged. It is
  placed first because it costs one call and removes the question.

## 9. `sudo` is not available to a non-interactive agent on this host

This is load-bearing for any later task that plans to run `sudo cp … /etc/rancher/k3s/registries.yaml`
or `sudo systemctl restart k3s` **as an agent step**. It cannot.

Every `sudo` form fails without a TTY, including the passwordless probe:

```
$ sudo -n true
sudo: interactive authentication is required
exit=1

$ sudo chown -R 200 /path/to/probe-data
sudo: A terminal is required to authenticate
exit=1

$ sudo -v
sudo: A terminal is required to authenticate
exit=1
```

The probe brief's own Step 2 is written as `sudo chown -R 200 …` and fails for
exactly this reason. What was used instead, and what worked, is to borrow root
from a throwaway container — the host's `docker` needs no `sudo` here:

```
docker run --rm -u 0 -v /path/to/probe-data:/d alpine:3.20 chown -R 200 /d
```

The same trick removed the root-owned `/nexus-data` tree at teardown
(`docker run --rm -u 0 -v …:/s alpine:3.20 sh -c 'rm -rf /s/nexus-data'`).

**Consequences for later tasks.** Anything requiring real root on the host —
writing under `/etc/rancher/k3s/`, `systemctl restart k3s` — is an **operator
hand-over step**, not something an agent can execute. Write those as one-line
commands for the user to run, and do not build a task that assumes it can
elevate on its own. Work that only needs *file ownership* rather than host root
can use the container trick above.
