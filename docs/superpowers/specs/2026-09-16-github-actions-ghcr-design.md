# GitHub Actions and GHCR — Design

Covers workstation-plan phase 20. The roadmap frames the phase as a single
pipeline — "git push → GitHub Actions → tests → docker build → GHCR → Argo CD
→ k3s" — and justifies it as the thing that "évite d'installer immédiatement un
registry interne".

**This design delivers that pipeline and three other things**, because the
roadmap's framing is narrower than what phase 20 actually owes. §2 argues why.

## 1. Goal

Make GitHub Actions a load-bearing part of this platform, and prove the
`push → test → build → GHCR → Argo CD → k3s` chain end to end with a
deliberately trivial application, so the plumbing is known-good before a real
application depends on it.

The phase is finished when a commit to the canary repository produces a new
GHCR digest, a write-back commit pins that digest, Argo CD syncs it unattended,
the running pod reports the new commit SHA over the tailnet — and, separately,
a malformed manifest pushed to `homelab` is rejected by CI before Argo CD ever
sees it.

## 2. Why this phase runs, and what it corrects

Read literally, the roadmap's premise is not met. `apps/` is an empty
`.gitkeep`. Nothing in this cluster runs an image built here, so there is no
subject for an image pipeline. This is the same shape as phase 19's "si
nécessaire" gate, and it gets the same answer for the same reason: the cheap
version is worth building now because the expensive discovery is finding out,
at the moment a real application arrives, that none of the plumbing exists.

**A canary earns its place only if it is honest about being one.** It is not
useful, it is not load-bearing, and the phase must not pretend otherwise. What
it is, is observable: it reports which commit is live, which is precisely the
question the whole chain exists to answer.

But the larger reason the phase is worth more than its features is that it
corrects two things this repository currently states incorrectly.

### 2.1 `main` is auto-deployed and nothing validates it

`environments/homelab/root.yaml` targets `main` with `selfHeal: true`. There is
no `.github/` directory anywhere in this repository. Nothing at all sits
between `git push` and Argo CD applying to the cluster. Every phase from 12
onward has added manifests to a repository with no gate on it.

This is the repository's most acute unmet need, it costs no credential, and it
is not mentioned in the roadmap's phase 20 at all.

### 2.2 The tailnet ACL debt, and an inverted authority claim

Phase 13 deferred ACL automation to phase 20 in writing, twice —
`infrastructure/networking/README.md` and
`docs/superpowers/plans/2026-09-02-tailscale-ingress.md` both say "Until phase
20 wires up `tailscale/gitops-acl-action` in GitHub Actions, this file is a
record of what should be live... It can drift."

That README also says:

> Treat the console as authoritative and this file as the reviewed copy.

**After this phase that sentence is false, and leaving it in place would be
dangerous.** See §7: the tool cannot preserve console edits, so authority must
move to git, and the README must say so.

## 3. Measured starting state

Measured 2026-09-16 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Applications | 10, all Synced/Healthy |
| k3s | v1.36.4+k3s1, containerd 2.3.4, Ubuntu 26.04.1 |
| Local kubectl | v1.37.0, bundling Kustomize v5.8.1 |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21, `vso-config` 22, `postgres` 23, `redis` 23 |
| VSO | `hashicorp/vault-secrets-operator:1.5.1` |
| PVCs | 2 |
| Memory available | ~9Gi of 15Gi — ARK not running |
| `/srv` free | 851G of 938G |
| YAML files | 24 total; **20 manifests, 4 Helm `values.yaml`** |
| `.github/` | **does not exist** |
| `gh` CLI | **not installed** on this host; Docker is |
| Git remotes | one: `git@github.com:Slqzeer/homelab.git` |

### 3.1 Facts verified rather than assumed

Each of these was probed on 2026-09-16 and each changed the design.

**The proposed CI gate passes on the repository as it stands**, and passes
strictly:

    Summary: 35 resources found in 20 files - Valid: 35, Invalid: 0, Errors: 0, Skipped: 0

`Skipped: 0` is the number that matters. It means every CRD in this repository
resolved against a real schema rather than being silently waved through — the
usual failure mode of manifest linting, where the CRDs that most need checking
are the ones quietly ignored.

