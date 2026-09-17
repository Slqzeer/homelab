# GitHub Actions and GHCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitHub Actions load-bearing for this platform: a CI gate on the
repository that auto-deploys to the cluster, the tailnet ACL under automation,
and the `push → test → build → GHCR → Argo CD → k3s` chain proved end to end by
a deliberately trivial Go canary called `beacon`.

**Architecture:** Four workstreams ordered by credential risk. Task 1 needs no
credential and guards every later commit in the phase. Task 2 adds the ACL
workflow in `test` mode only. Tasks 3–4 build `beacon` in its own repository and
publish it to GHCR, with CI writing the resulting `tag@digest` back into
beacon's own deploy manifests. Tasks 5–6 wire the cluster: a `read:packages`
PAT in Vault, delivered by VSO as a `dockerconfigjson` pull Secret. Task 7
corrects the documentation this phase invalidates. Task 8 flips the ACL job to
`apply`, last, with evidence.

**Tech Stack:** GitHub Actions (GitHub-hosted runners), GHCR, kubeconform 0.8.0,
yamllint 1.38.0, kustomize 5.8.1, Go 1.27.1, k3s v1.36.4+k3s1, Argo CD v3.5.2,
Vault 2.0.4, VSO 1.5.1, Tailscale operator.

**Spec:** `docs/superpowers/specs/2026-09-16-github-actions-ghcr-design.md`

## Global Constraints

- **Never run `git push`.** The user reserves all pushes. Commit locally, print
  the exact push command on one line, and wait.
- **Work on `main`.** Argo CD tracks `main`; nothing else reconciles into the
  cluster. Do not create feature branches for this work.
- **`kubectl` needs a group wrapper on this host:** `sg k3s-admin -c '...'`.
  Nested quoting inside it is fragile — for anything multi-line, write a script
  to the scratchpad and run that.
- **Commands the user must edit and run must be shell-safe as written.** Never
  use `<angle brackets>` as a placeholder; `<` is a shell redirection and has
  broken this user's shell twice. Never use line continuations in a command
  handed over for copy-paste. Keep such commands on one line.
- **No credential may become a command argument.** Use the `key=@<path>` form
  `configure-vault.sh` and the unsealer already use, so only a filename appears
  in `ps`.
- **The GHCR PAT is never printed, echoed, committed, or pasted into the
  conversation.** It is typed by the user at a hidden prompt and nowhere else.
- **Never grep `ps` output for a credential.** The pattern lands in the grep's
  own argv and `ps` matches the grep itself, reporting a leak that is not there.
  Read the full `ps` output instead.
- **Every third-party Action is pinned to a commit SHA**, never a tag. Resolved
  2026-09-16:
  - `actions/checkout` → `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09` (v5)
  - `actions/setup-go` → `924ae3a1cded613372ab5595356fb5720e22ba16` (v6)
  - `docker/login-action` → `c94ce9fb468520275223c153574b00df6fe4bcc9` (v3)
  - `docker/build-push-action` → `10e90e3645eae34f1e60eeb005ba3a3d33f178e8` (v6)
  - `docker/metadata-action` → `c299e40c65443455700f0fdfc63efafe5b349051` (v5)
  - `tailscale/gitops-acl-action` → `5a4a17f5708e9bf96f4ee915a95e9f83c2eebe1a` (v1)
- **Tool versions are pinned too.** kubeconform `v0.8.0`, sha256
  `9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883` for
  `kubeconform-linux-amd64.tar.gz` (verified against the binary actually run on
  2026-09-16). yamllint `1.38.0`. kustomize `v5.8.1`. Go `1.27.1`.
- **Never put a comment on a list item in a file CI rewrites.** Verified on
  kustomize v5.8.1: `kustomize edit set image` preserves comments but
  **relocates** them — a comment attached to an `images:` entry migrated up
  under `resources:`, where it then described the wrong line. Header comments at
  the top of the file survive in place. In `beacon/deploy/kustomization.yaml`,
  put explanation in the header block only.
- **The ACL job ships `action: test` and stays there until Task 8.** Do not
  write `apply` into the workflow before then, even as a commented-out line that
  someone could uncomment.
- **Sync waves after this plan:** `argocd` -1, `namespaces` 0,
  `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21,
  `vso-config` 22, `postgres` 23, `redis` 23, **`registry` 23**, **`beacon` 24**.
  `beacon` is the one component in this repository deliberately placed at a
  later wave than its neighbours, because it has a real dependency on the
  Secret `registry` creates — see Task 6.
- **A directory used as an Argo CD `path` may contain only valid manifests.**
- **Argo CD reporting Synced/Healthy proves only what it applied**, never that
  a credential works or that a pull was authenticated. Verify those separately.

## File Structure

### In `homelab` (this repository)

| Path | Responsibility |
| --- | --- |
| `.yamllint.yaml` | Create. Lint profile matching the house comment-first style |
| `.github/workflows/validate.yaml` | Create. yamllint + kubeconform gate |
| `.github/workflows/tailscale-acl.yaml` | Create. ACL `test` (Task 2), `apply` (Task 8) |
| `bootstrap/namespaces/namespaces.yaml` | Modify. Add namespace `apps` |
| `platform/registry/config/vault-secrets.yaml` | Create. VaultConnection, SA, VaultAuth, VaultStaticSecret for the pull Secret |
| `platform/registry/README.md` | Create. PAT issuance, seeding, rotation, expiry |
| `platform/vault/configure-vault.sh` | Modify. Add `vso-ghcr-read` policy and `vso-ghcr` role — **no secret value** |
| `environments/homelab/apps/registry.yaml` | Create. Application, wave 23, path `platform/registry/config` |
| `environments/homelab/apps/beacon.yaml` | Create. Application, wave 23, pointing at the beacon repo |
| `README.md` | Modify. Rebuild list, known gaps, layout table |
| `infrastructure/networking/README.md` | Modify. Authority flips to git |

### In `beacon` (new repository, `/srv/projects/beacon`)

| Path | Responsibility |
| --- | --- |
| `go.mod` | Module definition |
| `main.go` | Two handlers and the injected `version` variable |
| `main_test.go` | Proves `/healthz` and that `/version` reports the injected stamp |
| `Dockerfile` | Multi-stage build to `scratch` |
| `.github/workflows/ci.yaml` | test → build → push → write the pin back |
| `deploy/deployment.yaml` | The Deployment, with `imagePullSecrets` |
| `deploy/service.yaml` | ClusterIP on 8080 |
| `deploy/ingress.yaml` | Tailscale Ingress, `proxy-class: homelab` |
| `deploy/kustomization.yaml` | The deploy pin. **Rewritten by CI** |
| `README.md` | States plainly that this is a canary |

**Why `registry` is its own Application rather than living in `beacon.yaml`:**
the pull Secret must exist in namespace `apps` before the Deployment that
references it is admitted, and it is homelab-owned infrastructure, not part of
the application. Splitting them also means a broken canary cannot take the
credential path down with it.

---

### Task 1: The manifest CI gate

Nothing currently sits between `git push` and Argo CD applying to the cluster.
This task closes that, and it goes first because every later commit in this
phase then passes through it.

**Files:**
- Create: `.yamllint.yaml`
- Create: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: a `validate` workflow with two jobs, `yamllint` and `kubeconform`,
  running on `pull_request` and on `push` to `main`. Later tasks add files that
  must pass it.

- [ ] **Step 1: Record the "before" measurement**

yamllint is not installable on this host (the venv bootstrap fails), so it runs
through Docker. Record what stock yamllint says about the current tree:

```bash
cd /srv/projects/homelab
docker run --rm -v "$PWD":/w:ro -w /w python:3.13-alpine sh -c "pip install -q yamllint==1.38.0 && yamllint -f parsable bootstrap environments infrastructure platform" 2>&1 | grep -v WARNING | wc -l
```

Expected: `23`. Every one of the 24 files fails `document-start`, because every
file here opens with an explanatory comment block. This is the measurement that
justifies `.yamllint.yaml` existing at all — if it prints `0`, stop and work out
what changed before writing a config that relaxes nothing.

- [ ] **Step 2: Write the lint profile**

Create `.yamllint.yaml`:

```yaml
# yamllint profile for this repository.
#
# Stock yamllint fails every file here. Measured 2026-09-16, before this file
# existed: 23 violations across 24 files, essentially all `document-start`,
# because every manifest in this repository opens with an explanatory comment
# block instead of `---`. Those comment blocks carry a large share of this
# repository's reasoning -- several phases invested in them deliberately. The
# linter adapts to the house style; the house style does not adapt to the
# linter.
#
# The workflow runs yamllint with --strict, so warnings fail too. That is the
# point: a gate that tolerates warnings accumulates them until nobody reads the
# output. Every relaxation below is therefore explicit and argued.
extends: default

rules:
  # See above. Comment-first headers are the convention, not a defect.
  document-start: disable

  # The longest line in the repository is 93 characters
  # (platform/vault/config/unsealer.yaml). 100 leaves headroom without
  # licensing unbounded lines.
  line-length:
    max: 100

  # `on:` in a GitHub Actions workflow is a mapping key, not the boolean
  # `on`. Without this, every workflow file this phase adds fails the gate
  # that this phase also adds.
  truthy:
    check-keys: false

  # One space before an inline comment, not two. Relaxed for a single
  # existing occurrence in platform/databases/postgres/config/postgres.yaml
  # rather than reformatting a file this phase has no other reason to touch.
  comments:
    min-spaces-from-content: 1
