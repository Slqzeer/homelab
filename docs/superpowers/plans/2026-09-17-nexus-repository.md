# Nexus Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Nexus Repository CE serving `raw-hosted` for versioned build outputs
and `docker-proxy` as a pull-through cache that k3s mirrors through, with its
admin credential generated in Vault, delivered by VSO, and rotated by a Job that
lives in git rather than by hand in a web UI.

**Architecture:** A `Recreate` Deployment on a pinned `sonatype/nexus3` tag with
one RWO PVC holding H2, blobs and config. An idempotent Argo CD `Sync`-hook Job
accepts the CE EULA, rotates the admin password from the boot-generated one to
the Vault-delivered one, and reconciles both repositories plus a cleanup policy
over the REST API. containerd reaches the Docker proxy through a NodePort on
`127.0.0.1`; tailnet clients reach it through its own Ingress hostname.

**Tech Stack:** k3s v1.36.4+k3s1, containerd v2.3.4-k3s1.36, Argo CD v3.5.2,
Vault 2.0.4, VSO 1.5.1, Sonatype Nexus Repository CE (`sonatype/nexus3`, exact
tag chosen in Task 2).

**Spec:** `docs/superpowers/specs/2026-09-17-nexus-repository-design.md`

## Global Constraints

- **Never run `git push`.** The user reserves all pushes. Commit locally, print
  the exact push command, and wait.
- **`kubectl` needs a group wrapper on this host:** `sg k3s-admin -c '...'`.
  Nested quoting inside it is fragile — for anything multi-line, write a script
  to the scratchpad and run that.
- **Commands the user must edit and run must be shell-safe as written.** Never
  use `<angle brackets>` as a placeholder; `<` is a shell redirection and has
  broken this user's shell twice. Keep such commands on one line.
- **No credential may become a command argument.** Use the `key=@<path>` form
  the unsealer and `configure-vault.sh` already use, so only a filename appears
  in `ps`.
- **The admin password is never printed, echoed, or pasted into the
  conversation.**
- **Never grep `ps` output for the password.** The pattern lands in the grep's
  own argv and `ps` matches the grep itself, reporting a leak that is not there.
  Read the full `ps` output instead.
- **Image pinned to an exact `sonatype/nexus3` tag.** No `latest`, ever. The tag
  is chosen in Task 2 and every later task uses that same tag.
- **Embedded H2. This phase creates no database user and touches PostgreSQL in
  no way.** If it does, it has gone wrong — see spec N4.
- **Sync waves after this plan:** `argocd` -1, `namespaces` 0,
  `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21,
  `vso-config` 22, `postgres` 23, `redis` 23, `registry` 23, **`nexus` 23**,
  `beacon` 24.
- **A directory used as an Argo CD `path` may contain only valid manifests.**
- **Argo CD reporting Synced/Healthy proves only what it applied.** Every
  verification asserts on running state — `status.resources`, a live object, an
  actual command against the running server.
- Argo CD polls every 3 minutes. Poll for existence before `kubectl wait`.
- **Nexus REST payload shapes vary by version.** Sonatype does not publish the
  per-format repository bodies and directs you to the instance's own Swagger.
  Task 2 captures them from the pinned tag; later tasks use what Task 2
  recorded, not what this plan guessed.
- Tailnet domain is `taildf6cd4.ts.net`. Ingress `tls.hosts` carries the **short
  name only**; MagicDNS supplies the rest.
- Scratchpad for temporary files:
  `/tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad`

---

### Task 1: The Vault credential, the namespace, and the directory

**Files:**
- Modify: `platform/vault/configure-vault.sh`
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Rename: `platform/artifactory/` → `platform/nexus/`

**Interfaces:**
- Produces: Vault KV path `homelab/nexus` holding `username` and `password`;
  policy `vso-nexus-read` on `homelab/data/nexus`; role `vso-nexus` bound to
  `system:serviceaccount:artifacts:nexus` with audience `vault`. Task 3's
  `VaultAuth` must name exactly that role and that audience. Namespace
  `artifacts`.

**Why the script is extended rather than duplicated.** `configure-vault.sh` is
Vault's one ceremony, and phases 18, 19 and 20 each extended it for exactly this
reason: a second script would be a second thing to remember on a rebuild.

**Why the directory is renamed.** `platform/artifactory/` holds a lone
`.gitkeep` and the product is not Artifactory. Spec §2 records why the roadmap's
word is kept while the roadmap's product is not.

- [ ] **Step 1: Read the existing script and record the "before" measurement**

```bash
cat platform/vault/configure-vault.sh
free -h
df -h /srv
sg k3s-admin -c 'kubectl get applications -n argocd'
sg k3s-admin -c 'kubectl get pvc -A'
```

Note the script's shape: `set -eu`, POSIX `sh`, and every guard written as the
*test* of an `if` so `set -e` does not abort on a non-zero `vault kv get`. Match
it exactly.

Record `free -h` and the PVC list verbatim in your report. They are the "before"
half of spec §17.9, which requires memory to be measured rather than estimated.

- [ ] **Step 2: Rename the directory**

```bash
git mv platform/artifactory platform/nexus
ls -la platform/nexus
```

Expected: `.gitkeep` present, `platform/artifactory` gone.

- [ ] **Step 3: Add the `artifacts` namespace**

Append to `bootstrap/namespaces/namespaces.yaml`:

```yaml
---
# Artifact repositories. Holds Nexus Repository CE (phase 21), which fills the
# roadmap's "Artifactory" role -- see docs/superpowers/specs/
# 2026-09-17-nexus-repository-design.md section 2 for why the product differs
# from the plan's word.
#
# Not `databases`: Nexus is not a data service. Not `apps`: that namespace is
# for workloads this homelab's own CI builds. A separate namespace is also a
# REQUIREMENT, not a preference -- the VaultAuth CRD states its ServiceAccount
# "must reside in the consuming secret's namespace".
apiVersion: v1
kind: Namespace
metadata:
  name: artifacts
```

Mind the file's own warning at the top: `namespaces` has `prune: true`, so
removing this entry later deletes the namespace and cascades to the PVC.

- [ ] **Step 4: Extend the ceremony script**

Append to `platform/vault/configure-vault.sh`, immediately before the final
`echo "==> done"` line. This mirrors the `ghcr` block directly above it:

```sh
echo "==> seeding homelab/nexus"
if vault kv get homelab/nexus >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed, exactly as the postgres and redis
  # blocks above.
  #
  # Alphanumeric only. Nexus accepts more, but this password is sent in a
  # `change-password` request body and pasted into browser logins; a / or @
  # survives neither round trip predictably.
  #
  # Written to a file so that only the FILENAME becomes an argument -- a
  # password on a command line is visible in `ps`.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/nexus username=admin password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-nexus-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-nexus-read - <<'POLICY'
path "homelab/data/nexus" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-nexus"
# bound_service_account_names must match the ServiceAccount created in
# platform/nexus/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-nexus \
    bound_service_account_names=nexus \
    bound_service_account_namespaces=artifacts \
    audience=vault \
    token_policies=vso-nexus-read \
    ttl=1h