**All five CRDs this repository uses have schemas in the datreeio catalog**:
`Application`, `VaultAuth`, `VaultConnection`, `VaultStaticSecret`,
`ProxyClass`. All five return HTTP 200.

**The gate bites.** Negative-tested against two deliberately corrupted copies:

| Injected fault | Caught as |
| --- | --- |
| `replicas:` → `replicaz:` in a Deployment | `at '/spec': additional properties 'replicaz' not allowed` |
| `refreshAfter:` → `refreshAfte:` in a `VaultStaticSecret` | `at '/spec': additional properties 'refreshAfte' not allowed` |

Both depend on `-strict`. Without it, an unknown field is accepted and the gate
is decorative.

**Kustomize accepts `newTag` and `digest` together.** Verified on v5.8.1 — it
does not error, and renders:

    image: ghcr.io/slqzeer/beacon:sha-a1b2c3d@sha256:0000...

The tag stays legible in `kubectl get pod`; the digest is what containerd
actually resolves. This is strictly stronger than the root README's existing
requirement to "pin its concrete image tag".

**VSO templates ADD keys, they do not replace them.** The live
`redis-credentials` Secret carries `['password', 'requirepass.conf',
'username']` — the templated key plus both source fields, despite
`excludeRaw: true`. This directly determines D6: without
`excludes: [".*"]`, a `dockerconfigjson` Secret would carry the PAT twice.

**VSO's `destination.type` exists in the live 1.5.1 CRD**, described as "Type
of Kubernetes Secret. Requires Create to be set to true. Defaults to Opaque."
So VSO can mint a `kubernetes.io/dockerconfigjson` Secret directly; no
intermediate controller is needed.

## 4. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Canary is `beacon`: Go static binary on `scratch`, private repo, `/healthz` and `/version` | §8. `/version` returns its own build SHA, making the chain observable rather than inferred |
| D2 | One phase, four workstreams, ordered by credential risk | §5. WS1 needs no credential and guards every later commit in the phase |
| D3 | The ACL workflow ships `action: test` only; `apply` is flipped as the phase's final step, with evidence | §7. The first `apply` is the dangerous one |
| D4 | `yamllint` + `kubeconform -strict`, k8s 1.36.0, datreeio catalog, excluding `values.yaml` | §6, verified in §3.1 |
| D4a | `yamllint` runs against a committed `.yamllint.yaml`, **not** its default profile | §6.2. The default profile is red on a clean tree, in all 24 files |
| D5 | Pin `:sha-xxxxxxx@sha256:...` — both tag and digest | §3.1. Legible *and* immutable |
| D6 | Package private; PAT with `read:packages` only, held in Vault; VSO mints the `dockerconfigjson` with `excludes: [".*"]` | §9. Without the exclude, the PAT ships twice — a third instance of the known `_raw` gap |
| D7 | Write-back uses `GITHUB_TOKEN` | §8.2. GitHub does not retrigger workflows on `GITHUB_TOKEN` pushes: recursion is prevented by the platform, not by a `[skip ci]` marker a later edit could drop |
| D8 | Namespace `apps`, with its own `VaultConnection`; `registry` at sync-wave **23**, `beacon` at **24** | §10 |
| D9 | Ingress `beacon.taildf6cd4.ts.net`, `tailscale.com/proxy-class: homelab`, owned by the **beacon** repo | §8.3 |
| D10 | Every third-party Action pinned to a commit SHA | The same discipline this repository already applies to every container image tag |
| D11 | No self-hosted runner | §12. Keeps GitHub outside the cluster's trust boundary; the cost is a stated gap, not a hidden one |
| D12 | Authority for the tailnet policy moves to git, and the README is rewritten to say so | §7 |

## 5. Architecture

Four workstreams, two credential paths that never touch.