```

- [ ] **Step 3: Verify the profile is green on the current tree, under `--strict`**

```bash
cd /srv/projects/homelab
docker run --rm -v "$PWD":/w:ro -w /w python:3.13-alpine sh -c "pip install -q yamllint==1.38.0 && yamllint --strict -c .yamllint.yaml -f parsable bootstrap environments infrastructure platform; echo EXIT=\$?" 2>&1 | grep -v WARNING | tail -3
```

Expected: no violation lines and `EXIT=0`. A gate that is red on a clean tree
gets disabled within a week and takes the useful checks with it.

- [ ] **Step 4: Verify the kubeconform half locally, and verify it bites**

```bash
cd /srv/projects/homelab
curl -sL https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o /tmp/kc.tgz
echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  /tmp/kc.tgz" | sha256sum -c -
tar xzf /tmp/kc.tgz -C /tmp kubeconform
/tmp/kubeconform -strict -summary -kubernetes-version 1.36.0 -ignore-filename-pattern 'values\.yaml$' -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' bootstrap environments infrastructure platform
```

Expected exactly:

```
Summary: 35 resources found in 20 files - Valid: 35, Invalid: 0, Errors: 0, Skipped: 0
```

**`Skipped: 0` is the number that matters.** It means every CRD resolved against
a real schema. If it reports any skips, a CRD schema failed to fetch and those
resources were waved through silently — the usual way manifest linting becomes
decorative. Do not proceed with skips.

Now confirm it actually fails on a bad manifest:

```bash
mkdir -p /tmp/kcneg && sed 's/  refreshAfter: 60s/  refreshAfte: 60s/' platform/databases/redis/config/vault-secrets.yaml > /tmp/kcneg/b.yaml
/tmp/kubeconform -strict -summary -kubernetes-version 1.36.0 -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' /tmp/kcneg
```

Expected: `Invalid: 1`, naming `additional properties 'refreshAfte' not allowed`.
If it reports `Valid`, `-strict` is missing and the gate checks nothing.

- [ ] **Step 5: Write the workflow**

Create `.github/workflows/validate.yaml`:

```yaml
# Validates every manifest before Argo CD can apply it.
#
# This repository is reconciled into the cluster from `main` with
# selfHeal: true. Before this workflow existed there was nothing at all
# between `git push` and the cluster. This narrows that window; it does not
# close it -- see "What this does NOT catch" below.
name: validate

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  yamllint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5
      - name: Install yamllint
        run: pip install --quiet yamllint==1.38.0
      - name: Lint
        # --strict makes warnings fail. .yamllint.yaml is tuned so the tree
        # is clean under it; see the argument in that file's header.
        run: yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure platform .github

  kubeconform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5

      - name: Install kubeconform
        # Pinned by version AND checksum. An unpinned linter is a supply-chain
        # hole in the very job whose purpose is to be trusted.
        run: |
          curl -sL https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o kc.tgz
          echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  kc.tgz" | sha256sum -c -
          tar xzf kc.tgz kubeconform

      - name: Validate manifests
        # -strict rejects unknown fields. Without it a typo like `replicaz:`
        # is accepted and this job is decorative.
        #
        # -ignore-filename-pattern excludes Helm values files, which are not
        # manifests and have no apiVersion/kind. The rule is by FILENAME
        # rather than by directory because every values file here is named
        # values.yaml, and a filename rule needs no maintenance as components
        # are added -- a directory list would need editing every phase and
        # would fail open when someone forgot.
        #
        # The second -schema-location supplies CRD schemas. All five CRDs
        # this repository uses (Application, VaultAuth, VaultConnection,
        # VaultStaticSecret, ProxyClass) are in the datreeio catalog.
        run: |
          ./kubeconform -strict -summary \
            -kubernetes-version 1.36.0 \
            -ignore-filename-pattern 'values\.yaml$' \
            -schema-location default \
            -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
            bootstrap environments infrastructure platform

      - name: Fail if any resource was skipped
        # A skipped resource is an UNVALIDATED resource. kubeconform exits 0
        # when it skips, so without this check a CRD schema that failed to
        # fetch would turn this job green while checking nothing.
        run: |
          ./kubeconform -summary -kubernetes-version 1.36.0 \
            -ignore-filename-pattern 'values\.yaml$' \
            -schema-location default \
            -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
            bootstrap environments infrastructure platform \
            | tee summary.txt
          grep -q 'Skipped: 0' summary.txt
```

- [ ] **Step 6: Commit**

```bash
cd /srv/projects/homelab
git add .yamllint.yaml .github/workflows/validate.yaml
git commit -m "Add manifest CI: yamllint and kubeconform on every push

This repository is reconciled into the cluster from main with
selfHeal: true, and until now nothing at all sat between git push and
Argo CD applying. Every phase from 12 onward added manifests to a
repository with no gate on it.

kubeconform runs with -strict (without it, unknown fields are accepted
and the check is decorative) and the run is failed explicitly if any
resource is SKIPPED -- kubeconform exits 0 on a skip, so a CRD schema
that failed to fetch would otherwise turn the job green while checking
nothing. Measured on the current tree: 35 resources, 20 files, 0
skipped.

.yamllint.yaml exists because stock yamllint fails all 24 files, almost
entirely on document-start: every manifest here opens with an
explanatory comment block rather than ---. Those comments carry much of
this repository's reasoning, so the linter adapts rather than the tree.
It runs with --strict so warnings cannot accumulate.

Helm values files are excluded from kubeconform by filename pattern:
they are not manifests. That gap is real and is recorded in the README.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 7: Hand the push to the user**

Print exactly this, on one line, and wait:

```
git push origin main
```

Then ask the user to confirm the `validate` workflow ran and both jobs passed,
at <https://github.com/Slqzeer/homelab/actions>. **Do not proceed to Task 2
until both jobs are green on `main`.** A gate nobody has seen pass is not a
gate. If `yamllint` fails on `.github/workflows/validate.yaml` itself, the
`truthy: check-keys: false` rule is missing from `.yamllint.yaml`.

---

### Task 2: The tailnet ACL workflow, in `test` mode only

Phase 13 deferred this here in writing, twice. It ships in `test` mode and
**stays there until Task 8** — read §7 of the spec before starting, because the
reason for that split is not obvious and the failure mode is losing every
tailnet URL including Argo CD's.

**Files:**
- Create: `.github/workflows/tailscale-acl.yaml`

**Interfaces:**
- Consumes: the `validate` gate from Task 1 (this file must pass it).
- Produces: a `tailscale-acl` workflow with one job running `action: test`.
  Task 8 changes that single input to `apply` and adds a second job.

- [ ] **Step 1: Hand the OAuth client prerequisite to the user**

This cannot be done from the CLI. Ask the user to do the following, and wait:

1. Open <https://login.tailscale.com/admin/settings/oauth>
2. Generate an OAuth client with **write** access to the **Policy File** scope.
   This must be a **second, separate client** from the Kubernetes operator's —
   the operator's client has device scopes and no policy access, and widening
   it would give the in-cluster operator the ability to rewrite the tailnet
   policy. The spec's §5 keeps these two credential paths apart deliberately.
3. Add both halves as repository secrets at
   <https://github.com/Slqzeer/homelab/settings/secrets/actions>:
   - `TS_OAUTH_CLIENT_ID`
   - `TS_OAUTH_SECRET`

**The secret value must never be pasted into this conversation.**

- [ ] **Step 2: Write the workflow, `test` only**

Create `.github/workflows/tailscale-acl.yaml`:

```yaml
# Validates infrastructure/networking/policy.hujson against the live tailnet.
#
# THIS JOB DOES NOT APPLY ANYTHING YET. It runs `action: test`, which checks
# the policy parses and its ACL tests pass, and reports how the file differs
# from what the control plane currently holds. The switch to `apply` is a
# deliberate, separate step -- see the phase-20 spec, section 7.
#
# Why that split matters. Reading gitops-pusher at the commit this action
# pins, `apply` CANNOT preserve a change made in the admin console:
#
#   * its drift guard compares a cached etag against the control plane's, but
#     the cache file defaults to ./version-cache.json, which does not survive
#     a runner. PrevETag is therefore always empty on entry, and the code
#     fills it with the CURRENT control etag -- making the drift comparison
#     structurally unable to be true.
#   * even if it could be, --fail-on-manual-edits defaults to false (a
#     printed warning, not a failure), and this composite action exposes no
#     input to set it.
#
# So `apply` overwrites the live policy with this file, every time, with at
# most a warning that in practice never prints. After Task 8, git is
# authoritative for the tailnet policy and a console edit survives only until
# the next push that touches this file. infrastructure/networking/README.md
# says so.
#
# This file also holds the stanzas the cluster depends on: tagOwners for
# tag:k8s-operator and tag:k8s, and autoApprovers.services. Removing either
# breaks all tailnet ingress -- Argo CD's own URL included.
name: tailscale-acl

on:
  pull_request:
    paths:
      - infrastructure/networking/policy.hujson
      - .github/workflows/tailscale-acl.yaml
  push:
    branches: [main]
    paths:
      - infrastructure/networking/policy.hujson
      - .github/workflows/tailscale-acl.yaml

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5
      - uses: tailscale/gitops-acl-action@5a4a17f5708e9bf96f4ee915a95e9f83c2eebe1a  # v1
        with:
          # `-` means "the default tailnet of the authenticated client" in
          # the Tailscale API. It avoids hardcoding taildf6cd4.ts.net, which
          # changes if the tailnet is ever renamed. If the run fails
          # resolving the tailnet, substitute taildf6cd4.ts.net here.
          tailnet: "-"
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          policy-file: ./infrastructure/networking/policy.hujson
          action: test
```

- [ ] **Step 3: Verify it passes the Task 1 gate locally before pushing**

```bash
cd /srv/projects/homelab
docker run --rm -v "$PWD":/w:ro -w /w python:3.13-alpine sh -c "pip install -q yamllint==1.38.0 && yamllint --strict -c .yamllint.yaml .github; echo EXIT=\$?" 2>&1 | grep -v WARNING | tail -3
```

Expected: `EXIT=0`. If `on:` is reported as a truthy problem, `.yamllint.yaml`
from Task 1 is missing its `truthy: check-keys: false` rule.

- [ ] **Step 4: Commit**

