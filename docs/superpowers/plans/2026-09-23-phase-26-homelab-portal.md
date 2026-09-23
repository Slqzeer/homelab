# Phase 26 Homelab Portal and Application Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the reusable personal-application onboarding contract and deploy Homelab Portal as its first conforming production application.

**Architecture:** Keep product packaging in `homelab-portal` and site policy in `homelab`. Add a contract validator, a narrow idempotent Keycloak client reconciler, and namespace/Vault/Argo/Ingress integration. Discover cluster-specific values with named preflight commands and commit only verified immutable identities.

**Tech Stack:** Python 3 `unittest` and PyYAML, Kubernetes 1.36/k3s, Argo CD, Kustomize 5.7.1, Vault Secrets Operator, Keycloak 26.7.4, Tailscale Kubernetes Operator, Go 1.26, Astro, GHCR.

**Spec:** `docs/superpowers/specs/2026-09-23-phase-26-personal-applications-design.md`

## Global Constraints

- `homelab-portal` owns source, CI, image, and generic manifests; `homelab` owns site integration.
- Child Applications live only in `environments/homelab/apps/`; centralized Ingresses live only in `infrastructure/ingress/config/`.
- Portal runs at wave 25 after Keycloak wave 24; its Ingress remains in wave 21.
- Secrets exist only in Vault/VSO projections and every `VaultStaticSecret` sets `excludeRaw: true`.
- Images use a release tag plus digest; `latest`, a zero digest, and floating chart versions are forbidden.
- Portal remains default-deny and grants only selected proxy, Prometheus, DNS, Kubernetes API, and Keycloak flows.
- Publication metadata is added only after target authentication is verified; no current administrative UI is `public`.
- Never print Secret data, session keys, client secrets, tokens, cookies, authorization headers, or restricted URLs.

## Review Focus

- A second client-registration run must create no duplicate and change no unrelated client; Task 2 tests this.
- A release tag whose rendered manifest still has a zero/floating digest must fail promotion; Task 3 tests it.
- Kubernetes API egress must use the cluster's real Service VIP and endpoint under the installed CNI; Tasks 4 and 6 prove it.
- Invalid publication metadata must never leak through non-admin HTML; Tasks 5 and 6 test it.
- Session-key rotation must preserve sessions only during the deliberate current/previous overlap; Task 6 tests both states.

---

### Task 1: Application onboarding contract and validator

**Files:**
- Create: `docs/application-onboarding.md`
- Create: `apps/_template/README.md`
- Create: `scripts/validate_application_onboarding.py`
- Create: `tests/test_application_onboarding.py`
- Modify: `.github/workflows/validate.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: Argo Application YAML, rendered workload YAML, and centralized Ingress YAML.
- Produces: `validate_application(application_path: Path, rendered_paths: list[Path], ingress_path: Path) -> list[str]`; empty means conforming.

- [ ] **Step 1: Write failing validator tests.**

Use `unittest.TemporaryDirectory`. Cover one valid fixture and exact failures for `:latest`, zero digest, missing restricted namespace labels, VSO without `excludeRaw: true`, no default-deny policy, missing ProxyClass, missing portal access, an exposed operations port, and durable state without a restore runbook.

```python
def test_rejects_floating_image(self):
    errors = self.validate(image="ghcr.io/slqzeer/example:latest")
    self.assertIn("deployment image must use an immutable sha256 digest", errors)