```

`username=admin` is not decorative: `admin` is the actual Nexus account whose
password this is, and Task 4's Job authenticates as that user. Storing it makes
the KV entry self-describing and matches `homelab/postgres`.

- [ ] **Step 5: Verify the script still parses, and that the guard is a guard**

```bash
sh -n platform/vault/configure-vault.sh && echo "PARSE OK"
grep -c 'mktemp' platform/vault/configure-vault.sh
grep -n 'vault kv get homelab/nexus' platform/vault/configure-vault.sh
grep -n 'homelab/data/nexus' platform/vault/configure-vault.sh
```

Expected: `PARSE OK`; `mktemp` count is **3** (postgres, redis, nexus — ghcr is
seeded by hand and has none); the `vault kv get` appears inside an `if`, not on
its own line; the policy path contains `data/`. A seed that is not guarded
overwrites a live credential on every rebuild, and a KV v2 policy without
`data/` matches nothing while reporting no error.

- [ ] **Step 6: Run the ceremony against the live Vault**

Write the script to the scratchpad first; do not attempt to nest it inside
`sg ... -c`.

```bash
sg k3s-admin -c 'kubectl exec -i -n vault vault-0 -- sh -s' < platform/vault/configure-vault.sh
```

Expected output ends with `==> seeding homelab/nexus` / `generated`, then the
policy and role lines, then `==> done`. **No password appears anywhere in the
output.** If one does, stop and report it.

- [ ] **Step 7: Confirm the credential and role exist without printing the password**

```bash
sg k3s-admin -c 'kubectl exec -n vault vault-0 -- vault kv get -field=username homelab/nexus'
sg k3s-admin -c 'kubectl exec -n vault vault-0 -- vault read -field=bound_service_account_namespaces auth/kubernetes/role/vso-nexus'
sg k3s-admin -c 'kubectl exec -n vault vault-0 -- vault policy read vso-nexus-read'
```

Expected: `admin`; `artifacts`; a policy whose path is `homelab/data/nexus`.
Note that only `-field=username` is read — never `-field=password`, and never a
bare `vault kv get`, which prints every field.

- [ ] **Step 8: Re-run the ceremony to prove the guard**

```bash
sg k3s-admin -c 'kubectl exec -i -n vault vault-0 -- sh -s' < platform/vault/configure-vault.sh
```

Expected: `already present, leaving the credential alone`. This is spec §17.4's
first half.

- [ ] **Step 9: Commit**

```bash
git add platform/vault/configure-vault.sh bootstrap/namespaces/namespaces.yaml platform/nexus
git commit -m "Add the Nexus admin credential's Vault policy, role and seed

Extends the one Vault ceremony rather than adding a second script, and
creates the artifacts namespace Nexus needs its own VaultAuth in.
platform/artifactory/ is renamed to platform/nexus/ because the product
filling the roadmap's Artifactory role is Nexus -- spec section 2.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

---

### Task 2: Pin the image, measure it, and capture the REST schemas

**Files:**
- Create: `/tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-probe.md` (working notes; not committed)
- Create: `platform/nexus/rest-schemas.md`

**Interfaces:**
- Produces: the exact image tag every later task uses; measured startup time and
  RSS that Task 3's `startupProbe` and resource limits are derived from; and the
  exact request bodies Task 4 posts.

**Why this is its own task.** Spec §15 deliberately leaves the tag unfixed, and
spec §7 states plainly that the JVM numbers are reasoned rather than cited.
Writing manifests before measuring would bake a guess into git. Sonatype also
declines to publish the per-format repository payloads, directing users to the
running instance's Swagger — so the schemas must come from the pinned version
itself. This task runs entirely in Docker on the host; it touches neither the
cluster nor Argo CD.

- [ ] **Step 1: Choose the tag**

```bash
curl -s 'https://hub.docker.com/v2/repositories/sonatype/nexus3/tags?page_size=25&ordering=last_updated' | python3 -c 'import json,sys; [print(t["name"], t["last_updated"]) for t in json.load(sys.stdin)["results"]]'
```

Pick the newest tag that is a plain three-part version (for example
`3.xx.y-zz`). Reject `latest`, and reject any tag carrying a suffix such as
`-java11`, `-alpine` or `-ubi`. Record the chosen tag and today's date; every
later task substitutes it wherever this plan writes `NEXUSTAG`.

- [ ] **Step 2: Start it with the spec's proposed JVM settings**

```bash
mkdir -p /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-data
sudo chown -R 200 /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-data
docker run -d --name nexus-probe -p 18081:8081 --memory=2500m -e INSTALL4J_ADD_VM_PARAMS='-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m' -v /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-data:/nexus-data sonatype/nexus3:NEXUSTAG
date +%s > /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-start
```

`--memory=2500m` mirrors the `limits.memory` the spec proposes, so this probe
tests the real ceiling rather than an unconstrained JVM. `chown -R 200` mirrors
`fsGroup: 200` and is the same trap as troubleshooting entry 1.

- [ ] **Step 3: Measure startup time**

```bash
until curl -sf http://127.0.0.1:18081/service/rest/v1/status >/dev/null; do sleep 5; done; echo "READY after $(( $(date +%s) - $(cat /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-start) )) seconds"
docker stats --no-stream nexus-probe
```

Record both numbers. **The startup time sets Task 3's `startupProbe`
`failureThreshold`**: take the measured seconds, divide by a 10-second
`periodSeconds`, and double it, because this host has a recorded history of
probe timeouts under memory pressure (troubleshooting entries 4 and 10).

If the container was OOM-killed instead (`docker inspect nexus-probe --format
'{{.State.OOMKilled}}'` reports `true`), spec §7 is explicit about the remedy:
**raise `--memory`, not the heap.** Re-measure and record the value that works.

- [ ] **Step 4: Capture the Swagger schemas**

```bash
cat /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-data/admin.password
```

Use that password as `ADMINPW` below. Then:

```bash
curl -s -u admin:ADMINPW http://127.0.0.1:18081/service/rest/swagger.json > /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/swagger.json
python3 -c 'import json; d=json.load(open("/tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/swagger.json")); [print(json.dumps({k: d["definitions"][k]}, indent=2)) for k in ("RawHostedRepositoryApiRequest","DockerProxyRepositoryApiRequest","CleanupPolicyResource") if k in d.get("definitions", {})]'
```

If a definition name is absent, list what is there and pick the matching one:

```bash
python3 -c 'import json; d=json.load(open("/tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/swagger.json")); [print(k) for k in sorted(d.get("definitions", {})) if "Raw" in k or "Docker" in k or "Cleanup" in k]'
```

- [ ] **Step 5: Prove the payloads against the running probe**

These are the bodies Task 4 will send. Confirm each returns 201 here before
committing them. Create the cleanup policy first — the Docker repository
references it by name, and a reference to a policy that does not exist is
rejected.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -u admin:ADMINPW -H 'Content-Type: application/json' -d '{"name":"docker-proxy-cleanup","format":"docker","notes":"Phase 21: keeps the CE 40000-component cap out of reach. Spec section 6.2.","criteriaLastDownloaded":30}' http://127.0.0.1:18081/service/rest/v1/cleanup-policies
curl -s -o /dev/null -w '%{http_code}\n' -X POST -u admin:ADMINPW -H 'Content-Type: application/json' -d '{"name":"raw-hosted","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true,"writePolicy":"ALLOW"},"cleanup":{"policyNames":[]},"component":{"proprietaryComponents":false},"raw":{"contentDisposition":"ATTACHMENT"}}' http://127.0.0.1:18081/service/rest/v1/repositories/raw/hosted
curl -s -o /dev/null -w '%{http_code}\n' -X POST -u admin:ADMINPW -H 'Content-Type: application/json' -d '{"name":"docker-proxy","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true},"cleanup":{"policyNames":["docker-proxy-cleanup"]},"proxy":{"remoteUrl":"https://registry-1.docker.io","contentMaxAge":1440,"metadataMaxAge":1440},"negativeCache":{"enabled":true,"timeToLive":1440},"httpClient":{"blocked":false,"autoBlock":true},"docker":{"v1Enabled":false,"forceBasicAuth":false,"httpPort":8082},"dockerProxy":{"indexType":"HUB","cacheForeignLayers":false}}' http://127.0.0.1:18081/service/rest/v1/repositories/docker/proxy
```

Expected: `201` three times. A `400` means this version's schema differs from
the body above — read the Swagger definition from Step 4, correct the body, and
record the corrected one. **Do not carry a body forward that has not returned
201 here.**

`forceBasicAuth: false` is what routes Docker clients through the Bearer Token
realm rather than HTTP basic auth, which is what makes anonymous pulls work. It
is the setting spec §9 step 5 is about.

- [ ] **Step 6: Confirm the Docker Bearer Token realm is active**

```bash
curl -s -u admin:ADMINPW http://127.0.0.1:18081/service/rest/v1/security/realms/active
curl -s -u admin:ADMINPW http://127.0.0.1:18081/service/rest/v1/security/realms/available | python3 -m json.tool
```

If the active list does not contain the Docker bearer-token realm, record its
exact id from the `available` list and the PUT that adds it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X PUT -u admin:ADMINPW -H 'Content-Type: application/json' -d '["NexusAuthenticatingRealm","NexusAuthorizingRealm","DockerToken"]' http://127.0.0.1:18081/service/rest/v1/security/realms/active
```