```bash
cd /srv/projects/homelab
git add .github/workflows/tailscale-acl.yaml
git commit -m "Add tailnet ACL workflow in test mode (apply deliberately withheld)

Phase 13 deferred this to phase 20 in writing, in both
infrastructure/networking/README.md and the phase-13 plan.

It ships as action: test only. Reading gitops-pusher at the commit this
action pins, apply cannot preserve a console edit: the drift guard
compares a cached etag that does not survive a CI runner, so PrevETag is
always empty and gets assigned the current control etag, making the
comparison structurally unable to fire -- and --fail-on-manual-edits
defaults to false and is not exposed by the composite action anyway.

So the first apply overwrites the live policy. This file carries the
tagOwners and autoApprovers stanzas the Kubernetes operator depends on,
and losing them breaks all tailnet ingress including Argo CD's own URL.
Running test first turns that from a hope into a diff that can be read.

The switch to apply is a separate commit, after reconciliation.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 5: Hand the push to the user, and read the output carefully**

Print exactly this, on one line, and wait:

```
git push origin main
```

Then ask the user for the job log. **What matters is not that it passed — it is
the three etag lines**, which `gitops-pusher` prints on every run:

```
control: <sha256 of the live policy, hujson-formatted>
local:   <sha256 of infrastructure/networking/policy.hujson>
cache:   <same as control, always, for the reason in the file header>
```

Record whether `control` and `local` match, verbatim, in the task notes:

- **They match** — the file in git is already exactly what the tailnet is
  running. Task 8's reconciliation is then a no-op, and `apply` is safe.
- **They differ** — the file has drifted from the console, and Task 8 **must**
  begin by copying the live policy into the file. Applying without that step
  deletes whatever was changed in the console since phase 13.

Do not proceed to Task 8 later without this measurement. It is the only
evidence that distinguishes a safe apply from a destructive one.

---

### Task 3: The `beacon` application

A deliberately trivial Go service whose entire job is to report which commit is
live. **Keep it honest about being a canary** — nothing depends on it, and the
moment it grows a feature it stops being the thing this phase is testing.

**Files:**
- Create: `/srv/projects/beacon/mise.toml`
- Create: `/srv/projects/beacon/go.mod`
- Create: `/srv/projects/beacon/main.go`
- Create: `/srv/projects/beacon/main_test.go`
- Create: `/srv/projects/beacon/Dockerfile`
- Create: `/srv/projects/beacon/README.md`
- Create: `/srv/projects/beacon/.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: an image entrypoint `/beacon` listening on `:8080` (overridable via
  `PORT`), serving `GET /healthz` → `200 ok` and `GET /version` →
  `{"version":"<stamp>"}`. The stamp is set at build time via
  `-ldflags "-X main.version=<value>"` and defaults to `unknown`. Task 4's
  workflow depends on that exact ldflags path and on the `VERSION` build arg.

- [ ] **Step 1: Hand the repository creation to the user**

There is no `gh` CLI on this host, so this is a browser step. Ask the user to
create <https://github.com/new> named **`beacon`**, **private**, with **no**
README, .gitignore or licence (an initial commit would conflict with the local
history built below). Wait for confirmation.

- [ ] **Step 2: Create the working tree**

```bash
mkdir -p /srv/projects/beacon
cd /srv/projects/beacon
git init -b main
git remote add origin git@github.com:Slqzeer/beacon.git
```

- [ ] **Step 3: Install the Go toolchain through mise**

Go is not installed on this host; mise is, and it currently has nothing
installed. Pin the toolchain to the repository:

```bash
cd /srv/projects/beacon
mise use go@1.27.1
mise exec -- go version
```

Expected: `go version go1.27.1 linux/amd64`. This writes `mise.toml`, which is
committed — the same discipline the roadmap's phase 4 describes.

- [ ] **Step 4: Initialise the module**

```bash
cd /srv/projects/beacon
mise exec -- go mod init github.com/Slqzeer/beacon
```

- [ ] **Step 5: Write the failing test**

Create `/srv/projects/beacon/main_test.go`:

```go
package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthzReturnsOK(t *testing.T) {
	rec := httptest.NewRecorder()
	newMux().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /healthz: got status %d, want %d", rec.Code, http.StatusOK)
	}
}

// The canary's whole purpose is that /version reports the BUILD STAMP rather
// than a constant. A test that only checked for a 200 would pass against a
// handler that always returned "unknown", which is precisely the bug that
// would make the entire phase-20 chain unverifiable.
func TestVersionReportsTheInjectedStamp(t *testing.T) {
	original := version
	version = "sha-deadbee"
	defer func() { version = original }()

	rec := httptest.NewRecorder()
	newMux().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/version", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /version: got status %d, want %d", rec.Code, http.StatusOK)
	}
	if !strings.Contains(rec.Body.String(), "sha-deadbee") {
		t.Fatalf("GET /version: body %q does not report the injected stamp", rec.Body.String())
	}
}

func TestUnknownPathIs404(t *testing.T) {
	rec := httptest.NewRecorder()
	newMux().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	if rec.Code != http.StatusNotFound {
		t.Fatalf("GET /: got status %d, want %d", rec.Code, http.StatusNotFound)
	}
}
```

- [ ] **Step 6: Run the test to verify it fails**

```bash
cd /srv/projects/beacon && mise exec -- go test ./...
```

Expected: a **build** failure — `undefined: newMux` and `undefined: version`.
That is the correct first failure. If it reports anything else, the test file
is wrong, not the toolchain.

- [ ] **Step 7: Write the minimal implementation**

Create `/srv/projects/beacon/main.go`:

```go
// Command beacon is the phase-20 canary.
//
// It exists to prove the push -> test -> build -> GHCR -> Argo CD -> k3s
// chain end to end, and nothing in this homelab depends on it. It is not
// useful and is not meant to become useful: if it ever grows a feature, it
// stops being the thing that phase is testing. See README.md.
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
)

// version is injected at build time with -ldflags "-X main.version=<sha>".
// The default is deliberately "unknown" rather than a plausible-looking
// version string: an unstamped build should be obviously unstamped, because
// the one question this service answers is which commit is live.
var version = "unknown"

func newMux() *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		fmt.Fprintln(w, "ok")
	})

	mux.HandleFunc("GET /version", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, "{\"version\":%q}\n", version)
	})

	return mux
}

func main() {
	addr := ":8080"
	if p := os.Getenv("PORT"); p != "" {
		addr = ":" + p
	}

	log.Printf("beacon %s listening on %s", version, addr)
	if err := http.ListenAndServe(addr, newMux()); err != nil {
		log.Fatal(err)
	}
}
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd /srv/projects/beacon && mise exec -- go test ./... -v
```

Expected: `PASS` for all three tests.

- [ ] **Step 9: Write the Dockerfile**

Create `/srv/projects/beacon/Dockerfile`:

```dockerfile
# Multi-stage build to a single static binary on scratch.
#
# CGO_ENABLED=0 is NOT optional. With cgo enabled the binary links libc
# dynamically, and `scratch` contains no libc -- so the image builds fine,
# pushes fine, and then fails at runtime with "no such file or directory"
# naming the binary that is plainly right there. That error names the
# executable, not the missing loader, which is why it is worth a comment.
#
# USER is numeric because scratch has no /etc/passwd for a name to resolve
# against. 65534 is nobody. The Deployment sets runAsNonRoot: true, which
# requires a numeric UID here or the kubelet refuses to start the container.
ARG GO_VERSION=1.27.1

FROM golang:${GO_VERSION}-alpine AS build
WORKDIR /src

# No dependencies beyond the standard library, so there is no go.sum and
# nothing to download. If that ever changes, add `COPY go.sum ./` and a
# `RUN go mod download` layer above the source copy.
COPY go.mod ./
COPY *.go ./

ARG VERSION=unknown
RUN CGO_ENABLED=0 GOOS=linux go build \
      -trimpath \
      -ldflags "-s -w -X main.version=${VERSION}" \
      -o /out/beacon .

FROM scratch
COPY --from=build /out/beacon /beacon
EXPOSE 8080
USER 65534:65534
ENTRYPOINT ["/beacon"]
```

- [ ] **Step 10: Build the image and prove the stamp survives into the binary**

This is the step that verifies the `-ldflags` path Task 4's workflow depends on.

```bash
cd /srv/projects/beacon
docker build --build-arg VERSION=sha-testing -t beacon:testing .
docker run -d --rm --name beacon-probe -p 18080:8080 beacon:testing
sleep 2
curl -s localhost:18080/healthz
curl -s localhost:18080/version
docker stop beacon-probe
```

Expected:

```
ok
{"version":"sha-testing"}
```

If `/version` reports `unknown`, the build arg is not reaching the `-ldflags`
`-X` path and **Task 4 will publish images that cannot report their own
commit** — which silently defeats the phase's end-to-end verification. Fix it
here, not later.

- [ ] **Step 11: Write the README and .gitignore**

Create `/srv/projects/beacon/README.md`:

```markdown
# beacon

The canary for the homelab's phase-20 CI/CD chain. **This service is not
useful, and is not meant to become useful.**

It exists to prove that
`git push -> go test -> docker build -> GHCR -> Argo CD -> k3s` works end to
end, by answering one question: which commit is currently live?

    curl https://beacon.taildf6cd4.ts.net/version
    {"version":"sha-a1b2c3d"}

If it ever grows a second job, it stops being the thing that chain is being
tested with, and the homelab loses its only end-to-end check. Build the new
thing as its own application instead.

## How a change reaches the cluster

1. Push to `main`. `.github/workflows/ci.yaml` runs `go test`, builds the
   image and pushes it to `ghcr.io/slqzeer/beacon`.
2. The same job rewrites `deploy/kustomization.yaml` to pin the new
   `tag@digest` and commits that back. It uses `GITHUB_TOKEN`, and GitHub does
   not trigger workflow runs from `GITHUB_TOKEN` pushes, so this does not loop.
3. Argo CD watches this repository's `main` and syncs `deploy/`.

The image is pinned by **both** tag and digest. The tag keeps
`kubectl get pod` readable; the digest is what containerd actually resolves.

## Deployment lives here, credentials do not

`deploy/` is owned by this repository, per the homelab README's "Application
repositories" rule. The GHCR pull credential is **not** here: it lives in
Vault and reaches the cluster through the Vault Secrets Operator. See
`platform/registry/README.md` in the homelab repository.
```

Create `/srv/projects/beacon/.gitignore`:

```gitignore
# Local build output. The image is built in CI; nothing built here is shipped.
/beacon
/out/
```

- [ ] **Step 12: Commit**