def test_rejects_state_without_restore_runbook(self):
    errors = self.validate(pvc=True, backup_annotation=None)
    self.assertIn("durable state requires backup.homelab.io/restore-runbook", errors)
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python3 -m unittest -v tests.test_application_onboarding`

Expected: failure because the validator is absent.

- [ ] **Step 3: Implement pure validation functions.**

Use `yaml.safe_load_all`, return stable sorted errors, and treat parse failures as errors. Require these Application annotations:

```yaml
homelab.io/onboarding-contract: v1
homelab.io/owner: personal-applications
homelab.io/state: stateless
```

For durable state require `backup.homelab.io/restore-runbook` with a checked-in path.

- [ ] **Step 4: Write the human contract and safe scaffold.**

Cover ownership, namespace, wave, Vault/VSO, OIDC, exposure, publication, NetworkPolicy, observability, state, backup, upgrade, rollback, retirement, and acceptance. State that `apps/_template/` is never an Argo source.

- [ ] **Step 5: Add and run CI entrypoints.**

Add `python3 -m unittest -v tests.test_application_onboarding` to the `yamllint` job, then run it locally with `python3 -m py_compile scripts/validate_application_onboarding.py tests/test_application_onboarding.py`.

- [ ] **Step 6: Commit.**

```bash
git add README.md docs/application-onboarding.md apps/_template/README.md scripts/validate_application_onboarding.py tests/test_application_onboarding.py .github/workflows/validate.yaml
git commit -m "test: define application onboarding contract"
```

### Task 2: Idempotent Keycloak client registration and Vault contract

**Files:**
- Create: `platform/keycloak/config/client-registration.yaml`
- Create: `platform/keycloak/config/networkpolicy.yaml`
- Create: `platform/keycloak/test_client_registration.py`
- Modify: `platform/keycloak/config/vault-secrets.yaml`
- Modify: `platform/vault/configure-vault.sh`
- Modify: `platform/keycloak/README.md`
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: Secrets `keycloak-admin`, `keycloak-portal-client`, and later `keycloak-nextcloud-client` in `keycloak`.
- Produces: client `homelab-portal` with exact redirect/logout URIs and groups claim; repeat runs converge.

- [ ] **Step 1: Write failing client-definition tests.**

Assert client ID, confidential authentication, standard flow enabled, implicit/direct grants disabled, callback `https://portal.taildf6cd4.ts.net/auth/callback`, origin `https://portal.taildf6cd4.ts.net`, groups scope, and preservation of an unrelated client.

Run: `python3 platform/keycloak/test_client_registration.py -v`

Expected: failure because the manifest is absent.

- [ ] **Step 2: Extend Vault configuration.**

Idempotently generate `homelab/keycloak-portal` key `clientSecret` and `homelab/portal` keys `oidc-client-secret` and `session-current-key` through private `mktemp` files and `key=@file`. Extend `vso-keycloak-read`; create `vso-portal-read` bound only to ServiceAccount `homelab-portal` in namespace `portal`.

- [ ] **Step 3: Project the registration secret and implement the hook.**

Add `keycloak-portal-client` to the Keycloak VaultAuth. Add a ConfigMap script and Argo `PostSync` Job using `quay.io/keycloak/keycloak:26.7.4`. A digest-pinned `ghcr.io/jqlang/jq` init container copies its verified static binary into a shared tools volume. The script authenticates with `kcadm.sh`, rejects duplicate client IDs, creates or updates only allowlisted clients, sends JSON on stdin, and compares a secret-redacted canonical result. Build secret-bearing JSON with `/tools/jq -n --arg`; never put secret values in argv or logs.

```sh
client_uuid=$($KCADM get clients -r homelab -q clientId=homelab-portal --fields id,clientId | jq -er 'if length == 0 then empty elif length == 1 then .[0].id else error("duplicate client") end' 2>/dev/null || true)
```

- [ ] **Step 4: Test hook security and idempotency.**

Assert deadline, resource limits, `restartPolicy: Never`, pinned tool image, no secret literal, exact client allowlist, no realm-wide mutation, and canonical comparison. Add the test to CI.

- [ ] **Step 5: Close Keycloak ingress before adding consumers.**

Add namespace default-deny ingress plus separate allows: selected Tailscale proxy to 8080, monitoring scraper to 9000, registration Job to 8080, and portal/Nextcloud namespaces to 8080. Tests reject namespace-wide or all-port grants.