```
WORKSTREAM 1 -- homelab CI                   (no credential, no cluster change)
  PR / push -> yamllint -> kubeconform -strict -> gate

WORKSTREAM 2 -- tailnet ACL                  (1 GitHub secret, no cluster change)
  PR   -> gitops-acl-action action=test
  main -> action=test   <- withheld at `apply` until reconciliation is evidenced,
                           then flipped as the phase's final step

WORKSTREAM 3 -- canary repo `beacon`         (GITHUB_TOKEN only)
  push -> go test -> buildx -> ghcr.io/slqzeer/beacon  (private package)
       -> kustomize edit set image (newTag + digest) -> commit back

WORKSTREAM 4 -- cluster side                 (PAT -> Vault -> VSO)
  human pastes PAT --> Vault KV homelab/ghcr
                          |  policy vso-ghcr-read, role vso-ghcr
                          v
                        VSO --> Secret ghcr-pull
                                type: kubernetes.io/dockerconfigjson
                                excludes: [".*"]
                          v
  Application `registry` wave 23 --> ns `apps` --> Secret ghcr-pull
  Application `beacon`   wave 24 --> ns `apps` --> Deployment imagePullSecrets
                                               --> Ingress beacon.taildf6cd4.ts.net
```

The Tailscale OAuth secret lives only in GitHub and never enters the cluster.
The GHCR PAT lives only in Vault and never enters GitHub. Neither system holds
the other's credential, and that separation is deliberate: a compromise of the
GitHub account cannot pull private images, and a compromise of the cluster
cannot rewrite the tailnet policy.

## 6. Workstream 1 — manifest CI on `homelab`

A single workflow on push and pull request:

1. `yamllint` over every YAML file — catches the parse-level problems
   `kubeconform` never sees because it cannot load the document at all.
2. `kubeconform -strict` over manifests only.

The exact invocation, verified green in §3.1:

```
kubeconform -strict -summary \
  -kubernetes-version 1.36.0 \
  -ignore-filename-pattern 'values\.yaml$' \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  bootstrap environments infrastructure platform
```

### 6.1 Why `values.yaml` is excluded, and what that costs

Helm values files are not Kubernetes manifests. `kubeconform` would reject all
four on sight — they have no `apiVersion` or `kind` — so they must be excluded
for the gate to run at all.

The exclusion is by filename pattern rather than by directory because the
convention in this repository is already perfectly regular: every Helm values
file is named `values.yaml`, and every manifest lives in a `config/` directory,
`environments/`, or is `namespaces.yaml`. A filename rule needs no maintenance
as components are added; a directory list would need editing every phase and
would fail open when someone forgot.

**The cost is real and belongs in the known gaps**: a typo in
`platform/vault/values.yaml` still reaches Argo CD unchecked. Those four files
configure Vault, VSO, the Tailscale operator and Argo CD itself — arguably the
four most consequential files here. The gate covers the many low-risk files and
misses the few high-risk ones. That is worth having anyway, and worth saying
out loud rather than letting the green check mark imply more than it means.

### 6.2 `yamllint` needs a config, because its default profile is red today

Measured on 2026-09-16, `yamllint`'s stock profile fails this repository on a
clean tree, two ways:

| Rule | Violations |
| --- | --- |
| `document-start` (require a leading `---`) | **All 24 files** |
| `line-length` (80 columns) | 3, all in `platform/vault/config/unsealer.yaml` |

The `document-start` result is not a defect in the repository. Every file here
opens with an explanatory comment block before its first key — a convention
that carries a large share of this repository's reasoning and that several
phases have deliberately invested in. The linter should adapt to the house
style, not the reverse.

**A gate that is red on a clean tree gets disabled within a week**, and takes
the useful checks with it when it goes. So a `.yamllint.yaml` is committed
alongside the workflow, relaxing `document-start` and widening `line-length`,
and the phase is not done until the gate is green on an unmodified tree
(§14, item 1). Adopting a linter's defaults without first running them against the
existing tree is how a check becomes decorative.

## 7. Workstream 2 — the tailnet ACL, and why authority must flip

`infrastructure/networking/policy.hujson` is the only file Argo CD cannot
apply. Today it is applied by hand in the admin console.

### 7.1 The drift guard does not work in CI

`tailscale/gitops-acl-action` is a composite action whose final step is
`go run tailscale.com/cmd/gitops-pusher@b4d39e2...`. Reading that source at the
pinned commit:

```go
if cache.PrevETag == "" {
    log.Println("no previous etag found, assuming the latest control etag")
    cache.PrevETag = controlEtag
}
...
if controlEtag == localEtag { /* no update needed */ return nil }

if cache.PrevETag != controlEtag {
    if err := modifiedExternallyError(); err != nil {
        if *failOnManualEdits { return err } else { fmt.Println(err) }
    }
}
if err := applyNewACL(ctx, tailnet, *policyFname, controlEtag); err != nil { ... }
```

The cache file defaults to `./version-cache.json` and is written with a
`defer`. On a GitHub-hosted runner it does not survive the job, so `PrevETag`
is **always** empty on entry. The first branch then assigns it `controlEtag` —
which makes the drift test `cache.PrevETag != controlEtag` structurally
unreachable.

**Two independent reasons the guard cannot stop an overwrite:**

1. The comparison can never be true, for the reason above.
2. Even if it were, `failOnManualEdits` defaults to `false` — a printed
   warning, not a failure — and the composite action exposes **no input** to
   set it. It is unreachable through the action at all.

So `apply` overwrites the live policy with the repository's copy, every time,
with at most a warning that in practice never prints.

### 7.2 What follows from that

The README's "copy the live policy into this file first" instruction stops
being good advice and becomes a **hard prerequisite** to the first `apply`.
And its "Treat the console as authoritative" sentence must be replaced —
after this phase, git is authoritative and a console edit survives only until
the next push that touches the policy file.

This is why D3 withholds `apply`. The stanzas the cluster depends on live in
this file (`tagOwners` for `tag:k8s-operator`/`tag:k8s`, and
`autoApprovers.services`), and the README is explicit that removing either
"breaks all tailnet ingress". The first `apply` is the one that can take the
tailnet down, including Argo CD's own URL. Shipping `test` first turns that
from a hope into a diff that can be read before anything is applied. `test`
also fails on an invalid policy, so it earns its place from day one.

### 7.3 Considered and rejected: committing `version-cache.json`

Committing the cache file and writing the new etag back would make the drift
comparison meaningful. It was rejected because it only half-works: the hard
failure still requires `--fail-on-manual-edits`, which the composite action
cannot pass, so it would mean abandoning the action and invoking
`gitops-pusher` directly. That is more machinery guarding a case the
reconciliation discipline in §7.2 already covers. If console drift becomes a
real recurring problem, direct invocation is the escape hatch — recorded here
so the option is not rediscovered from scratch.

## 8. Workstream 3 — the canary and the write-back

### 8.1 The application

`beacon` is a Go HTTP server of roughly thirty lines with two endpoints:
`/healthz`, and `/version` returning the commit SHA injected at build time via
`-ldflags`. Multi-stage build to `scratch`: single-digit megabytes, no package
manager, no runtime CVE surface, nothing to keep updated. `go test` is a real
test step, so the roadmap's `tests` link in the flow is genuinely exercised
rather than stubbed.

### 8.2 The write-back, and why it does not loop

The build job publishes to GHCR, then rewrites the pinned image in the
repository's own `deploy/kustomization.yaml` and commits.

The obvious worry is a loop: the commit triggers the workflow, which builds,
which commits. It does not happen, and not because of a `[skip ci]` marker.
GitHub does not trigger workflow runs from pushes made with the repository's
`GITHUB_TOKEN`. The protection is a property of the platform rather than of a
string in a commit message that a later edit could drop.

**This has a consequence worth stating**: if someone later swaps
`GITHUB_TOKEN` for a personal access token — a common reaction to a branch
protection error — the loop protection silently disappears. The workflow must
carry a comment saying so at the point of the swap.

### 8.3 Deploy manifests live in the beacon repository

The root README already sets this rule: "An application's own repository owns
its Dockerfile, CI, and deploy manifests. This repository holds only an
Application pointing at it." The Deployment, Service, Ingress and
`kustomization.yaml` therefore live in `beacon`, and `homelab` gains exactly
one file: `environments/homelab/apps/beacon.yaml`.

The Ingress reuses the existing `homelab` ProxyClass by annotation. That is a
cross-repository reference to a cluster-scoped object created at wave 21 by
`ingress-config`, consumed at wave 23 — the ordering is safe, and it is the
same reason the ProxyClass comment gives for not setting
`proxyConfig.defaultProxyClass`.