```bash
cd /srv/projects/beacon
git add mise.toml go.mod main.go main_test.go Dockerfile README.md .gitignore
git commit -m "Add beacon: the phase-20 canary

A deliberately trivial Go service that reports which commit is live, so
the homelab's CI/CD chain can be verified end to end with one curl.

/version reports a stamp injected at build time via -ldflags, defaulting
to \"unknown\" so an unstamped build is obviously unstamped rather than
plausible. The test asserts the injected value specifically -- a test
that only checked for a 200 would pass against a handler that always
answered \"unknown\", which is exactly the bug that would make the whole
chain unverifiable.

Static binary on scratch. CGO_ENABLED=0 is load-bearing: with cgo the
binary links libc dynamically and scratch has none, which fails at
runtime with an error naming the binary rather than the missing loader.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 13: Hand the push to the user**

Print exactly this, on one line, and wait:

```
git -C /srv/projects/beacon push -u origin main
```

Nothing is watching this repository yet, so this push deploys nothing. It only
needs to land before Task 4 adds the workflow that runs on it.

---

### Task 4: beacon's deploy manifests and its CI workflow

This is the task that makes the chain a chain.

**Files:**
- Create: `/srv/projects/beacon/deploy/deployment.yaml`
- Create: `/srv/projects/beacon/deploy/service.yaml`
- Create: `/srv/projects/beacon/deploy/ingress.yaml`
- Create: `/srv/projects/beacon/deploy/kustomization.yaml`
- Create: `/srv/projects/beacon/.github/workflows/ci.yaml`

**Interfaces:**
- Consumes: the image entrypoint and `VERSION` build arg from Task 3.
- Produces: the image `ghcr.io/slqzeer/beacon`, pinned in
  `deploy/kustomization.yaml` as `newTag: sha-<short>` plus
  `digest: sha256:<...>`. Task 6's Application renders `deploy/` with kustomize
  and expects a Secret named `ghcr-pull` to exist in namespace `apps`.

- [ ] **Step 1: Write the Deployment**

Create `/srv/projects/beacon/deploy/deployment.yaml`:

```yaml
# beacon -- the phase-20 canary. Nothing depends on it; see README.md.
#
# The image below says `placeholder` on purpose. CI rewrites the pin in
# kustomization.yaml, and kustomize substitutes it at render time, so this
# literal is never what runs. A plausible-looking default here would be worse
# than an obviously-wrong one: it could deploy silently.
#
# Probes are httpGet, NOT exec. This image is FROM scratch -- there is no
# shell, no sh, no wget and no curl inside it, so an exec probe cannot work
# and would fail in a way that reads like the application being unhealthy.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: beacon
  namespace: apps
spec:
  replicas: 1
  selector:
    matchLabels:
      app: beacon
  template:
    metadata:
      labels:
        app: beacon
    spec:
      # The GHCR package is private. This Secret does not exist in git: it is
      # rendered by the Vault Secrets Operator from Vault at homelab/ghcr.
      # See platform/registry/ in the homelab repository. Without it the pod
      # sits in ImagePullBackOff with a 401 that names no cause.
      imagePullSecrets:
        - name: ghcr-pull
      securityContext:
        runAsNonRoot: true
        # Numeric because scratch has no /etc/passwd. Must match the USER
        # line in the Dockerfile or the kubelet refuses to start the pod.
        runAsUser: 65534
        runAsGroup: 65534
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: beacon
          image: ghcr.io/slqzeer/beacon:placeholder
          ports:
            - name: http
              containerPort: 8080
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 1
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 20
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
            limits:
              memory: 64Mi
```

- [ ] **Step 2: Write the Service and the Ingress**

Create `/srv/projects/beacon/deploy/service.yaml`:

```yaml
# ClusterIP only. The tailnet reaches this through the Ingress beside it.
apiVersion: v1
kind: Service
metadata:
  name: beacon
  namespace: apps
spec:
  selector:
    app: beacon
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Create `/srv/projects/beacon/deploy/ingress.yaml`:

```yaml
# beacon on the tailnet at https://beacon.taildf6cd4.ts.net
#
# The whole point of the canary is answering "which commit is live?" from
# anywhere, in one curl. TLS terminates at the Tailscale proxy with a real
# Let's Encrypt certificate; the hop to the Service is plain HTTP inside the
# cluster, matching Argo CD and Vault.
#
# The proxy-class annotation is NOT decorative: a tailscale Ingress gets a
# dedicated proxy pod by default and the chart's default is
# `resources: {}` -- unbounded. `homelab` is a CLUSTER-SCOPED ProxyClass
# created by the homelab repository at sync-wave 21
# (infrastructure/ingress/config/proxyclass.yaml); this Ingress reconciles at
# wave 23, so it is always already there. It is a cross-repository reference,
# which is why it is spelled out here rather than assumed.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: beacon
  namespace: apps
  annotations:
    tailscale.com/proxy-class: homelab
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: beacon
      port:
        number: 8080
  tls:
    # Short name only. Becomes beacon.taildf6cd4.ts.net via MagicDNS.
    - hosts:
        - beacon
```

- [ ] **Step 3: Write the kustomization — header comments only**

Create `/srv/projects/beacon/deploy/kustomization.yaml`:

```yaml
# THIS FILE IS REWRITTEN BY CI. See .github/workflows/ci.yaml.
#
# Do not put a comment anywhere below the header block. Verified on kustomize
# v5.8.1: `kustomize edit set image` preserves comments but RELOCATES them --
# a comment attached to an entry under `images:` migrated up under
# `resources:`, where it then described the wrong line entirely. A comment
# that silently starts lying is worse than no comment. Header comments at the
# top of the file do stay put, which is why all the explanation is here.
#
# `newTag: placeholder` with no digest is the pre-first-CI-run state, and it
# is deliberately unrunnable: the tag does not exist in GHCR, so a cluster
# that somehow rendered this would report ImagePullBackOff rather than
# quietly running something plausible.
#
# After CI runs, this block carries BOTH newTag and digest. kustomize renders
# them together as name:tag@digest -- the tag keeps `kubectl get pod`
# readable, the digest is what containerd actually resolves. Verified on
# v5.8.1; it does not error on having both.
resources:
  - deployment.yaml
  - service.yaml
  - ingress.yaml
images:
  - name: ghcr.io/slqzeer/beacon
    newTag: placeholder
```

- [ ] **Step 4: Verify the kustomization renders before wiring CI to it**

```bash
cd /srv/projects/beacon/deploy && kubectl kustomize .
```

Expected: three documents, with `image: ghcr.io/slqzeer/beacon:placeholder`.
`kubectl` bundles kustomize v5.8.1 on this host, so no extra tool is needed for
rendering — only `kustomize edit` (used in CI) needs the standalone binary.

- [ ] **Step 5: Write the CI workflow**

Create `/srv/projects/beacon/.github/workflows/ci.yaml`:

```yaml
# test -> build -> push to GHCR -> write the pin back.
#
# The write-back is what makes this continuous delivery rather than just
# publishing. It commits the new tag@digest into deploy/kustomization.yaml,
# and Argo CD -- which watches this repository's main -- picks it up.
name: ci

on:
  push:
    branches: [main]

permissions:
  # Required for the write-back commit. This is what makes the job able to
  # push to its own repository.
  contents: write
  # Required to push the image to ghcr.io. GITHUB_TOKEN can publish a package
  # owned by this repository; it CANNOT be used by the cluster to pull one.
  # That is a separate, long-lived PAT held in Vault -- see the homelab
  # repository, platform/registry/README.md.
  packages: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5

      - uses: actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16  # v6
        with:
          go-version: "1.27.1"

      # The roadmap's flow has a `tests` stage. This is it, and it is real --
      # the canary has actual tests, so the link is exercised rather than
      # stubbed. A failure here must stop the build.
      - name: Test
        run: go test ./... -v

      - uses: docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9  # v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta
        uses: docker/metadata-action@c299e40c65443455700f0fdfc63efafe5b349051  # v5
        with:
          # Lowercase, hardcoded. GHCR rejects uppercase in a repository
          # path, and this account is `Slqzeer` -- so ${{ github.repository }}
          # would produce ghcr.io/Slqzeer/beacon and fail the push with an
          # error about the name, not about the case.
          images: ghcr.io/slqzeer/beacon
          tags: |
            type=sha,prefix=sha-,format=short

      - id: build
        uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8  # v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          build-args: |
            VERSION=sha-${{ github.sha }}
          # Links the GHCR package to this repository, so the package page
          # shows the source and inherits its README.
          annotations: ${{ steps.meta.outputs.annotations }}

      - name: Install kustomize
        # kubectl bundles kustomize for RENDERING but does not expose
        # `kustomize edit`, which is what rewrites the pin. Hence the
        # standalone binary, pinned.
        run: |
          curl -sL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.8.1/kustomize_v5.8.1_linux_amd64.tar.gz" -o kustomize.tgz
          tar xzf kustomize.tgz kustomize
          sudo mv kustomize /usr/local/bin/

      - name: Pin the new image and commit it back
        # WHY THIS DOES NOT LOOP: the push below uses GITHUB_TOKEN, and
        # GitHub does not trigger workflow runs from GITHUB_TOKEN pushes.
        # The protection is a property of the platform, not a [skip ci]
        # marker in a commit message that a later edit could drop.
        #
        # IF YOU EVER SWAP GITHUB_TOKEN FOR A PAT HERE -- a common reaction
        # to a branch-protection error -- THAT PROTECTION DISAPPEARS and this
        # job will retrigger itself indefinitely. Solve branch protection by
        # excepting this workflow, never by changing the token.
        env:
          TAG: ${{ steps.meta.outputs.version }}
          DIGEST: ${{ steps.build.outputs.digest }}
        run: |
          set -euo pipefail
          cd deploy
          kustomize edit set image "ghcr.io/slqzeer/beacon:${TAG}@${DIGEST}"
          cd ..
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add deploy/kustomization.yaml
          if git diff --staged --quiet; then
            echo "pin unchanged, nothing to commit"
            exit 0
          fi
          git commit -m "Deploy ${TAG}

          Image: ghcr.io/slqzeer/beacon:${TAG}@${DIGEST}
          Built from ${GITHUB_SHA}."
          git push origin HEAD:main
```

- [ ] **Step 6: Commit**