Expected: `204`. Replace `DockerToken` with the exact id the `available`
endpoint reported if it differs. Whether this call is needed is recorded in
`rest-schemas.md` — Task 4 includes it only if it was needed here.

- [ ] **Step 7: Prove an anonymous pull works through the probe**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18081/repository/docker-proxy/v2/
```

Expected: `200`. A `401` means anonymous read is off or the realm is missing;
fix it here and record what fixed it. This is the single question that decides
whether Task 6's mirror can work at all, and finding out now costs a container
restart instead of a cluster restart.

- [ ] **Step 8: Record everything, then tear the probe down**

Write `platform/nexus/rest-schemas.md` containing: the chosen tag and the date
probed; measured startup seconds and the `failureThreshold` derived from it;
measured RSS and whether the 2500m ceiling held; the three verified request
bodies exactly as they returned 201; whether the realm PUT was needed and the
exact realm id; and the anonymous-pull status code.

```bash
docker rm -f nexus-probe
sudo rm -rf /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/nexus-data
```

- [ ] **Step 9: Commit**

```bash
git add platform/nexus/rest-schemas.md
git commit -m "Record the probed Nexus tag, sizing and REST payloads

Sonatype does not publish per-format repository bodies and points at the
running instance's Swagger, so these were taken from the pinned tag and
each verified to return 201 before being written down. Startup time and
RSS were measured against the same memory ceiling the manifests will set,
per spec section 7, which is explicit that those numbers were reasoned
rather than cited.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

---

### Task 3: The manifests

**Files:**
- Create: `platform/nexus/config/nexus.yaml`
- Create: `platform/nexus/config/vault-secrets.yaml`
- Create: `environments/homelab/apps/nexus.yaml`
- Delete: `platform/nexus/.gitkeep`

**Interfaces:**
- Consumes: from Task 1, role `vso-nexus`, policy `vso-nexus-read`, KV path
  `homelab/nexus`, namespace `artifacts`. From Task 2, the exact image tag and
  the `startupProbe` `failureThreshold`.
- Produces: Service `nexus.artifacts.svc` with ports `8081` (http) and `8082`
  (docker), NodePort `30082` on the docker port, PVC `nexus-data`, and Secret
  `nexus-admin` with keys `username` and `password`. Task 4's Job mounts that
  PVC and reads that Secret; Task 5's Ingresses name that Service and those
  ports; Task 6's `registries.yaml` uses NodePort 30082.

- [ ] **Step 1: Write `platform/nexus/config/vault-secrets.yaml`**

```yaml
# Nexus's credential path: Vault KV -> VSO -> Secret.
#
# All four objects live in `artifacts` and that is a requirement, not a
# preference: the VaultAuth CRD states its ServiceAccount "must reside in the
# consuming secret's namespace". This namespace therefore gets its own
# ServiceAccount, VaultConnection, VaultAuth and VaultStaticSecret rather than
# reusing the ones in `vault` or `databases`.
---
# The identity Vault trusts. Its name must match bound_service_account_names
# in platform/vault/configure-vault.sh.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: nexus
  namespace: artifacts
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: vault
  namespace: artifacts
spec:
  # Plain HTTP in-cluster: single node, so pod traffic never leaves the host.
  # skipTLSVerify is required by the CRD even when the address is http, where
  # it has no effect.
  address: http://vault.vault.svc:8200
  skipTLSVerify: false
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultAuth
metadata:
  name: nexus
  namespace: artifacts
spec:
  vaultConnectionRef: vault
  method: kubernetes
  mount: kubernetes
  kubernetes:
    # role and audiences must both match platform/vault/configure-vault.sh.
    # A mismatch in either is a permission denial that names neither side,
    # while Argo CD reports everything Synced.
    role: vso-nexus
    serviceAccount: nexus
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: nexus-admin
  namespace: artifacts
spec:
  vaultAuthRef: nexus
  mount: homelab
  type: kv-v2
  path: nexus
  refreshAfter: 60s
  # No _raw key. The two phase-18/19 VaultStaticSecrets predate this setting
  # and still carry one; this phase does not add a fourth instance of that
  # gap. Retrofitting the existing ones is deliberately out of scope.
  excludeRaw: true
  destination:
    name: nexus-admin
    create: true
```

- [ ] **Step 2: Write `platform/nexus/config/nexus.yaml`**

Substitute the tag and `failureThreshold` recorded in Task 2 where this shows
`NEXUSTAG` and `FAILURETHRESHOLD`.

```yaml
# Nexus Repository Community Edition -- phase 21, filling the roadmap's
# "Artifactory" role with a different product. Spec section 2 has the argument:
# no free JFrog edition covers what plan section 24 asks for, because
# Artifactory OSS has no Docker and JCR has no Maven/npm/PyPI.
#
# State is the embedded H2 database, the blob store and the configuration, all
# three inside the one PVC below. That is deliberate: Community Edition stops
# accepting components at 40,000, well under H2's own 100,000, so an external
# PostgreSQL would add a database user, a second credential path and a
# two-source backup while buying no headroom at all. See spec section 6.2.
---
apiVersion: v1
kind: Service
metadata:
  name: nexus
  namespace: artifacts
spec:
  # NodePort, not ClusterIP, and only because of the docker port. containerd
  # runs on the HOST: it does not use cluster DNS, so it cannot resolve
  # nexus.artifacts.svc, and routing its layer pulls through the tailnet proxy
  # would make every image pull depend on the Tailscale operator. A NodePort on
  # 127.0.0.1 needs neither. See spec section 8.
  type: NodePort
  selector:
    app: nexus
  ports:
    - name: http
      port: 8081
      targetPort: 8081
    - name: docker
      port: 8082
      targetPort: 8082
      # Pinned, not auto-assigned: /etc/rancher/k3s/registries.yaml names this
      # number and that file is not reconciled by Argo CD. A reassignment here
      # would break image pulls silently on the next rebuild.
      nodePort: 30082
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: nexus-data
  namespace: artifacts
spec:
  # A standalone PVC rather than a StatefulSet volumeClaimTemplate. This is a
  # Deployment (one replica, no stable network identity to preserve), and it
  # also sidesteps the atomic-list server-side-apply trap documented at length
  # in platform/databases/postgres/config/postgres.yaml.
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      # local-path does not enforce capacity -- a PV is a directory on /srv,
      # which has 851G free. Nominal, not a quota. Sized for cached Docker
      # layers, which are the bulk of what lands here.
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nexus
  namespace: artifacts
spec:
  replicas: 1
  strategy:
    # Recreate, NOT RollingUpdate. The PVC is ReadWriteOnce, so a rolling
    # update would start a second pod that blocks forever waiting for a volume
    # the outgoing pod still holds. The cost is a gap in service on every
    # image change, which is correct for a single-replica stateful workload.
    type: Recreate
  selector:
    matchLabels:
      app: nexus
  template:
    metadata:
      labels:
        app: nexus
    spec:
      serviceAccountName: nexus
      securityContext:
        # The image runs as uid 200, gid 200, with state at /nexus-data. This
        # is exactly troubleshooting entry 1's trap: a non-root container
        # cannot write a local-path volume on /srv unless fsGroup makes it
        # writable. That mechanism was proven on this cluster in phase 16.
        runAsNonRoot: true
        runAsUser: 200
        runAsGroup: 200
        fsGroup: 200
      containers:
        - name: nexus
          image: sonatype/nexus3:NEXUSTAG
          env:
            # Far below the image's documented default of
            # -Xms2703m -Xmx2703m -XX:MaxDirectMemorySize=2703m, which reserves
            # roughly 5.4Gi. This host had 7.7Gi free when the phase was
            # planned and is also a workstation, a KVM host and a future game
            # server, with a recorded CoreDNS outage caused by swap exhaustion.
            # Xms equals Xmx on Sonatype's own advice, to avoid heap resizing.
            #
            # These values were MEASURED against this exact tag, not assumed --
            # see platform/nexus/rest-schemas.md. If this pod is OOM-killed,
            # raise limits.memory, NOT the heap: heap, direct memory, metaspace,
            # code cache and thread stacks are all counted by the cgroup, so a
            # bigger heap under an unchanged limit moves the cliff closer.
            - name: INSTALL4J_ADD_VM_PARAMS
              value: "-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=768m"
          ports:
            - name: http
              containerPort: 8081
            - name: docker
              # Served by the docker-proxy repository's own connector, which
              # Task 4 configures through docker.httpPort. Nothing listens here
              # until that repository exists.
              containerPort: 8082
          resources:
            requests:
              cpu: 200m
              memory: 1.5Gi
            limits:
              # Memory only, no CPU limit -- the shape postgres.yaml uses.
              memory: 2.5Gi
          volumeMounts:
            - name: data
              mountPath: /nexus-data
          startupProbe:
            # Nexus is a JVM application that takes minutes to come up on this
            # host, and it answers this endpoint only once the REST API is
            # live. The threshold below is the measured startup time doubled --
            # see rest-schemas.md. A startupProbe rather than a long
            # initialDelaySeconds on readiness, so that a genuinely stuck pod
            # is still caught promptly once started.
            httpGet:
              path: /service/rest/v1/status
              port: 8081
            periodSeconds: 10
            failureThreshold: FAILURETHRESHOLD
          readinessProbe:
            httpGet:
              path: /service/rest/v1/status
              port: 8081
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            httpGet:
              path: /service/rest/v1/status
              port: 8081
            periodSeconds: 30
            timeoutSeconds: 10
            # Deliberately tolerant, for the reason postgres.yaml gives: this
            # host has a recorded history of probe timeouts under memory
            # pressure. Killing a repository manager because the host was
            # briefly busy is worse than waiting.
            failureThreshold: 6
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: nexus-data
```