## 9. Workstream 4 — the cluster side

### 9.1 The PAT is a new class of credential here

Every credential in this cluster so far is generated inside Vault and never
seen by a human. `configure-vault.sh` is explicit about it: "Generated here and
never displayed. It exists only in Vault and in the Secret VSO derives from
it."

A GHCR PAT cannot work that way. It is issued by github.com and must be pasted
*in*. This is the first externally-issued credential in this design, and it
needs a different handling story.

**It cannot be added to `configure-vault.sh`.** Two reasons, either sufficient:

1. A PAT written into the script is a PAT in git.
2. The script is run as `kubectl exec -i vault-0 -- sh < configure-vault.sh`,
   so the pod's stdin *is* the script. A `read` prompt would consume the
   script's own remaining lines rather than waiting for a human — failing in a
   way that looks like a corrupted script rather than a design error.

So the seeding is a documented standalone step: an interactive
`vault kv put homelab/ghcr` run inside the pod at a real terminal. The script
still gains its policy and role blocks, which contain no secret and are
idempotent like every other block.

### 9.2 VSO must be told to drop the source fields

Verified in §3.1: templates add keys rather than replacing them. A
`VaultStaticSecret` that templates `.dockerconfigjson` and does nothing else
produces a Secret carrying the PAT twice — once inside the docker config, once
as the plain source field. That is a third instance of the `_raw` gap the root
README already tracks, created deliberately, in the same phase that documents
why the gap matters.

`transformation.excludes: [".*"]` removes every source field, leaving only the
templated key. `excludeRaw: true` remains for `_raw`.

## 10. Namespace and sync wave

Namespace `apps`, added to `bootstrap/namespaces/namespaces.yaml`, with its own
`VaultConnection` — a `VaultConnection` is namespace-scoped, so the one in
`databases` cannot be reached from here. This mirrors what phase 18 did when it
created `databases`.

**The namespace `apps` and the top-level directory `apps/` are unrelated.** The
directory stays empty: beacon's manifests live in beacon's own repository. The
root README already warns that an Application placed in that directory is
silently ignored with no error; a namespace sharing its name is an obvious trap
for the next reader, so it is named here as a non-relationship rather than left
to be inferred.

The credential and its consumer are **two Applications**, not one:
`registry` (this repository, `platform/registry/config`) and `beacon` (the
beacon repository, `deploy/`). Splitting them keeps homelab-owned
infrastructure out of the application, and means a broken canary cannot take
the credential path down with it.

`registry` takes sync-wave **23**, shared with `postgres` and `redis`. It
depends on the VSO operator (wave 21) and on Vault having been seeded, which
are exactly the preconditions those two share; it depends on neither of them.
The root README states the rule directly: "a component that does not depend on
Postgres (or on anything else at wave 23) belongs at or below 23, not above it
out of habit." Sharing the wave lets all three reconcile in parallel.

`beacon` takes **24** — and this is the case the README's rule explicitly
allows, not the habit it warns against. That warning is about components with
*no* dependency on the wave below. Here there is a real one: beacon's pod
cannot pull its image until `registry` has created the `ghcr-pull` Secret.
Without the ordering the pod would `ImagePullBackOff` and recover on its own,
which is correct but noisy, and indistinguishable at a glance from a genuinely
broken credential. Nothing sits behind wave 24, so an unhealthy canary still
gates nothing.

## 11. New cluster-only state

Two things this phase creates that exist in no repository and that a rebuild
does not recreate. Both belong in the root README's "First install / rebuild"
list, beside `operator-oauth` and `vault-unseal-keys`:

1. **A `repo-beacon` deploy-key Secret in `argocd`.** Argo CD must clone a
   second private repository, and the existing `repo-homelab` credential does
   not grant access to it. Without this the `beacon` Application reports a
   clone failure, which reads as a repository-URL typo.
2. **The GHCR PAT in Vault at `homelab/ghcr`.** §9.1.

## 12. Risks