```bash
cd /srv/projects/beacon
git add deploy .github/workflows/ci.yaml
git commit -m "Add deploy manifests and the CI chain

CI tests, builds, pushes to GHCR, then rewrites deploy/kustomization.yaml
with the new tag@digest and commits it back. Argo CD watches this
repository's main, so that commit is the deploy.

Pinned by BOTH tag and digest: kustomize renders them as name:tag@digest,
so the tag keeps kubectl output readable while the digest is what
containerd resolves. Verified on kustomize v5.8.1.

kustomization.yaml carries header comments only. kustomize edit preserves
comments but relocates them -- a comment on an images: entry migrated up
under resources: and then described the wrong line. A comment that starts
lying silently is worse than none.

The write-back uses GITHUB_TOKEN, and GitHub does not retrigger workflows
on GITHUB_TOKEN pushes, so there is no loop and no need for a [skip ci]
marker that a later edit could drop.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 7: Hand the push to the user and verify the first run**

Print exactly this, on one line, and wait:

```
git -C /srv/projects/beacon push origin main
```

Then check, at <https://github.com/Slqzeer/beacon/actions>:

1. **`go test` ran and passed** — the `tests` link of the chain is real.
2. **A package appeared** at
   <https://github.com/Slqzeer?tab=packages> named `beacon`.
3. **A write-back commit exists** on `main`, authored by
   `github-actions[bot]`, titled `Deploy sha-...`.
4. **That commit did NOT start a second workflow run.** If it did, the push is
   not using `GITHUB_TOKEN` — fix that before going further, or every push will
   build forever.
5. `git -C /srv/projects/beacon pull` then
   `cat /srv/projects/beacon/deploy/kustomization.yaml` — confirm `newTag` and
   `digest` are both present and the header comments are still at the top.

**Record the package's visibility** (private is the GHCR default for a package
pushed from a private repository, but confirm rather than assume — Task 5's
whole purpose is the credential that a private package requires). If it is
public, either set it to private on the package settings page or tell the user
the pull-credential path in Tasks 5–6 is being built for a package that does
not need it.

---

### Task 5: The GHCR credential in Vault, and Argo CD's access to the beacon repo

Two credentials, both hand-created, neither reproducible from any repository.
This task is where the phase's one genuinely new credential class appears: the
PAT is issued by github.com and pasted **in**, unlike every other secret here,
which is generated inside Vault and never seen.

**Files:**
- Modify: `platform/vault/configure-vault.sh` (append policy and role blocks)
- Create: `platform/registry/README.md`

**Interfaces:**
- Consumes: nothing in git.
- Produces: Vault KV at `homelab/ghcr` with keys `username` and `password`;
  Vault policy `vso-ghcr-read`; Vault role `vso-ghcr` bound to ServiceAccount
  `registry` in namespace `apps`, audience `vault`. Task 6's `VaultAuth` must
  match the role name, ServiceAccount name and audience exactly.

- [ ] **Step 1: Record the "before" state of the ceremony script**

```bash
cd /srv/projects/homelab
grep -c '^echo "==>' platform/vault/configure-vault.sh
tail -20 platform/vault/configure-vault.sh
```

Expected: `14` step markers, ending with the `vso-redis` role and
`echo "==> done"`. If the count differs, the script has genuinely changed
since this count was recorded — read it fully before appending.

- [ ] **Step 2: Append the policy and role, and nothing else**

Insert the following **before** the final `echo "==> done"` line in
`platform/vault/configure-vault.sh`:

```sh
echo "==> policy vso-ghcr-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-ghcr-read - <<'POLICY'
path "homelab/data/ghcr" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-ghcr"
# bound_service_account_names must match the ServiceAccount created in
# platform/registry/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-ghcr \
    bound_service_account_names=registry \
    bound_service_account_namespaces=apps \
    audience=vault \
    token_policies=vso-ghcr-read \
    ttl=1h
```

**There is deliberately no `seeding homelab/ghcr` block**, unlike every other
credential in this script. Two independent reasons, either sufficient:

1. A GHCR PAT is issued by github.com. It cannot be generated in the pod, so
   the value would have to be written into the script — which puts a
   credential in git.
2. The script is run as `kubectl exec -i vault-0 -- sh < configure-vault.sh`,
   so the pod's stdin **is the script**. A `read` prompt inside it would
   consume the script's own remaining lines rather than waiting for a human,
   failing in a way that looks like a corrupted script rather than a design
   error.

The seeding is therefore a separate, interactive ceremony — Step 6.

- [ ] **Step 3: Verify the script still parses, and that the new blocks are guarded correctly**

```bash
cd /srv/projects/homelab
sh -n platform/vault/configure-vault.sh && echo "PARSES OK"
grep -c '^echo "==>' platform/vault/configure-vault.sh
grep -n 'homelab/data/ghcr\|vso-ghcr' platform/vault/configure-vault.sh
```

Expected: `PARSES OK`, a step count of `16`, and the policy path showing
`homelab/data/ghcr` — **with the `data/` segment**. Without it the policy
matches nothing and produces a permission denial that reads exactly like a
wrong path. This has bitten three previous phases; it is the single most
likely mistake in this task.

Confirm also that the script contains no secret:

```bash
grep -in 'ghp_\|github_pat_\|token=\|password=' platform/vault/configure-vault.sh
```

Expected: only the existing `password=@"$PWFILE"` lines from the postgres and
redis blocks. If a literal PAT appears, stop and remove it — and treat that PAT
as burned.

- [ ] **Step 4: Write the registry README**

Create `platform/registry/README.md`:

```markdown
# GHCR pull credential

The cluster pulls private images from `ghcr.io/slqzeer/*`. This directory
holds the path that credential takes: Vault -> Vault Secrets Operator ->
a `kubernetes.io/dockerconfigjson` Secret named `ghcr-pull` in namespace
`apps`.

## This credential is different from every other one here

Every other secret in this cluster is generated inside Vault by
`platform/vault/configure-vault.sh` and never seen by a human. A GHCR
personal access token cannot work that way: GitHub issues it, and it must be
pasted in.

It is therefore **not** in `configure-vault.sh`. Putting it there would either
place a credential in git, or require an interactive prompt in a script that
is fed to the pod on stdin — where a `read` would consume the script's own
remaining lines instead of waiting for input. The script carries only the
policy and the role; the value is seeded by the ceremony below.

## Issuing the token

GHCR pulls need a **classic** personal access token. Fine-grained tokens do
not carry package scopes.

1. <https://github.com/settings/tokens> -> Generate new token (classic)
2. Scope: **`read:packages` only**. Nothing else. This token can only pull.
3. Set an expiry and **write the date down** — see "When it expires" below.

## Seeding it into Vault

Run at a real terminal, not through a piped script:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh'

Then, inside the pod:

    vault login
    umask 077
    TMPF=$(mktemp)
    stty -echo; printf 'Paste the PAT, then press Enter: '; read -r PAT; stty echo; printf '\n'
    printf '%s' "$PAT" > "$TMPF"
    unset PAT
    wc -c < "$TMPF"
    vault kv put homelab/ghcr username=Slqzeer password=@"$TMPF"
    rm -f "$TMPF"
    rm -f /home/vault/.vault-token
    exit

`stty -echo` keeps the token off the screen. `printf '%s'` writes it with **no
trailing newline** — a newline inside the token produces a 401 whose message
says nothing about whitespace. `password=@"$TMPF"` passes only the *filename*
as an argument, so the token never appears in `ps`; this is the same form the
unsealer and `configure-vault.sh` use.

`wc -c` prints the byte count. Check it against the token's real length before
continuing — it is the only confirmation available that the paste was complete,
and it reveals nothing.

## When it expires

**Running pods are not affected.** Only *new* pulls fail, so an expired token
surfaces at the next rollout, node restart or eviction — arbitrarily far from
the cause, as `ImagePullBackOff` with a 401.

To rotate: issue a new classic token with `read:packages`, repeat the seeding
ceremony above (`vault kv put` overwrites), and VSO propagates it within its
`refreshAfter` window. Nothing needs restarting, and no manifest changes.

| Field | Value |
| --- | --- |
| Issued | _fill in_ |
| Expires | _fill in_ |
| Scope | `read:packages` |
| Vault path | `homelab/ghcr` |

## Related

- `platform/registry/config/vault-secrets.yaml` — the VSO wiring
- `platform/vault-secrets-operator/README.md` — how VSO authenticates
- The `beacon` repository — the first consumer
```

- [ ] **Step 5: Commit**

```bash
cd /srv/projects/homelab
git add platform/vault/configure-vault.sh platform/registry/README.md
git commit -m "Add the GHCR pull credential's Vault policy, role and ceremony

The cluster needs to pull private images from ghcr.io. This adds the
vso-ghcr-read policy and the vso-ghcr role to the ceremony script, and
documents how the token is issued and seeded.

The token itself is deliberately NOT seeded by configure-vault.sh, unlike
every other credential here. A GHCR PAT is issued by github.com rather
than generated in the pod, so scripting it would put a credential in git
-- and the script is fed to the pod on stdin, so an interactive read
inside it would consume the script's own remaining lines rather than
waiting for a human.

Classic token, read:packages only. Its expiry is the operational risk:
running pods keep running, and only new pulls fail, so it surfaces as a
401 at the next rollout, far from the cause. README records that.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 6: Hand the push and both ceremonies to the user**

Print the push, on one line:

```
git push origin main
```

Then ask the user to do three things, in this order, and wait for each.

**(a) Issue the PAT and seed it.** Point them at
`platform/registry/README.md`, sections "Issuing the token" and "Seeding it
into Vault". **Do not paste the token into this conversation.** Ask them to
report only the `wc -c` byte count and whether it matched.

**(b) Re-run the ceremony script**, so the new policy and role exist:

```
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
```

then:

```
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
```

then:

```
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

The script is idempotent; re-running it leaves the existing postgres and redis
credentials untouched and only adds the two new objects.

**(c) Create the beacon deploy key.** Argo CD must clone a *second* private
repository, and the existing `repo-homelab` credential grants no access to it.
Ask the user to run:

```
ssh-keygen -t ed25519 -N "" -C "argocd-beacon-deploy" -f ~/.ssh/argocd_beacon_deploy
```

then add the **public** half at
<https://github.com/Slqzeer/beacon/settings/keys> as a deploy key, **read-only
— leave "Allow write access" unchecked**. Argo CD only ever reads; a writable
key here would let anything that compromised the cluster push to the repository
that deploys into it.

Then create the Secret, matching the convention `repo-homelab` already uses:

```
sg k3s-admin -c 'kubectl -n argocd create secret generic repo-beacon --from-literal=type=git --from-literal=url=git@github.com:Slqzeer/beacon.git --from-file=sshPrivateKey=$HOME/.ssh/argocd_beacon_deploy'
```

```
sg k3s-admin -c 'kubectl -n argocd label secret repo-beacon argocd.argoproj.io/secret-type=repository'
```

**The label is what makes Argo CD treat the Secret as a repository
credential.** Without it the Secret is inert and the clone fails with an
authentication error that names no cause.

- [ ] **Step 7: Verify all three, from evidence rather than assumption**

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault policy read vso-ghcr-read'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault read auth/kubernetes/role/vso-ghcr'
sg k3s-admin -c "kubectl -n vault exec vault-0 -- vault kv get -field=username homelab/ghcr"
sg k3s-admin -c "kubectl -n argocd get secret repo-beacon -o jsonpath='{.metadata.labels}'"
```

Expected, in order: a policy on `homelab/data/ghcr` (**with `data/`**); a role
showing `bound_service_account_names [registry]`,
`bound_service_account_namespaces [apps]`, `audience vault`,
`token_policies [vso-ghcr-read]`; the username `Slqzeer`; and the label
`{"argocd.argoproj.io/secret-type":"repository"}`.