- [ ] **Step 3: Write `environments/homelab/apps/nexus.yaml`**

```yaml
# Nexus Repository CE, the roadmap's phase 21. Spec section 2 records why the
# product is not the Artifactory the plan names.
#
# Wave 23 -- the SAME wave as postgres, redis and registry, not 24. Nexus
# depends on vso-config (22) for Secret nexus-admin and on nothing at 23.
# Both postgres.yaml and redis.yaml warn against stacking a wave "out of
# habit"; sharing one means these reconcile in parallel and none gates another.
#
# Nothing sits behind wave 23 that depends on Nexus, so an unhealthy repository
# manager gates nothing in Argo CD. Image pulls keep working regardless --
# containerd always tries the upstream registry as a last resort, which is what
# makes a cold rebuild possible at all. See spec section 6.4.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: nexus
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: platform/nexus/config
  destination:
    server: https://kubernetes.default.svc
    namespace: artifacts
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 4: Remove the placeholder and validate the manifests**

`platform/nexus/config` is an Argo CD `path`, so it may contain only valid
manifests. `rest-schemas.md` lives one level up, outside it, which is why
Task 2 put it there.

```bash
rm -f platform/nexus/.gitkeep
yamllint platform/nexus/config/ environments/homelab/apps/nexus.yaml
kubeconform -strict -ignore-missing-schemas platform/nexus/config/nexus.yaml
grep -n 'NEXUSTAG\|FAILURETHRESHOLD' platform/nexus/config/nexus.yaml
```

Expected: yamllint and kubeconform clean, and **the final grep finds nothing**.
A surviving placeholder means the tag or threshold from Task 2 was not
substituted.

- [ ] **Step 5: Commit and let Argo CD reconcile**

```bash
git add platform/nexus environments/homelab/apps/nexus.yaml
git commit -m "Deploy Nexus Repository CE at wave 23

Recreate Deployment on a pinned tag with one RWO PVC holding H2, blobs and
config; NodePort 30082 on the docker port because containerd runs on the
host and cannot use cluster DNS. JVM sized far below the shipped default
against measured numbers, not assumed ones.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

Then tell the user this needs pushing before Argo CD can see it, and print the
command on one line:

```bash
git push origin main
```

Wait for confirmation that it is pushed. Argo CD polls every 3 minutes.

- [ ] **Step 6: Verify the credential arrived before blaming the pod**

```bash
sg k3s-admin -c 'kubectl get vaultstaticsecret -n artifacts nexus-admin'
sg k3s-admin -c 'kubectl get secret -n artifacts nexus-admin -o jsonpath="{.data}" | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"'
```

Expected: the VaultStaticSecret exists, and the key list is exactly
`['password', 'username']`. **No `_raw`** — that is spec §17.11 verified rather
than assumed. If the Secret is missing, the fault is the role/audience/namespace
triple from Task 1, not the Deployment.

- [ ] **Step 7: Verify the pod reaches Ready and record what it cost**

```bash
sg k3s-admin -c 'kubectl get pods -n artifacts -w'
```

Wait for `1/1 Running`, then:

```bash
sg k3s-admin -c 'kubectl get application nexus -n argocd -o jsonpath="{.status.sync.status} {.status.health.status}"; echo'
sg k3s-admin -c 'kubectl get application nexus -n argocd -o jsonpath="{range .status.resources[*]}{.kind}/{.name}{\"\n\"}{end}"'
sg k3s-admin -c 'kubectl top pod -n artifacts'
free -h
```

Expected: `Synced Healthy`; `status.resources` lists the Deployment, Service,
PVC, ServiceAccount, VaultConnection, VaultAuth and VaultStaticSecret — a green
Application alone proves only what Argo CD applied. Record `kubectl top` and
`free -h`; they are the "after" half of the measurement started in Task 1.

If the pod is `OOMKilled` (`kubectl describe pod -n artifacts` shows the reason),
raise `limits.memory`, not the heap, and re-measure.

---

### Task 4: The bootstrap Job

**Files:**
- Create: `platform/nexus/config/bootstrap-job.yaml`

**Interfaces:**
- Consumes: from Task 3, PVC `nexus-data`, Secret `nexus-admin`, and the
  in-cluster Service `nexus.artifacts.svc:8081`. From Task 2, the three verified
  request bodies and whether the realm PUT is needed.
- Produces: an accepted EULA, the admin password rotated to the Vault value, the
  `raw-hosted` and `docker-proxy` repositories, the cleanup policy, and the
  Docker connector on 8082 that Task 5 and Task 6 both depend on.

**Why a Job and not a README.** Repositories, users and connectors are rows in
Nexus's own database, not Kubernetes objects. Configured by hand, the entire
substance of phase 21 would live inside a PVC where git cannot see it, and a
rebuild would be a restore rather than a reconcile.

**Why it reads the password file.** Setting `nexus.security.randompassword=false`
would make the first-boot credential the publicly documented `admin/admin123`.
That is what most automation does, and its failure mode is bad: if the Job never
runs, that known credential stays live indefinitely on a tailnet-published
service. Reading the boot-generated file has no such window, and it supplies the
idempotency signal for free — Nexus deletes the file when the password changes,
so *file present* means first run and *file absent* means reconcile. It works
only because RWO is a per-**node** constraint and this is a single-node cluster;
spec §9 records that this is the one element that would break on a second node.

- [ ] **Step 1: Write `platform/nexus/config/bootstrap-job.yaml`**

Substitute the tag from Task 2 for `NEXUSTAG`. If Task 2 found the realm PUT
unnecessary, delete the `realms/active` block and its comment.

**Check the wait loop against Task 2's measurement.** The script below waits
`60 * 5s = 300s` for the REST API. An Argo CD `Sync` hook can start before the
Deployment has finished rolling, so this loop absorbs the whole cold start. If
Task 2 measured startup above roughly 240 seconds, raise the `seq 1 60` bound
before committing.