| Risk | Severity | Handling |
| --- | --- | --- |
| **The PAT expires and image pulls fail silently** | Medium | Running pods are unaffected; only *new* pulls break, so it surfaces at the next rollout or node restart, arbitrarily far from the cause. Needs a written rotation procedure and the expiry date recorded |
| **CI validates schema, not semantics** | Medium | A GitHub-hosted runner cannot reach this cluster (D11), so there is no server-side dry-run. A `VaultAuth` naming a role that does not exist in Vault is schema-perfect and fails at runtime — the exact class of bug the phase-17/18/19 manifest comments keep warning about. The gate narrows the window; it does not close it |
| **The four `values.yaml` files stay unvalidated** | Medium | §6.1. Stated in the known gaps rather than implied away by a green check |
| **First ACL `apply` takes down the tailnet** | High if mishandled | D3 withholds `apply`; §7.2 makes reconciliation a hard prerequisite |
| **Branch protection on `main` breaks the write-back** | Low | Fails as what looks like a permissions error rather than a policy one; §8.2 names it |
| **Swapping `GITHUB_TOKEN` for a PAT reintroduces the build loop** | Low | §8.2; a comment at the point of the swap |
| **One more Tailscale proxy pod** | Low | ~64Mi request / 128Mi limit under the existing ProxyClass. Fine at ~9Gi available, noted because ARK reclaims ~8GB |

## 13. Out of scope

Deliberately not in this phase:

- **Artifactory** — phase 21, and explicitly conditional there.
- **Fixing the two existing `_raw` Secrets** — still the known gap recorded in
  the root README. This phase avoids adding a third (§9.2) but does not fix the
  two, for the same reason phase 19 gave: it is one small cross-cutting change,
  not a component fix.
- **`NetworkPolicy`** — still absent cluster-wide, still waiting on a first
  consumer to tell it what to say.
- **Self-hosted runners** — D11.
- **Image signing and provenance attestation** (cosign, SLSA) — named here so
  that its absence is a decision rather than an oversight. It belongs with a
  real application, not a canary.
- **Renovate or Dependabot** — dependency automation is its own phase.

## 14. Verification

The phase is not complete until each of these has been run and its output
seen:

1. **The gate is green on a clean tree** — *both* linters. `kubeconform`
   reports `Valid: 35, Invalid: 0, Errors: 0, Skipped: 0` in CI, not just
   locally, and `yamllint` passes against the committed `.yamllint.yaml`
   (§6.2). A green `kubeconform` beside a red `yamllint` is a failed gate.
2. **The gate is red on a broken tree** — a deliberately malformed manifest on
   a branch fails the check, and the failure names the field.
3. **The ACL `test` job passes** against the live tailnet, and its log shows
   the control etag and the local etag so the diff is legible before any
   `apply`.
4. **A `beacon` commit produces a new GHCR digest**, visible in the package's
   version list.
5. **The write-back commit exists**, pins `tag@digest`, and **did not trigger
   a second workflow run**.
6. **Argo CD syncs `beacon` unattended** to `Synced`/`Healthy` at wave 23.
7. **`curl https://beacon.taildf6cd4.ts.net/version` from a tailnet device
   returns the new commit SHA** — the whole chain, end to end, in one command.
8. **The pull actually used the credential.** This one needs care, because
   the two obvious ways to test it both lie:

   - Deleting the `ghcr-pull` Secret proves nothing — VSO recreates it within
     `refreshAfter`, so the pull may simply race and succeed.
   - Restarting the pod proves nothing either — containerd already has the
     image cached on the node, so it never contacts GHCR at all.

   The honest test is to remove **both** the cache and the credential
   reference: `crictl rmi` the image on the node, patch `imagePullSecrets`
   off the Deployment, and confirm `ImagePullBackOff` with a 401 in the
   events. Then restore the patch and confirm the pull succeeds. Anything
   less leaves the private package unproven.
9. **`kubectl get secret ghcr-pull -o jsonpath='{.data}'` lists exactly one
   key**, `.dockerconfigjson` — proving §9.2's exclusion worked and the PAT is
   not duplicated.
10. **The ACL `apply` flip** is the last change made, after 3 above, with the
    reconciliation done and evidenced.