**Read the username, never the password.** Confirming the username proves the
path exists and the write succeeded, and discloses nothing.

Finally, confirm Argo CD can actually clone the new repository — the Secret
existing proves only that a Secret exists:

```bash
sg k3s-admin -c 'kubectl -n argocd rollout restart deploy/argocd-repo-server'
sg k3s-admin -c 'kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=3m'
```

---

### Task 6: The cluster wiring

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Create: `platform/registry/config/vault-secrets.yaml`
- Create: `environments/homelab/apps/registry.yaml`
- Create: `environments/homelab/apps/beacon.yaml`

**Interfaces:**
- Consumes: Vault role `vso-ghcr`, policy `vso-ghcr-read` and the KV path
  `homelab/ghcr` from Task 5; `deploy/` in the beacon repository from Task 4.
- Produces: Secret `ghcr-pull` (type `kubernetes.io/dockerconfigjson`) in
  namespace `apps`, consumed by beacon's `imagePullSecrets`.

**A deliberate refinement to the spec's §10.** The spec places `beacon` at
wave 23 alongside `postgres` and `redis`. Splitting the credential into its own
`registry` Application — which the spec did not do — creates a **real**
dependency: beacon's pod cannot pull until `ghcr-pull` exists. So `registry`
takes wave 23 and `beacon` takes **24**. This is not the "stacking out of
habit" the root README warns against; that warning is about components with *no*
dependency on the wave below. Here there is one, and nothing sits behind beacon,
so the extra wave gates nothing.

- [ ] **Step 1: Add the namespace**

Append to `bootstrap/namespaces/namespaces.yaml`:

```yaml
---
# Applications built and published by this homelab's own CI. Holds the
# GHCR pull credential (platform/registry/) and its consumers.
#
# NOTE: this namespace and the top-level `apps/` DIRECTORY in this repository
# are unrelated. That directory is not watched by anything -- an Application
# placed there is silently ignored, as the root README explains -- and it stays
# empty because application manifests live in their own repositories. The
# shared name is a coincidence worth knowing about, not a connection.
apiVersion: v1
kind: Namespace
metadata:
  name: apps
```

- [ ] **Step 2: Write the VSO wiring**

Create `platform/registry/config/vault-secrets.yaml`:

```yaml
# The GHCR pull credential's path: Vault KV -> VSO -> a dockerconfigjson
# Secret that the kubelet uses to authenticate image pulls.
#
# All four objects live in `apps` and that is a requirement, not a preference:
# the VaultAuth CRD states its ServiceAccount "must reside in the consuming
# secret's namespace". This namespace is new in phase 20, so unlike `redis` in
# phase 19 there is no existing VaultConnection here to reuse -- this file
# creates the first one for `apps`, the way phase 18 did for `databases`.
---
# The identity Vault trusts. Its name must match bound_service_account_names
# in platform/vault/configure-vault.sh.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: registry
  namespace: apps
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: vault
  namespace: apps
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
  name: registry
  namespace: apps
spec:
  vaultConnectionRef: vault
  method: kubernetes
  mount: kubernetes
  kubernetes:
    # role and audiences must both match platform/vault/configure-vault.sh.
    # A mismatch in either is a permission denial that names neither side,
    # while Argo CD reports everything Synced.
    role: vso-ghcr
    serviceAccount: registry
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: ghcr-pull
  namespace: apps
spec:
  vaultAuthRef: registry
  mount: homelab
  type: kv-v2
  path: ghcr
  refreshAfter: 60s
  destination:
    name: ghcr-pull
    create: true
    # This is what makes the kubelet treat it as registry credentials rather
    # than an opaque blob. Without it the Secret is created, Argo CD reports
    # Healthy, and every pull still fails with a 401.
    type: kubernetes.io/dockerconfigjson
    transformation:
      excludeRaw: true
      # Drop EVERY source field. Verified against the live redis-credentials
      # Secret: templates ADD keys, they do not replace them, so without this
      # the Secret would carry the PAT twice -- once inside .dockerconfigjson
      # and once as a plain `password` key. That would be a third instance of
      # the _raw duplication the root README already tracks as a known gap,
      # created in the very phase that documents why it matters.
      #
      # Per the CRD, these filters are "never applied to templated fields",
      # so the template below can still read `username` and `password` even
      # though neither reaches the destination Secret.
      excludes:
        - ".*"
      templates:
        # Renders the whole docker config. The key name must be exactly
        # `.dockerconfigjson`, leading dot included -- that is the key the
        # kubelet looks for in a secret of this type.
        #
        # username/password are used rather than a base64 `auth` field on
        # purpose: it needs no encoding helper, so this does not depend on
        # which template function library VSO ships. The kubelet accepts
        # either form.
        ".dockerconfigjson":
          text: |
            {"auths":{"ghcr.io":{"username":{{ get .Secrets "username" | toJson }},"password":{{ get .Secrets "password" | toJson }}}}}
```

- [ ] **Step 3: Write both Applications**

Create `environments/homelab/apps/registry.yaml`:

```yaml
# The GHCR pull credential. Phase 20.
#
# Wave 23, beside postgres and redis: it needs the VSO operator (21) running
# and the Vault ceremony already run, which are the same two preconditions
# those two share. It depends on neither of them, so it shares their wave
# rather than queueing behind them.
#
# `beacon` sits at 24 rather than here, because it genuinely depends on the
# Secret this Application creates -- see environments/homelab/apps/beacon.yaml.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: registry
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: platform/registry/config
  destination:
    server: https://kubernetes.default.svc
    namespace: apps
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

Create `environments/homelab/apps/beacon.yaml`:

```yaml
# beacon, the phase-20 canary. Its manifests live in ITS OWN repository --
# this file is the only thing about it in the homelab repo, per the root
# README's "Application repositories" rule.
#
# Wave 24, NOT 23. The root README warns against stacking a wave out of
# habit, and that warning is about components with no dependency on the wave
# below. This one has a real dependency: the pod cannot pull its image until
# `registry` (wave 23) has created the ghcr-pull Secret. Without the ordering
# the pod would ImagePullBackOff and recover on its own -- correct, but noisy
# and indistinguishable from a genuinely broken credential.
#
# Nothing sits behind wave 24, so an unhealthy canary gates nothing, and it is
# well after ingress-config (21), so a failure here costs no tailnet URL.
#
# targetRevision is `main`: CI commits the new tag@digest into that branch, so
# following it IS the deploy. The image is pinned by digest inside deploy/, so
# following a branch here does not mean following a moving image.
#
# Requires the `repo-beacon` deploy-key Secret in the argocd namespace. It
# exists in no repository -- see the root README's rebuild list.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: beacon
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "24"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/beacon.git
    targetRevision: main
    path: deploy
  destination:
    server: https://kubernetes.default.svc
    namespace: apps
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 4: Run the Task 1 gate locally before pushing**

The gate exists now, so use it — this is the first task that adds manifests
since it landed.

```bash
cd /srv/projects/homelab
/tmp/kubeconform -strict -summary -kubernetes-version 1.36.0 -ignore-filename-pattern 'values\.yaml$' -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' bootstrap environments infrastructure platform
docker run --rm -v "$PWD":/w:ro -w /w python:3.13-alpine sh -c "pip install -q yamllint==1.38.0 && yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure platform .github; echo EXIT=\$?" 2>&1 | grep -v WARNING | tail -3
```

Expected: `Summary: 42 resources found in 23 files - Valid: 42, Invalid: 0,
Errors: 0, Skipped: 0`, and `EXIT=0`.

The arithmetic, so a mismatch is diagnosable rather than just alarming: 35
resources in 20 files before this task, plus the `apps` Namespace (1), the four
objects in `vault-secrets.yaml` (ServiceAccount, VaultConnection, VaultAuth,
VaultStaticSecret), and the two Applications — 7 new resources across 3 new
files. A different count means a document is missing or duplicated; a non-zero
`Skipped` means a CRD schema failed to fetch and those resources went
unchecked.

If the `.dockerconfigjson` template line exceeds 100 characters, either wrap it
or raise `line-length` in `.yamllint.yaml` **with a comment saying why** —
do not silently disable the rule.

- [ ] **Step 5: Commit**