```yaml
# Phase 21's configuration, in git rather than in a web UI.
#
# An Argo CD Sync HOOK, not a plain Job. A Job is immutable, so re-applying an
# unchanged one does nothing at all -- a changed repository definition would
# never reach the cluster, and the idempotency check in the spec's verification
# list would be untestable. BeforeHookCreation deletes the previous Job so each
# sync genuinely re-runs this.
#
# Re-running is safe because of the branch in Step 2 below: after the first
# run, /nexus-data/admin.password no longer exists, so the script authenticates
# with the Vault password and RECONCILES rather than creating.
apiVersion: batch/v1
kind: Job
metadata:
  name: nexus-bootstrap
  namespace: artifacts
  annotations:
    argocd.argoproj.io/hook: Sync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
spec:
  # Two attempts, not the default six. Every failure mode here is either
  # permanent (a schema mismatch) or slow (Nexus not up yet, which the wait
  # loop already covers), and six retries only delays the diagnosis.
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: nexus
      securityContext:
        # Must match the Deployment's: this Job reads a file the Nexus process
        # created as uid 200 on the same volume.
        runAsNonRoot: true
        runAsUser: 200
        runAsGroup: 200
        fsGroup: 200
      containers:
        - name: bootstrap
          # The Nexus image itself, purely because it already carries curl and
          # the exact version this configuration was validated against. Pinned
          # to the same tag as the Deployment on purpose -- the REST schema is
          # version-specific.
          image: sonatype/nexus3:NEXUSTAG
          command: ["/bin/bash", "/script/bootstrap.sh"]
          env:
            - name: NEXUS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: nexus-admin
                  key: password
          volumeMounts:
            - name: script
              mountPath: /script
            - name: data
              # The SAME ReadWriteOnce claim the Deployment holds. Legal here
              # because RWO restricts concurrent use to one NODE, not one pod,
              # and this cluster has one node. Read-only: this Job must never
              # write to Nexus's state directory.
              mountPath: /nexus-data
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 128Mi
      volumes:
        - name: script
          configMap:
            name: nexus-bootstrap-script
        - name: data
          persistentVolumeClaim:
            claimName: nexus-data
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: nexus-bootstrap-script
  namespace: artifacts
  annotations:
    argocd.argoproj.io/hook: Sync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
data:
  bootstrap.sh: |
    #!/bin/bash
    # Configures Nexus over its REST API. Idempotent: safe to re-run on every
    # Argo CD sync, which is exactly what the Sync hook causes.
    set -euo pipefail

    NEXUS=http://nexus.artifacts.svc:8081
    BOOTPW=/nexus-data/admin.password

    # The password reaches curl through --config, never as an argument.
    # A password on a command line is visible in `ps` -- the rule
    # configure-vault.sh and the Vault unsealer both already follow.
    AUTHFILE=$(mktemp)
    trap 'rm -f "$AUTHFILE"' EXIT

    echo "==> waiting for the REST API"
    # Ready is not the same as writable. The Deployment's startupProbe already
    # waits on /status, but this Job can start the moment the pod reports
    # Ready, and Nexus accepts writes slightly later.
    for _ in $(seq 1 60); do
      if curl -sf "$NEXUS/service/rest/v1/status/writable" >/dev/null 2>&1; then
        break
      fi
      sleep 5
    done
    curl -sf "$NEXUS/service/rest/v1/status/writable" >/dev/null

    echo "==> selecting credentials"
    if [ -f "$BOOTPW" ]; then
      echo "    first run: boot password present"
      printf 'user = "admin:%s"\n' "$(cat "$BOOTPW")" > "$AUTHFILE"
      ROTATE=yes
    else
      echo "    already provisioned: using the Vault password"
      printf 'user = "admin:%s"\n' "$NEXUS_PASSWORD" > "$AUTHFILE"
      ROTATE=no
    fi

    echo "==> accepting the Community Edition EULA"
    # The disclaimer text must be echoed back EXACTLY as the GET returned it,
    # so it is read from the instance rather than hardcoded here. That also
    # makes this survive a version bump that reworded it. Without an accepted
    # EULA, Community Edition refuses to fetch or upload any component.
    curl -sf -K "$AUTHFILE" "$NEXUS/service/rest/v1/system/eula" \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); d["accepted"]=True; print(json.dumps(d))' \
      > /tmp/eula.json
    curl -sf -K "$AUTHFILE" -X POST -H 'Content-Type: application/json' \
      -d @/tmp/eula.json "$NEXUS/service/rest/v1/system/eula"
    echo "    accepted"

    if [ "$ROTATE" = yes ]; then
      echo "==> rotating the admin password to the Vault value"
      # text/plain, not JSON: this endpoint takes the bare password as the body.
      # 204 is success. After this, Nexus deletes /nexus-data/admin.password,
      # which is what makes the branch above a reliable first-run signal.
      curl -sf -K "$AUTHFILE" -X PUT -H 'Content-Type: text/plain' \
        -d "$NEXUS_PASSWORD" \
        "$NEXUS/service/rest/v1/security/users/admin/change-password"
      printf 'user = "admin:%s"\n' "$NEXUS_PASSWORD" > "$AUTHFILE"
      echo "    rotated"
    fi

    # Helper: POST a body, treating 409 (already exists) as success. That is
    # what makes every call below idempotent -- Nexus has no upsert.
    create_if_absent() {
      code=$(curl -s -o /tmp/out -w '%{http_code}' -K "$AUTHFILE" \
        -X POST -H 'Content-Type: application/json' -d "$2" "$NEXUS$1")
      case "$code" in
        201|204) echo "    created ($code)" ;;
        400)     echo "    SCHEMA REJECTED ($code): $(cat /tmp/out)"; return 1 ;;
        409)     echo "    already present ($code)" ;;
        *)       echo "    UNEXPECTED ($code): $(cat /tmp/out)"; return 1 ;;
      esac
    }

    echo "==> cleanup policy docker-proxy-cleanup"
    # Created BEFORE the repository that names it: a reference to a policy that
    # does not exist is rejected.
    #
    # This is not housekeeping. Community Edition stops accepting new
    # components at 40,000 and says nothing -- it PAUSES rather than erroring,
    # so a proxy that has silently stopped caching looks exactly like one that
    # is merely slow. A Docker proxy accrues one component per cached tag.
    # Nexus creates and maintains the task that enforces this policy itself.
    create_if_absent /service/rest/v1/cleanup-policies \
      '{"name":"docker-proxy-cleanup","format":"docker","notes":"Phase 21: keeps the CE 40000-component cap out of reach.","criteriaLastDownloaded":30}'

    echo "==> repository raw-hosted"
    create_if_absent /service/rest/v1/repositories/raw/hosted \
      '{"name":"raw-hosted","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true,"writePolicy":"ALLOW"},"cleanup":{"policyNames":[]},"component":{"proprietaryComponents":false},"raw":{"contentDisposition":"ATTACHMENT"}}'

    echo "==> repository docker-proxy"
    # docker.httpPort 8082 IS the connector -- there is no separate object to
    # create. forceBasicAuth false routes Docker clients through the Bearer
    # Token realm instead of HTTP basic auth, which is what lets containerd
    # pull anonymously.
    create_if_absent /service/rest/v1/repositories/docker/proxy \
      '{"name":"docker-proxy","online":true,"storage":{"blobStoreName":"default","strictContentTypeValidation":true},"cleanup":{"policyNames":["docker-proxy-cleanup"]},"proxy":{"remoteUrl":"https://registry-1.docker.io","contentMaxAge":1440,"metadataMaxAge":1440},"negativeCache":{"enabled":true,"timeToLive":1440},"httpClient":{"blocked":false,"autoBlock":true},"docker":{"v1Enabled":false,"forceBasicAuth":false,"httpPort":8082},"dockerProxy":{"indexType":"HUB","cacheForeignLayers":false}}'

    echo "==> docker bearer token realm"
    # Delete this block if Task 2 found the realm already active. A 204 is
    # success; this endpoint replaces the whole active list, so the two default
    # realms must be repeated or every login breaks.
    curl -sf -K "$AUTHFILE" -X PUT -H 'Content-Type: application/json' \
      -d '["NexusAuthenticatingRealm","NexusAuthorizingRealm","DockerToken"]' \
      "$NEXUS/service/rest/v1/security/realms/active"

    echo "==> verifying anonymous docker read"
    # The question Task 6's mirror depends on. A 401 here means containerd will
    # be refused, and the fix is the realm above, not a repository permission.
    code=$(curl -s -o /dev/null -w '%{http_code}' "$NEXUS/repository/docker-proxy/v2/")
    if [ "$code" != "200" ]; then
      echo "    ANONYMOUS READ REFUSED ($code) -- the mirror will not work"
      exit 1
    fi
    echo "    anonymous read OK"

    echo "==> done"
```