- [ ] **Step 6: Run tests and commit.**

```bash
python3 platform/keycloak/test_client_registration.py -v
git add platform/keycloak platform/vault/configure-vault.sh .github/workflows/validate.yaml
git commit -m "feat: reconcile personal application OIDC clients"
```

### Task 3: Produce and approve an immutable portal release

**Files (portal repository):**
- Verify: `/srv/projects/homelab/homelab-portal/`
- Modify: `deploy/overlays/homelab/image-patch.yaml`
- Create: `deploy/overlays/homelab/namespace-delete-patch.yaml`

**Interfaces:**
- Consumes: clean portal main branch and protected release workflow.
- Produces: reviewed tag, commit, GHCR digest, signature, provenance, SBOM, scan reports, and immutable rendered overlay.

- [ ] **Step 1: Run the complete portal gate.**

```bash
cd /srv/projects/homelab/homelab-portal
git status --short
make test
bash scripts/verify.sh
go test ./tests/manifest ./tests/integration
```

Expected: clean worktree and all tests pass.

- [ ] **Step 2: Prove the template cannot promote and remove duplicate namespace ownership.**

Render with `kubectl kustomize deploy/overlays/homelab`; confirm the zero digest is rejected by release/manifest tests. Add a production-overlay deletion patch for `Namespace/portal`, because `bootstrap/namespaces/namespaces.yaml` is its sole owner. Add a manifest test requiring no Namespace in the production render while the reusable base retains one.

- [ ] **Step 3: Release through the protected workflow.**

Select the next semantic patch tag from `git tag --sort=-version:refname`, push the reviewed tag, and wait for test, release, and production approval. Take image name and digest only from workflow outputs.

- [ ] **Step 4: Verify and pin the artifact.**

Verify signature, provenance, SBOM subject, both platform manifests, and scan result against the workflow digest. Patch the overlay with the exact GHCR tag-and-digest reference emitted by that workflow, include `namespace-delete-patch.yaml`, rerun tests, commit `release: pin portal production image`, and create a replacement patch release if the selected revision changed.

### Task 4: Register the portal with GitOps and Vault

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Create: `apps/portal/config/vault-secrets.yaml`
- Create: `apps/portal/config/kustomization.yaml`
- Create: `environments/homelab/apps/homelab-portal.yaml`
- Modify: `tests/test_application_onboarding.py`

**Interfaces:**
- Consumes: Task 3 revision/digest and Task 2 Vault paths.
- Produces: restricted `portal` namespace, Secret `homelab-portal-secrets`, and wave-25 multi-source Application.

- [ ] **Step 1: Add failing portal conformance assertions.**

Require restricted/v1.36 namespace labels, wave `25`, immutable portal revision, image digest, and VSO keys `oidc-client-secret`, `session-current-key`, optional `session-previous-key` only.

- [ ] **Step 2: Add namespace and VSO resources.**

Use ServiceAccount `homelab-portal`, VaultAuth role `vso-portal`, path `portal`, refresh `60s`, `excludeRaw: true`, and rollout restart target Deployment `homelab-portal`.

- [ ] **Step 3: Add the multi-source Application.**

Source the immutable portal revision at `deploy/overlays/homelab` plus this repository's `apps/portal/config`. Add onboarding annotations, `homelab.io/state: stateless`, wave 25, prune/self-heal, and ServerSideApply. Do not use `CreateNamespace=true`.

- [ ] **Step 4: Replace all site templates with observed values.**

```bash
kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}{"\n"}'
kubectl get endpoints kubernetes -n default -o jsonpath='{.subsets[*].addresses[*].ip}{"\n"}'
kubectl -n monitoring get pods --show-labels
kubectl -n tailscale get pods --show-labels
```

Set portal base URL and issuer. The render must contain none of `192.0.2.1`, `198.51.100.1`, `registry.example`, or `example.ts.net`.

- [ ] **Step 5: Test and commit.**