```bash
cd /srv/projects/homelab
git add bootstrap/namespaces/namespaces.yaml platform/registry/config/vault-secrets.yaml environments/homelab/apps/registry.yaml environments/homelab/apps/beacon.yaml
git commit -m "Wire the GHCR pull credential and the beacon canary into the cluster

Namespace apps, its own VaultConnection (the CRD requires the VaultAuth's
ServiceAccount to live in the consuming Secret's namespace, and this
namespace is new), and a VaultStaticSecret that renders a
kubernetes.io/dockerconfigjson Secret from Vault at homelab/ghcr.

destination.type is load-bearing: without it the Secret is created, Argo
CD reports Healthy, and every pull still fails with a 401.

excludes: [\".*\"] drops every source field. VSO templates ADD keys rather
than replacing them -- verified against the live redis-credentials Secret
-- so without it the PAT would ship twice, once in .dockerconfigjson and
once as a plain password key. Per the CRD these filters never apply to
templated fields, so the template still reads both.

registry is wave 23; beacon is 24. That is not habit-stacking: beacon's
pod genuinely cannot pull until registry has created the Secret. Nothing
sits behind 24.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 6: Hand the push to the user**

Print exactly this, on one line, and wait:

```
git push origin main
```

- [ ] **Step 7: Verify the Secret is right before checking whether the pod runs**

```bash
sg k3s-admin -c 'kubectl -n argocd get applications'
sg k3s-admin -c "kubectl -n apps get secret ghcr-pull -o jsonpath='{.type}{\"\n\"}'"
sg k3s-admin -c "kubectl -n apps get secret ghcr-pull -o jsonpath='{.data}'" | python3 -c "import sys,json;print(list(json.load(sys.stdin).keys()))"
```

Expected, in order: all twelve Applications `Synced`/`Healthy`;
`kubernetes.io/dockerconfigjson`; and **exactly** `['.dockerconfigjson']`.

**That last one is the check that matters.** If it lists `password` or
`username` as well, `excludes` did not take effect and the PAT is stored twice
in the cluster. Fix it before moving on — this phase documents that gap; it
must not create another instance of it.

Confirm the rendered config is well-formed JSON naming the right registry,
**without printing the password**:

```bash
sg k3s-admin -c "kubectl -n apps get secret ghcr-pull -o jsonpath='{.data.\.dockerconfigjson}'" | base64 -d | python3 -c "import sys,json;d=json.load(sys.stdin);print('registries:',list(d['auths'].keys()));print('username:',d['auths']['ghcr.io']['username']);print('password length:',len(d['auths']['ghcr.io']['password']))"
```

Expected: `registries: ['ghcr.io']`, `username: Slqzeer`, and a password length
matching the `wc -c` recorded in Task 5. Printing the length rather than the
value proves the template rendered without disclosing the token.

- [ ] **Step 8: Verify the pod is actually running the published image**

```bash
sg k3s-admin -c 'kubectl -n apps get pods -o wide'
sg k3s-admin -c "kubectl -n apps get deploy beacon -o jsonpath='{.spec.template.spec.containers[0].image}{\"\n\"}'"
```

Expected: one `Running` pod, and an image of the form
`ghcr.io/slqzeer/beacon:sha-xxxxxxx@sha256:...` — **both** halves present. If
it still says `placeholder`, CI's write-back did not land or Argo CD has not
synced it.

If the pod is in `ImagePullBackOff`, read the events before changing anything:

```bash
sg k3s-admin -c 'kubectl -n apps describe pod -l app=beacon' | tail -25
```

A 401 means the credential path is wrong (check Step 7's output first, then the
Vault role binding from Task 5). A 404 means the digest is not in GHCR, which
points at CI, not at the cluster.

---

### Task 7: Prove the chain end to end

Everything is built. This task is the one that says whether it *works* — and it
is deliberately separate, because every previous task's verification proved only
its own link.

**Files:**
- Modify: `/srv/projects/beacon/main.go` (a trivial, revertible change)

**Interfaces:**
- Consumes: everything from Tasks 3–6.
- Produces: evidence, recorded in the task notes. No lasting code change.

- [ ] **Step 1: Record the current live commit**

```bash
curl -s https://beacon.taildf6cd4.ts.net/version
```

Expected: `{"version":"sha-<something>"}` matching the current `main` of the
beacon repository. Record the value.

If this does not resolve, check that the Ingress got a hostname before assuming
the application is broken:

```bash
sg k3s-admin -c 'kubectl -n apps get ingress beacon'
```

An Ingress stays `Progressing` until the Tailscale control plane provisions it.
Also confirm HTTPS Certificates are still enabled on the tailnet — the root
README's first-install step 1 — since without it the hostname resolves but
every TLS handshake fails, naming nothing in the cluster.

- [ ] **Step 2: Make a trivial change and push it**

```bash
cd /srv/projects/beacon
sed -i 's|log.Printf("beacon %s listening on %s", version, addr)|log.Printf("beacon %s listening on %s (chain check)", version, addr)|' main.go
mise exec -- go test ./...
git add main.go
git commit -m "Log line tweak to exercise the phase-20 chain end to end"
```

Expected: tests pass. Then print the push, on one line, and wait:

```
git -C /srv/projects/beacon push origin main
```

- [ ] **Step 3: Watch the chain, one link at a time**

Check each, in order, and record what you saw. Do not skip to the end — if the
chain breaks, which link broke is the whole diagnostic.

1. The `ci` workflow ran and `go test` passed.
2. A new package version exists in GHCR with a new digest.
3. A `Deploy sha-...` commit by `github-actions[bot]` is on beacon's `main`.
4. **No second workflow run was triggered by that commit.** If one was, stop —
   the write-back is not using `GITHUB_TOKEN` and this will build forever.
5. Argo CD picked it up:

```bash
sg k3s-admin -c 'kubectl -n argocd get application beacon'
sg k3s-admin -c "kubectl -n apps get deploy beacon -o jsonpath='{.spec.template.spec.containers[0].image}{\"\n\"}'"
```

6. The live answer changed:

```bash
curl -s https://beacon.taildf6cd4.ts.net/version
```

Expected: a **different** SHA from Step 1, matching the new commit. That single
line is the whole phase: a push became a running pod, unattended.

- [ ] **Step 4: Prove the pull actually used the credential**

This needs care, because the two obvious tests both lie. Deleting the
`ghcr-pull` Secret proves nothing — VSO recreates it within `refreshAfter`, so
the pull may simply race and win. Restarting the pod proves nothing either —
containerd already has the image cached on the node, so it never contacts GHCR
at all.

Remove **both** the cache and the credential reference:

```bash
sg k3s-admin -c "kubectl -n apps get deploy beacon -o jsonpath='{.spec.template.spec.containers[0].image}{\"\n\"}'"
```

Record that image reference, then:

```bash
sudo k3s crictl rmi "$(sg k3s-admin -c "kubectl -n apps get deploy beacon -o jsonpath='{.spec.template.spec.containers[0].image}'")"
sg k3s-admin -c 'kubectl -n apps patch deploy beacon --type json -p "[{\"op\":\"remove\",\"path\":\"/spec/template/spec/imagePullSecrets\"}]"'
sg k3s-admin -c 'kubectl -n apps rollout status deploy/beacon --timeout=90s'
```

Expected: the rollout does **not** complete, and:

```bash
sg k3s-admin -c 'kubectl -n apps describe pod -l app=beacon' | grep -i -A3 'Failed\|401\|unauthorized'
```

shows an authorisation failure pulling from `ghcr.io`. **That failure is the
proof.** If the pod starts anyway, either the package is public — in which case
this whole credential path is unnecessary and the phase should say so — or the
image was still cached and the `crictl rmi` did not take effect.

Now restore it. Argo CD's `selfHeal` will revert the patch on its own, but do
not wait passively — confirm it:

```bash
sg k3s-admin -c 'kubectl -n argocd get application beacon'
sg k3s-admin -c 'kubectl -n apps rollout status deploy/beacon --timeout=3m'
curl -s https://beacon.taildf6cd4.ts.net/version
```

Expected: `Synced`/`Healthy`, the rollout completes, and `/version` answers
again. **This doubles as a genuine `selfHeal` test** — a hand-patched
Deployment being reverted without intervention is exactly what this repository
assumes everywhere and has never explicitly verified.

- [ ] **Step 5: Revert the throwaway change**

```bash
cd /srv/projects/beacon
git revert --no-edit HEAD~1
mise exec -- go test ./...
```

Use `HEAD~1` advisedly: `HEAD` is CI's `Deploy sha-...` commit, not yours. Check
with `git log --oneline -3` first and revert the log-line commit specifically,
by hash, if the history looks different from expected.

Then print the push, on one line, and wait:

```
git -C /srv/projects/beacon push origin main
```

This runs the chain a second time, which is itself worth having: the first run
of a pipeline often passes for reasons that do not repeat.

---

### Task 8: Correct the documentation this phase invalidated

Two documents now say things that are false, and one of them is dangerous. This
is not tidying — the networking README currently tells a future reader to treat
the admin console as authoritative, which after Task 9 will silently lose their
work.

**Files:**
- Modify: `infrastructure/networking/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the etag measurement recorded in Task 2, Step 5.
- Produces: documentation matching reality. No code.

- [ ] **Step 1: Flip the authority claim in the networking README**

Replace the section that currently reads:

```markdown
Until phase 20 wires up `tailscale/gitops-acl-action` in GitHub Actions,
this file is a record of what should be live, kept under review, and
applied by hand. It can drift. Treat the console as authoritative and
this file as the reviewed copy.
```

with:

```markdown
**Git is authoritative for this file.** Phase 20 wired up
`tailscale/gitops-acl-action`: `.github/workflows/tailscale-acl.yaml` tests
the policy on every pull request and applies it on every push to `main` that
touches it.

### A console edit will be silently reverted

Not "may" — will, at the next push that touches this file, with no warning
that survives to anyone who would act on it. `gitops-pusher` has a drift
guard and **it cannot fire in CI**:

- It compares a cached etag against the control plane's, but the cache file
  (`./version-cache.json`) does not survive a runner. `PrevETag` is therefore
  always empty on entry, and the code fills it with the *current* control
  etag — so the comparison it guards with can never be true.
- Even if it could be, `--fail-on-manual-edits` defaults to false (a printed
  warning, not a failure) and the composite action exposes no input to set it.

So the rule below is not a nicety. It is the only thing standing between a
console edit and its deletion.

### Before editing this file, copy the live policy into it first

The policy file is not merged into the live policy — it **replaces** it. Open
<https://login.tailscale.com/admin/acls>, copy what is actually live into this
file, and make your change on top of that. Skipping this deletes any rule
added in the console since the last push.

If console drift becomes a recurring problem rather than an occasional one,
the escape hatch is to abandon the composite action and invoke
`gitops-pusher` directly with `--fail-on-manual-edits` and a committed
`version-cache.json`. That was considered and rejected for this phase — see
the phase-20 spec, §7.3 — but the reasoning is recorded so it need not be
rediscovered.
```

Also update the sentence at the end of the file that reads
`Automating this in phase 20 needs a second OAuth client with policy_file write
scope, separate from the operator's.` — that is now done, not pending. Replace
it with a statement of what exists: the `TS_OAUTH_CLIENT_ID` and
`TS_OAUTH_SECRET` repository secrets, why the client is separate from the
operator's, and that neither value exists in any repository.

- [ ] **Step 2: Add both new pieces of cluster-only state to the rebuild list**

In the root `README.md`, under "First install / rebuild", add two steps after
the existing step 3 (the `repo-homelab` deploy key), keeping the existing
numbering coherent:

```markdown
4. Create the `repo-beacon` deploy-key Secret in the `argocd` namespace.
   Argo CD clones a **second** private repository — `beacon` — and the
   `repo-homelab` credential grants no access to it. Without this, the
   `beacon` Application reports a clone failure that reads like a
   repository-URL typo. See
   `docs/superpowers/plans/2026-09-16-github-actions-ghcr.md`, Task 5,
   for the key generation and the exact commands. Read-only: Argo CD never
   writes, and a writable key here would let anything that compromised the
   cluster push to the repository that deploys into it.
5. Seed the GHCR pull token into Vault at `homelab/ghcr`. This is a classic
   GitHub PAT with `read:packages` and nothing else. It is **not** created by
   `configure-vault.sh` like every other credential here — GitHub issues it,
   so it must be pasted in, and the script is fed to the pod on stdin where an
   interactive prompt would consume its own remaining lines. Until it exists,
   the `registry` Application is unhealthy and every pod pulling a private
   image sits in `ImagePullBackOff` with a 401. See
   `platform/registry/README.md`.
```