- [ ] **Step 2: Validate and commit**

```bash
yamllint platform/nexus/config/bootstrap-job.yaml
kubeconform -strict -ignore-missing-schemas platform/nexus/config/bootstrap-job.yaml
grep -n 'NEXUSTAG' platform/nexus/config/bootstrap-job.yaml
```

Expected: clean, and the grep finds nothing.

```bash
git add platform/nexus/config/bootstrap-job.yaml
git commit -m "Configure Nexus from git with an idempotent Sync-hook Job

Repositories and connectors are rows in Nexus's own database, so a plain
deployment would leave all of phase 21's substance inside a PVC. The Job
accepts the CE EULA, rotates the admin password from the boot-generated
one to the Vault value, and reconciles both repositories and the cleanup
policy. It reads /nexus-data/admin.password rather than setting
randompassword=false, which would leave a documented credential live
indefinitely if the Job ever failed to run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

Print the push command on one line and wait:

```bash
git push origin main
```

- [ ] **Step 3: Watch the Job and read its log**

```bash
sg k3s-admin -c 'kubectl get jobs -n artifacts -w'
sg k3s-admin -c 'kubectl logs -n artifacts job/nexus-bootstrap'
```

Expected: `1/1` complete, and a log ending `==> done` with
`first run: boot password present`, `accepted`, `rotated`, three `created (201)`
lines, and `anonymous read OK`. **No password appears in the log.** If one does,
stop and report it.

A `SCHEMA REJECTED (400)` means this version's payload differs from what Task 2
recorded — compare against `platform/nexus/rest-schemas.md` and the instance's
Swagger, correct the body, and commit the correction.

- [ ] **Step 4: Prove the rotation actually happened**

```bash
sg k3s-admin -c 'kubectl exec -n artifacts deploy/nexus -- ls -la /nexus-data/admin.password'
```

Expected: **`No such file or directory`.** That is the proof the password
changed; a Job that exited 0 proves only that it ran. This is spec §17.3.

- [ ] **Step 5: Prove idempotency**

```bash
sg k3s-admin -c 'kubectl delete job -n artifacts nexus-bootstrap'
sg k3s-admin -c 'kubectl -n argocd patch app nexus --type merge -p "{\"operation\":{\"sync\":{}}}"'
sg k3s-admin -c 'kubectl logs -n artifacts job/nexus-bootstrap'
```

Expected on the second run: `already provisioned: using the Vault password`,
and `already present (409)` for all three creates. **No duplicate repository and
no error.** Confirm with:

```bash
sg k3s-admin -c 'kubectl exec -n artifacts deploy/nexus -- curl -s http://localhost:8081/service/rest/v1/repositories' | python3 -c 'import json,sys; [print(r["name"], r["format"], r["type"]) for r in json.load(sys.stdin)]'
```

Expected: exactly `raw-hosted raw hosted` and `docker-proxy docker proxy`,
once each. This is spec §17.8.

- [ ] **Step 6: Round-trip an artifact through `raw-hosted`**

The Deployment does not mount `nexus-admin`, so this runs from a throwaway pod
that does. That also keeps the password out of every shell argument. Write the
manifest to the scratchpad rather than nesting it inside `sg ... -c`:

```bash
cat > /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/roundtrip.yaml <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: nexus-roundtrip
  namespace: artifacts