```bash
python3 -m unittest -v tests.test_application_onboarding
kubectl kustomize apps/portal/config
git add bootstrap/namespaces/namespaces.yaml apps/portal environments/homelab/apps/homelab-portal.yaml tests/test_application_onboarding.py
git commit -m "feat: register homelab portal application"
```

### Task 5: Expose the portal and publish approved targets

**Files:**
- Create: `infrastructure/ingress/config/portal-ingress.yaml`
- Modify: approved `infrastructure/ingress/config/*-ingress.yaml`
- Modify: `tests/test_application_onboarding.py`
- Modify: `infrastructure/ingress/README.md`

**Interfaces:**
- Consumes: portal Service named port `public` and explicit target-owner decisions.
- Produces: `https://portal.taildf6cd4.ts.net` and an intentional catalogue that excludes itself.

- [ ] **Step 1: Write failing Ingress tests.**

Require class `tailscale`, ProxyClass `homelab`, short TLS host `portal`, backend named port `public`, and reject `operations`. For published targets require valid `enabled`, `name`, `description`, `category`, `icon`, `access`, `order`, and conditional `groups`.

- [ ] **Step 2: Add the unlisted portal Ingress.**

Create namespace/name `portal/homelab-portal`, route `/` to `homelab-portal:public`, and add no portal publication annotation.

- [ ] **Step 3: Verify and publish initial targets.**

In private browser sessions verify Vault, Argo CD, Grafana, Nexus, and Keycloak authentication. Add only `admin` or exact group metadata; no administrative target is `public`.

- [ ] **Step 4: Test and commit.**

```bash
python3 -m unittest -v tests.test_application_onboarding
git add infrastructure/ingress tests/test_application_onboarding.py
git commit -m "feat: publish approved portal catalogue entries"
```

### Task 6: Deploy, prove boundaries, and document portal operations

**Files:**
- Create: `apps/portal/README.md`
- Create: `docs/runbooks/portal-recovery.md`
- Modify: `README.md`
- Modify: `docs/workstation-plan.md`

**Interfaces:**
- Consumes: Tasks 1–5 and portal `docs/runbooks/acceptance.md`.
- Produces: running measured portal, acceptance evidence, and recovery/rotation procedures.

- [ ] **Step 1: Record preflight evidence.**

Record `free -h`, `df -h /srv /backups`, `kubectl top node`, all Argo states, proxy resources, and fresh Keycloak/PostgreSQL backup names. Stop on any pre-existing unhealthy Application.

- [ ] **Step 2: Apply Vault and reconcile dependencies.**

Run the documented stdin Vault ceremony; inspect only VSO status columns. Reconcile the Keycloak client hook, then portal. Never retrieve Secret values as evidence.

- [ ] **Step 3: Run cluster acceptance.**

Prove anonymous/authenticated/group/admin views, invalid metadata, stale/expired cache, readiness, listener separation, forbidden Secret reads, forbidden Ingress writes, and denied network paths. Verify Argo `Synced/Healthy` and exactly one Ingress hostname.

- [ ] **Step 4: Prove rotation and degraded behavior.**

Rotate session keys through current/previous overlap, verify old sessions only during overlap, remove the previous key after expiry, and prove Keycloak outage blocks new login without leaking restricted entries.

- [ ] **Step 5: Measure, document, and run the repository gate.**

Record idle and peak portal/proxy CPU and memory. Update operational docs and the Phase 26 subsection with measured portal facts. Run:

```bash
python3 -m unittest discover -v
yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure observability platform apps .github
git diff --check
```

Run the pinned kubeconform commands from `.github/workflows/validate.yaml`; require `Invalid: 0`, `Errors: 0`, `Skipped: 0` and all Argo Applications healthy.

- [ ] **Step 6: Commit.**

```bash
git add apps/portal/README.md docs/runbooks/portal-recovery.md README.md docs/workstation-plan.md
git commit -m "docs: record portal production acceptance"
```