- [ ] **Step 3: Record the new known gaps**

Add to the root `README.md`'s "Known gaps" section:

```markdown
- **CI validates schema, not semantics.** `.github/workflows/validate.yaml`
  runs `kubeconform -strict` on every push, which catches malformed manifests
  and unknown fields. It runs on a GitHub-hosted runner, which cannot reach
  this cluster, so there is no server-side dry-run: a `VaultAuth` naming a
  Vault role that does not exist is schema-perfect and still fails at runtime.
  The gate narrows the window between a bad commit and the cluster; it does
  not close it. Every `role:`/`serviceAccount:`/`audiences:` comment in this
  repository warning that a mismatch "names neither side" still applies
  exactly as before.
- **The four Helm `values.yaml` files are not validated by CI.** They are
  excluded from `kubeconform` by filename pattern because they are not
  Kubernetes manifests and have no `apiVersion`/`kind`. They configure Vault,
  VSO, the Tailscale operator and Argo CD itself — arguably the four most
  consequential files here. The gate covers the many low-risk files and misses
  the few high-risk ones. A green check on this repository means less than it
  looks like it means.
- **The GHCR token expires, and the failure is delayed and misleading.**
  Running pods are unaffected; only *new* pulls fail. An expired token
  therefore surfaces at the next rollout, node restart or eviction —
  arbitrarily far from the cause — as `ImagePullBackOff` with a 401 that names
  no expiry. Rotation is a documented manual step with no alarm on it; the
  expiry date is recorded in `platform/registry/README.md` and nowhere the
  cluster can see it.
```

- [ ] **Step 4: Update the layout table and the Application-repositories section**

In the root `README.md`'s layout table, replace the `platform/registry/` row
(currently folded into "Vault, databases, registry") with an explicit entry,
and add a row for `apps` namespace ownership. Then extend the "Application
repositories" section, which currently mandates pinning but describes no
mechanism, with the one this phase built:

```markdown
The `beacon` repository is the worked example. Its CI publishes to GHCR and
then rewrites `deploy/kustomization.yaml` with the new `tag@digest` and
commits that back; Argo CD watches that branch, so the write-back commit *is*
the deploy. Pinning both tag and digest is deliberate: the tag keeps
`kubectl get pod` readable while the digest is what containerd resolves, and
kustomize renders them together as `name:tag@digest`.

Note that `beacon` is a canary, not an application. It exists to keep this
chain proved. Do not add features to it — build the real thing as its own
repository and leave the canary boring.
```

- [ ] **Step 5: Verify the documentation matches reality**

Documentation drift is what this whole task is fixing, so do not introduce
more of it. Check each claim mechanically:

```bash
cd /srv/projects/homelab
sg k3s-admin -c 'kubectl -n argocd get applications'
grep -n "sync-wave" environments/homelab/apps/*.yaml | sed 's|environments/homelab/apps/||'
grep -c "repo-beacon\|homelab/ghcr" README.md
```

Confirm the wave list in the README's "Adding a component" section matches the
grep output exactly, including `registry` at 23 and `beacon` at 24. The README
currently describes wave 23 as "last of all" — that is no longer true and must
be corrected wherever it appears.

- [ ] **Step 6: Run the gate, then commit**

```bash
cd /srv/projects/homelab
docker run --rm -v "$PWD":/w:ro -w /w python:3.13-alpine sh -c "pip install -q yamllint==1.38.0 && yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure platform .github; echo EXIT=\$?" 2>&1 | grep -v WARNING | tail -3
git add README.md infrastructure/networking/README.md
git commit -m "Correct the documentation phase 20 invalidated

infrastructure/networking/README.md told readers to treat the admin
console as authoritative and this file as the reviewed copy. After the
ACL workflow lands that is false and dangerous: gitops-pusher's drift
guard cannot fire in CI -- its cache file does not survive a runner, so
PrevETag is always empty and gets assigned the current control etag,
making the comparison structurally unable to be true, and
--fail-on-manual-edits defaults to false and is not exposed by the
action. A console edit will be silently reverted. Git is authoritative
now, and copying the live policy in before editing is the only thing
standing between a console edit and its deletion.

Adds repo-beacon and the GHCR token to the rebuild list: both are
cluster-only state that no repository can recreate.

Records three new known gaps -- CI checks schema but not semantics and
cannot reach the cluster; the four Helm values files are excluded from
validation and are the most consequential files here; and the GHCR
token's expiry fails late, misleadingly, and with no alarm.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 7: Hand the push to the user**

```
git push origin main
```

---

### Task 9: Flip the ACL workflow to `apply`

**Last. Do not start this until Tasks 1–8 are complete and Task 2's etag
measurement is in hand.** This is the only step in the phase that can take the
tailnet down, and with it every URL in this homelab including Argo CD's.

**Files:**
- Modify: `.github/workflows/tailscale-acl.yaml`
- Possibly modify: `infrastructure/networking/policy.hujson` (reconciliation)

**Interfaces:**
- Consumes: Task 2's `control:` / `local:` etag comparison.
- Produces: the tailnet policy under GitOps.

- [ ] **Step 1: Reconcile the file with what is actually live — do not skip this**

Open <https://login.tailscale.com/admin/acls>, copy the **live** policy, and
compare it against `infrastructure/networking/policy.hujson`.

Recall Task 2, Step 5:

- **`control` and `local` matched** — the file is already what the tailnet
  runs. Confirm visually anyway; the etags are a checksum of the
  hujson-formatted file, so they are trustworthy, but this is a one-way door.
- **They differed** — the file has drifted. **Copy the live policy into the
  file and commit that first**, as its own commit, before changing the
  workflow. Applying without this deletes whatever was changed in the console.

Whatever the outcome, confirm these two stanzas are present in the file, since
the operator depends on both and losing either breaks all tailnet ingress:

```bash
cd /srv/projects/homelab
grep -n -A4 '"tagOwners"' infrastructure/networking/policy.hujson | grep -v '^\s*//'
grep -n -A5 '"autoApprovers"' infrastructure/networking/policy.hujson | grep -v '^\s*//'
```

Expected: `tag:k8s-operator` owned by nobody, `tag:k8s` owned by
`tag:k8s-operator`, and `autoApprovers.services` granting `tag:k8s` to
`tag:k8s`. These must be the **uncommented** occurrences — the stock Tailscale
example that this file was seeded from contains commented-out `tagOwners` near
the top, and matching that one instead would be a false positive.

- [ ] **Step 2: Change exactly one input**

In `.github/workflows/tailscale-acl.yaml`, split the single job into two so
that a pull request still only tests, while `main` applies:

```yaml
jobs:
  test:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5
      - uses: tailscale/gitops-acl-action@5a4a17f5708e9bf96f4ee915a95e9f83c2eebe1a  # v1
        with:
          tailnet: "-"
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          policy-file: ./infrastructure/networking/policy.hujson
          action: test

  apply:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5
      - uses: tailscale/gitops-acl-action@5a4a17f5708e9bf96f4ee915a95e9f83c2eebe1a  # v1
        with:
          tailnet: "-"
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          policy-file: ./infrastructure/networking/policy.hujson
          action: apply
```

Update the file's header comment: it currently says "THIS JOB DOES NOT APPLY
ANYTHING YET". That is now false. Keep the whole explanation of *why* the drift
guard cannot fire — that reasoning is more important now, not less — and change
only the status sentence.

- [ ] **Step 3: Commit**

```bash
cd /srv/projects/homelab
git add .github/workflows/tailscale-acl.yaml
git commit -m "Apply the tailnet policy from main

Completes the phase-13 debt. The file has been reconciled against the
live policy first, which is mandatory rather than advisory: apply
replaces the live policy wholesale and gitops-pusher's drift guard
cannot fire in CI, so anything changed in the console and not copied
into the file here is deleted by this commit's first run.

Pull requests still only test. Only pushes to main apply.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019W5FsGNLcjBkkvbFovWj9M"
```

- [ ] **Step 4: Hand the push over — and say plainly what it does**

Before printing the command, tell the user in one sentence that this push
**replaces their live tailnet policy** with the file's contents, and confirm
they have done Step 1. Then:

```
git push origin main
```

- [ ] **Step 5: Verify the tailnet did not break**

Immediately after the workflow completes:

```bash
sg k3s-admin -c 'kubectl -n argocd get applications'
curl -s https://beacon.taildf6cd4.ts.net/version
curl -sI https://argocd.taildf6cd4.ts.net | head -1
curl -sI https://vault.taildf6cd4.ts.net | head -1
```

Expected: all Applications still `Synced`/`Healthy`, and all three URLs
answering over TLS. **Check all three**, not just one — they use different
proxies, and a `tagOwners` mistake can take out some and not others.

If a hostname stops resolving, the `tagOwners` or `autoApprovers` stanza was
lost in the apply. Recovery is to restore the policy in the admin console by
hand — <https://login.tailscale.com/admin/acls> — which does not depend on the
tailnet being up, and then fix the file in git **before** pushing anything else,
since the next push would re-apply the broken policy.

```bash
sg k3s-admin -c 'kubectl -n tailscale get pods'
```

is reachable throughout, because `kubectl` talks to the local API server and
not over the tailnet.

- [ ] **Step 6: Confirm the loop closes**

Make a trivial, safe change to the policy file — a comment line — and push it,
to prove the pipeline works in the ordinary case rather than only in the
dramatic one. Then verify the three URLs again.

---

## Phase completion checklist

The phase is done when every one of these has been *seen*, not assumed:

- [ ] `validate` is green on `main`, with `Skipped: 0`
- [ ] `validate` has been observed failing on a deliberately broken manifest
- [ ] `yamllint --strict` is green against the committed `.yamllint.yaml`
- [ ] The ACL `test` job ran and its `control`/`local` etags were recorded
- [ ] A beacon commit produced a new GHCR digest
- [ ] The write-back commit exists and triggered **no** second workflow run
- [ ] Argo CD synced `beacon` unattended
- [ ] `curl https://beacon.taildf6cd4.ts.net/version` returns the new SHA
- [ ] Removing the cached image *and* the `imagePullSecrets` produced a 401
- [ ] `selfHeal` restored the Deployment without intervention
- [ ] `ghcr-pull` carries exactly one key, `.dockerconfigjson`
- [ ] `repo-beacon` and the GHCR token are both in the README's rebuild list
- [ ] The networking README no longer calls the console authoritative
- [ ] The ACL `apply` flip landed last, after reconciliation
- [ ] All tailnet URLs still answer after the apply