spec:
  restartPolicy: Never
  containers:
    - name: probe
      image: curlimages/curl:8.11.1
      command: ["/bin/sh", "-c"]
      args:
        - |
          set -eu
          printf 'user = "admin:%s"\n' "$NEXUS_PASSWORD" > /tmp/auth
          head -c 1048576 /dev/urandom > /tmp/probe.bin
          BEFORE=$(md5sum /tmp/probe.bin | cut -d' ' -f1)
          echo "before $BEFORE"
          echo "upload $(curl -s -K /tmp/auth --upload-file /tmp/probe.bin -o /dev/null -w '%{http_code}' http://nexus.artifacts.svc:8081/repository/raw-hosted/probe/1.0.0/probe.bin)"
          AFTER=$(curl -s http://nexus.artifacts.svc:8081/repository/raw-hosted/probe/1.0.0/probe.bin | md5sum | cut -d' ' -f1)
          echo "after $AFTER"
          test "$BEFORE" = "$AFTER" && echo "CHECKSUM MATCH" || { echo "CHECKSUM MISMATCH"; exit 1; }
      env:
        - name: NEXUS_PASSWORD
          valueFrom:
            secretKeyRef:
              name: nexus-admin
              key: password
YAML
sg k3s-admin -c 'kubectl apply -f /tmp/claude-1000/-srv-projects-homelab/dedcfc94-8615-48b1-b110-7486c00b3a41/scratchpad/roundtrip.yaml'
sg k3s-admin -c 'kubectl wait --for=condition=Ready=false pod/nexus-roundtrip -n artifacts --timeout=120s' || true
sg k3s-admin -c 'kubectl logs -n artifacts nexus-roundtrip'
sg k3s-admin -c 'kubectl delete pod -n artifacts nexus-roundtrip'
```

Expected: `upload 201`, the two checksums identical, and `CHECKSUM MATCH`. An
upload returning 201 proves nothing on its own; the round trip is the test.
**A separate pod matters** — doing this from inside the Nexus pod over
`localhost` would prove nothing about the Service. This is spec §17.5.

---

### Task 5: The tailnet hostnames

**Files:**
- Create: `infrastructure/ingress/config/nexus-ingress.yaml`

**Interfaces:**
- Consumes: from Task 3, Service `nexus` in `artifacts` with ports 8081 and 8082.
- Produces: `https://nexus.taildf6cd4.ts.net` (UI and raw) and
  `https://nexus-docker.taildf6cd4.ts.net` (Docker clients).

**Why both Ingresses live here and not in the Nexus Application.** Every Ingress
in this repository sits in `ingress-config` at wave 21 so that nothing ever
waits on ingress health to sync — the reasoning `argocd-ingress.yaml` spells
out. These are the first Ingresses here whose backend Service arrives *later*
(wave 23), which is harmless: `ingress-config` goes Healthy when the Tailscale
operator populates `status.loadBalancer.ingress`, not when the backend exists.
The hostnames simply 502 for the seconds between wave 21 and wave 23.

**Why a second hostname rather than Docker path-based routing.** Sonatype's own
documentation warns that a repository context path in a registry URL "can result
in authentication failures", because Docker clients issue requests against the
registry root. One hostname per endpoint is also what `argocd`, `vault` and
`beacon` already do here, and each gets a real Let's Encrypt certificate from
its own proxy.

- [ ] **Step 1: Write `infrastructure/ingress/config/nexus-ingress.yaml`**

```yaml
# Nexus on the tailnet: the UI and raw repository at
# https://nexus.taildf6cd4.ts.net, and the Docker registry at
# https://nexus-docker.taildf6cd4.ts.net
#
# TWO hostnames, one per endpoint, rather than Docker path-based routing.
# Sonatype warns that a repository context path in a registry URL can break
# authentication, because Docker clients issue requests against the registry
# root -- so the registry gets a host of its own where it answers at /v2/
# exactly as the client expects, with a real Let's Encrypt certificate and no
# wildcard.
#
# These are also NOT redundant with the NodePort in platform/nexus/config/
# nexus.yaml. That NodePort is plain HTTP, host-local, and exists for
# containerd, which runs on the host and cannot use cluster DNS. These carry
# TLS and exist for docker clients on this machine or any other tailnet
# device, which should not be configured to trust an insecure registry.
#
# Both sit at wave 21 with every other Ingress here, so that nothing waits on
# ingress health. Their backend Service arrives at wave 23 -- the first time
# that ordering occurs in this repository. It is harmless: this Application
# goes Healthy when the Tailscale operator populates
# status.loadBalancer.ingress, which does not depend on the backend existing.
# The hostnames 502 for the seconds in between.
#
# The proxy-class annotation is NOT decorative: it is what bounds each proxy
# pod's memory. Omitting it leaves the proxy unbounded (chart default is
# `resources: {}`), which is how this host lost CoreDNS once.
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: nexus
  namespace: artifacts
  annotations:
    tailscale.com/proxy-class: homelab
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: nexus
      port:
        number: 8081
  tls:
    # Short name only. Becomes nexus.taildf6cd4.ts.net via MagicDNS.
    - hosts:
        - nexus
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: nexus-docker
  namespace: artifacts
  annotations:
    tailscale.com/proxy-class: homelab
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: nexus
      # The docker-proxy repository's own connector, configured through
      # docker.httpPort by the bootstrap Job. Nothing answers here until that
      # Job has run.
      port:
        number: 8082
  tls:
    - hosts:
        - nexus-docker
```

- [ ] **Step 2: Validate, commit, push**

```bash
yamllint infrastructure/ingress/config/nexus-ingress.yaml
kubeconform -strict -ignore-missing-schemas infrastructure/ingress/config/nexus-ingress.yaml
git add infrastructure/ingress/config/nexus-ingress.yaml
git commit -m "Publish Nexus and its Docker registry on the tailnet

Two hostnames rather than Docker path-based routing, which Sonatype warns
can break authentication because Docker clients address the registry root.
Both at wave 21 with every other Ingress here; their backend arrives at
wave 23, which ingress-config's health does not depend on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

Print the push command on one line and wait:

```bash
git push origin main
```

- [ ] **Step 3: Verify both hostnames answer**

```bash
sg k3s-admin -c 'kubectl get ingress -n artifacts'
sg k3s-admin -c 'kubectl get pods -n tailscale'
curl -sL -o /dev/null -w 'nexus %{http_code}\n' https://nexus.taildf6cd4.ts.net/
curl -s -o /dev/null -w 'nexus-docker %{http_code}\n' https://nexus-docker.taildf6cd4.ts.net/v2/
```

Expected: both Ingresses show an address; two new `ts-nexus-*` proxy pods;
and both curls return `200`. **Record the status codes actually observed rather
than the ones expected** — phase 19 found Vault answers `307`, not `200`, and an
earlier draft there asserted the wrong code.

- [ ] **Step 4: Log in to the UI with the Vault password**

Read the password into the clipboard without printing it, then log in at
`https://nexus.taildf6cd4.ts.net` as `admin`:

```bash
sg k3s-admin -c 'kubectl get secret -n artifacts nexus-admin -o jsonpath="{.data.password}"' | base64 -d | xclip -selection clipboard && echo "copied to clipboard"
```

If `xclip` is absent, use `wl-copy`. Expected: the login succeeds, confirming
the rotation in Task 4 reached the running server and not merely the Secret.
This is spec §17.2. Do not paste the password into this conversation.

---

### Task 6: The k3s mirror, and the proof that a rebuild survives it

**Files:**
- Create: `platform/nexus/registries.yaml`

**Interfaces:**
- Consumes: from Task 3, NodePort 30082. From Task 4, a working `docker-proxy`
  answering anonymously.
- Produces: `/etc/rancher/k3s/registries.yaml` on the host — **not reconciled by
  Argo CD**, the same arrangement as `infrastructure/networking/policy.hujson`.

**This task restarts the cluster.** k3s reads `registries.yaml` only at startup,
so applying it means `systemctl restart k3s`, which restarts every pod on this
node. Do it deliberately, at a quiet moment, and never as a side effect of
something else.

- [ ] **Step 1: Write `platform/nexus/registries.yaml`**

```yaml
# The canonical copy of /etc/rancher/k3s/registries.yaml.
#
# NOT RECONCILED BY ARGO CD. k3s reads this file from the host at startup;
# nothing in the cluster can apply it. Same arrangement as
# infrastructure/networking/policy.hujson. Copying it into place requires
# `sudo systemctl restart k3s`, which restarts every pod on this node.
#
# WHY 127.0.0.1 AND A NODEPORT, and not a Service name or the tailnet
# hostname: containerd runs on the HOST. It does not use cluster DNS, so it
# cannot resolve nexus.artifacts.svc -- and CoreDNS may not even be running at
# the moment containerd needs to pull. Routing through the tailnet hostname
# would make every layer pull depend on the Tailscale operator, which is itself
# an image containerd has to pull.
#
# WHY `docker.io` EXPLICITLY AND NOT "*": with containerd 2.x, the "*" wildcard
# breaks fallback to the upstream registry (k3s#11857). This host runs
# containerd v2.3.4-k3s1.36, so that bug applies here. For the same reason
# there are NO `rewrite:` rules -- those break fallback too (k3s#7007).
#
# WHY THIS IS SAFE ON A COLD REBUILD: containerd always tries the default
# endpoint as a last resort, even when a mirror is configured. So on a cluster
# with no Nexus yet, pulls -- including Nexus's own image -- go straight to
# Docker Hub. Disabling that with --disable-default-registry-endpoint would
# make this file a bootstrap deadlock; this host must never set that flag.
#
# `http://` is REQUIRED. Without the scheme containerd assumes HTTPS and every
# pull fails against a plain-HTTP NodePort.
mirrors:
  docker.io:
    endpoint:
      - "http://127.0.0.1:30082"
```

- [ ] **Step 2: Prove the endpoint answers before touching k3s**

```bash
curl -s -o /dev/null -w 'nodeport %{http_code}\n' http://127.0.0.1:30082/v2/
```

Expected: `200`. **If this is not 200, stop.** Installing the mirror now would
slow every pull on the cluster for no benefit, and the cause is in Task 4's
realm or anonymous-read configuration, not here.

- [ ] **Step 3: Record what a cold pull costs today**

```bash
sg k3s-admin -c 'sudo k3s crictl rmi docker.io/library/hello-world:latest' 2>/dev/null || true
time sg k3s-admin -c 'sudo k3s crictl pull docker.io/library/hello-world:latest'
```

Record the time. It is the "before" half of the cache measurement in Step 6.

- [ ] **Step 4: Install the file and restart k3s**

Tell the user this restarts every pod on the node, and get their go-ahead
first. Then, on one line each:

```bash
sudo cp platform/nexus/registries.yaml /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
```

- [ ] **Step 5: Wait for the cluster to come back, and confirm nothing broke**

```bash
sg k3s-admin -c 'kubectl get nodes'
sg k3s-admin -c 'kubectl get pods -A'
sg k3s-admin -c 'kubectl get applications -n argocd'
curl -sL -o /dev/null -w 'argocd %{http_code}\n' https://argocd.taildf6cd4.ts.net/
curl -sL -o /dev/null -w 'vault %{http_code}\n' https://vault.taildf6cd4.ts.net/
curl -sL -o /dev/null -w 'nexus %{http_code}\n' https://nexus.taildf6cd4.ts.net/
```

Expected: node `Ready`; every pod Running again; **all 13 Applications
Synced/Healthy**; and all three tailnet URLs answering. Vault needs the `-L`
above because it answers `307` to `/ui/`. A phase that quietly breaks Argo CD's
or Vault's ingress has not passed — this is spec §17.12.

- [ ] **Step 6: Prove the mirror is actually serving pulls**

```bash
sg k3s-admin -c 'sudo k3s crictl rmi docker.io/library/hello-world:latest' 2>/dev/null || true
time sg k3s-admin -c 'sudo k3s crictl pull docker.io/library/hello-world:latest'
curl -s 'http://127.0.0.1:30082/v2/_catalog'
sg k3s-admin -c 'kubectl exec -n artifacts deploy/nexus -- curl -s "http://localhost:8081/service/rest/v1/search?repository=docker-proxy&name=library/hello-world"' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["items"]), "components cached")'
```

Expected: the pull succeeds, and the search reports at least one cached
component. **The component appearing in `docker-proxy` is the proof** — a
successful pull alone would also be explained by containerd silently falling
back to Docker Hub, which is precisely the failure this check exists to
distinguish. This is spec §17.6.

- [ ] **Step 7: Prove a cold rebuild still works — the most important check here**

```bash
sg k3s-admin -c 'kubectl scale deploy/nexus -n artifacts --replicas=0'
sg k3s-admin -c 'kubectl wait --for=delete pod -l app=nexus -n artifacts --timeout=120s'
sg k3s-admin -c 'sudo k3s crictl rmi docker.io/library/busybox:latest' 2>/dev/null || true
time sg k3s-admin -c 'sudo k3s crictl pull docker.io/library/busybox:latest'
```

Expected: **the pull succeeds** with Nexus down, using an image that was never
cached, after a delay while the mirror attempt fails. That delay is the
documented cost in spec §8: a dead Nexus makes pulls slower, not broken.

This is the claim the whole design rests on — that containerd always falls back
to the default endpoint, so a cold rebuild can pull Nexus's own image. It is
tested here rather than trusted. If it fails, remove
`/etc/rancher/k3s/registries.yaml`, restart k3s, and report: the mirror cannot
be used on this cluster.

```bash
sg k3s-admin -c 'kubectl scale deploy/nexus -n artifacts --replicas=1'
```

- [ ] **Step 8: Measure memory under load**

```bash
sg k3s-admin -c 'kubectl wait --for=condition=ready pod -l app=nexus -n artifacts --timeout=600s'
for i in alpine:3.20 nginx:1.27 redis:7-alpine debian:12-slim; do sg k3s-admin -c "sudo k3s crictl pull docker.io/library/$i"; done
sg k3s-admin -c 'kubectl top pod -n artifacts'
free -h
sg k3s-admin -c 'kubectl get pod -n artifacts -l app=nexus -o jsonpath="{.items[0].status.containerStatuses[0].restartCount}"; echo'
```

Expected: restart count `0`. Record `kubectl top` and `free -h` against the
Task 1 baseline. **If the pod was OOM-killed, raise `limits.memory` in
`nexus.yaml`, not the heap**, commit that, and re-measure. This is spec §17.9,
and the spec is explicit that the sizing is not proven until this is done.

- [ ] **Step 9: Prove state survives a restart**

```bash
sg k3s-admin -c 'kubectl delete pod -n artifacts -l app=nexus'
sg k3s-admin -c 'kubectl wait --for=condition=ready pod -l app=nexus -n artifacts --timeout=600s'
sg k3s-admin -c 'kubectl exec -n artifacts deploy/nexus -- curl -s http://localhost:8081/service/rest/v1/repositories' | python3 -c 'import json,sys; [print(r["name"]) for r in json.load(sys.stdin)]'
sg k3s-admin -c 'kubectl exec -n artifacts deploy/nexus -- curl -s http://localhost:8081/service/rest/v1/cleanup-policies' | python3 -c 'import json,sys; [print(p["name"]) for p in json.load(sys.stdin)]'
```

Expected: both repositories and the cleanup policy still present, and the UI
login from Task 5 still works with the same password. This is spec §17.10 — the
PVC doing its job.

- [ ] **Step 10: Commit**

```bash
git add platform/nexus/registries.yaml
git commit -m "Mirror docker.io through Nexus, with fallback proven

containerd reaches the proxy on 127.0.0.1:30082 because it runs on the
host and cannot use cluster DNS. docker.io is named explicitly and there
are no rewrite rules: both the wildcard (k3s#11857) and rewrites (k3s#7007)
break fallback on containerd 2.x, which this host runs. Verified that an
uncached image still pulls with Nexus scaled to zero, which is what makes
a cold rebuild possible.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

---

### Task 7: Documentation

**Files:**
- Create: `platform/nexus/README.md`
- Modify: `README.md`
- Modify: `docs/workstation-plan.md`

**Interfaces:**
- Consumes: every measured value recorded in Tasks 2, 3 and 6.

**Why this is a task and not a footnote.** The rebuild list in the root README
is what makes this cluster reconstructible, and three things in this phase live
outside Argo CD's reach: the Vault ceremony, `registries.yaml`, and the
knowledge that H2 cannot be backed up by copying it.

- [ ] **Step 1: Write `platform/nexus/README.md`**

It must contain, each as its own section:

1. **What this is and why it is not Artifactory** — one paragraph, linking the
   spec. State the edition split as the reason: Artifactory OSS has no Docker,
   JCR has no Maven/npm/PyPI.
2. **Community Edition's limits** — 40,000 components, 100,000 requests/day, and
   the crucial behaviour: at the cap Nexus **pauses accepting new components
   without erroring**. Point at the Usage Center and at the
   `docker-proxy-cleanup` policy.
3. **Rotating the admin password** — and the ordering trap, spelled out:
   Nexus keeps its own password hash in H2 and will not notice a changed Vault
   value. Rotate by updating Vault **and then re-running the bootstrap Job while
   the old password is still known** — the Job's "already provisioned" branch
   authenticates with the value currently in the Secret, so discarding the old
   value first strands the account.
4. **`registries.yaml`** — that it is not reconciled, the copy-and-restart
   procedure, that the restart cycles every pod, and the escape hatch: delete
   the file and restart k3s if the mirror ever misbehaves.
5. **Backups, and the trap** — verbatim: *rsyncing a live H2 database is not a
   backup; it restores as corruption.* The procedure is to run Nexus's "Export
   databases for backup" task first, then copy **both** `/nexus-data/backup/`
   and `/nexus-data/blobs/` to `/backups/`. The export without the blobs
   restores an index pointing at nothing. State plainly that phase 21 does not
   automate this and has not performed a restore drill — that is phase 34.
6. **Known gaps** — no language proxies (no consumer yet, spec §2.1); no hosted
   Docker registry (GHCR holds this homelab's images); no `NetworkPolicy`
   (cluster-wide gap); the bootstrap Job's PVC sharing works only because this
   is a single node.

- [ ] **Step 2: Update the root README**

In the layout table, replace nothing and add one row after the
`platform/registry/` row:

```markdown
| `platform/nexus/` | Nexus Repository CE: manifests, the bootstrap Job that configures it over REST, the probed REST schemas, and the k3s `registries.yaml` that is **not** reconciled |
```

Then add `artifacts` to any namespace list, and add to the rebuild list — the
section naming things that exist in no repository or are not reconciled — these
two entries:

```markdown
- `/etc/rancher/k3s/registries.yaml` — copy from `platform/nexus/registries.yaml`
  and `sudo systemctl restart k3s`. Not reconciled by Argo CD.
- Nexus's admin password is seeded by `platform/vault/configure-vault.sh` and
  applied to the running server by the `nexus-bootstrap` Job, not by hand.
```

- [ ] **Step 3: Update the workstation plan**

In `docs/workstation-plan.md`, leave the §24 heading "Phase 21 — Artifactory"
alone — it names the role. Replace the body's architecture block and add a note
directly under the heading:

```markdown
**Réalisé avec Sonatype Nexus Repository Community Edition, pas avec
Artifactory.** Aucune édition gratuite de JFrog ne couvre la liste ci-dessus :
Artifactory OSS n'a pas Docker, et JCR n'a ni Maven, ni npm, ni PyPI. Nexus CE
couvre les deux rôles dans un seul déploiement. Voir
`docs/superpowers/specs/2026-09-17-nexus-repository-design.md`.

Périmètre livré : `raw-hosted` pour les artefacts de build versionnés, et
`docker-proxy` comme cache pull-through de Docker Hub que k3s utilise en
miroir. Les proxys Maven/npm/PyPI sont reportés — aucun consommateur
aujourd'hui. Les images de ce homelab restent sur GHCR (phase 20).
```

Also correct §37's architecture tree: replace the `Artifactory` line with
`Nexus Repository (raw + docker proxy)`.

- [ ] **Step 4: Verify the documentation matches reality**

```bash
grep -rn 'artifactory' --include=*.md --include=*.yaml . | grep -v docs/superpowers | grep -v workstation-plan
sg k3s-admin -c 'kubectl get applications -n argocd'
sg k3s-admin -c 'kubectl get ns artifacts'
ls platform/nexus/
```

Expected: the first grep returns **nothing** — no stale reference to a
`platform/artifactory` path outside the spec and the roadmap, where the word is
deliberate. All 13 Applications Synced/Healthy.

- [ ] **Step 5: Commit**

```bash
git add platform/nexus/README.md README.md docs/workstation-plan.md
git commit -m "Document Nexus, its CE limits, and the two things git cannot apply

Records the rotation ordering trap, that registries.yaml is not reconciled,
and that copying a live H2 database is not a backup -- the export task must
run first and the blobs must come with it. Corrects the roadmap to say which
product fills the Artifactory role and why.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

Print the push command on one line and wait:

```bash
git push origin main
```
