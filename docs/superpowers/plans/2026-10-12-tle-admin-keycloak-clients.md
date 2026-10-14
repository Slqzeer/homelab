# TLE Admin Keycloak Clients Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issue #1 by automatically registering `tle-admin-dev` and
`tle-admin-prod` and reconciling their client roles from TLE's pinned manifest.

**Architecture:** Keep client registration, URI allowlists, and credential
projection in the existing Keycloak PostSync Job. Generate a separate ConfigMap
whose mounted script reconciles exact role and composite sets on only the two
TLE clients after their registration. Vault creates one client secret per
environment, and VSO projects both only into Keycloak.

**Tech Stack:** Kubernetes manifests, Argo CD hooks, Vault KV v2 and Vault
Secrets Operator, Keycloak 26 `kcadm.sh`, POSIX shell, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-12-tle-admin-keycloak-clients-design.md`

## Global Constraints

- Client IDs are exactly `tle-admin-dev` and `tle-admin-prod`.
- Their origins are respectively `https://tle-dev-operator.taildf6cd4.ts.net`
  and `https://tle-prod-operator.taildf6cd4.ts.net`; permit only
  `/auth/callback` and post-logout `/`.
- Both clients use the vendored `apps/tle-dev/deploy-info/role-manifest.json`.
- Generate and preserve distinct client secrets at `homelab/tle-admin-dev-client`
  and `homelab/tle-admin-prod-client`, each under `clientSecret`.
- Never print a secret or send one as a process argument.
- Only roles and role-composite members on the two TLE admin clients may be
  removed. Never mutate realm roles, users, groups, realm settings, default
  realm roles, or any unrelated client.
- Group-to-composite assignment remains a documented manual console operation.
- Production client registration must not create a production ingress.

## Review Focus

- A stale role or composite member on either TLE client is removed, while an
  identical stale role on an unrelated client is untouched (Task 1).
- A duplicate TLE client ID or an absent registered client stops reconciliation
  before role mutation (Task 1).
- A generated script remains tied to the vendored manifest, including its
  version/count header, after manifest regeneration (Task 1).
- Each client secret is independently generated and projected; a missing or
  empty Secret must fail the hook without leaking either value (Task 2).
- Exact URI assertions reject a swapped dev/prod origin, wildcard, alternate
  callback, or logout path (Task 2).

---

### Task 1: Generate and mount exact TLE role reconciliation

**Files:**
- Modify: `platform/keycloak/tle-operator/generate.py`
- Delete: `platform/keycloak/tle-operator/reconcile-roles.sh`
- Create: `platform/keycloak/config/tle-admin-role-reconciliation.yaml`
- Modify: `platform/keycloak/config/client-registration.yaml`
- Modify: `platform/keycloak/test_client_registration.py`

**Interfaces:**
- Consumes: `apps/tle-dev/deploy-info/role-manifest.json`.
- Produces: a generated `ConfigMap/keycloak-tle-admin-role-reconciliation`
  containing executable key `reconcile-tle-admin-roles.sh`.
- Consumes: `TLE_ADMIN_CLIENT_IDS="tle-admin-dev tle-admin-prod"`,
  `KC_ADMIN_USERNAME`, and `KC_CLI_PASSWORD` from the registration Job.
- Produces: exit status 0 only when both clients' roles and composites exactly
  match the manifest; otherwise a non-zero status without touching any global
  Keycloak resource.

- [ ] **Step 1: Add failing role-reconciliation tests to `platform/keycloak/test_client_registration.py`**

  Extend `FAKE_KCADM` with in-memory handlers for the Keycloak client-role and
  client-role-composite endpoints used by the new script. Add tests that run
  the generated script against both TLE clients and assert that it:

  - creates missing capability/bundle roles and composite members;
  - deletes stale role and stale composite member from each TLE client;
  - leaves an unrelated client's roles and composite members unchanged;
  - never invokes a realm-role, user, group, realm, or default-realm-role
    endpoint; and
  - fails before writes for a duplicate or missing TLE client ID.

- [ ] **Step 2: Run the role-reconciliation tests to verify they fail**

  Run: `python3 platform/keycloak/test_client_registration.py -v`

  Expected: FAIL because the generated ConfigMap/script and fake role handlers
  do not exist yet.

- [ ] **Step 3: Change `platform/keycloak/tle-operator/generate.py` to emit the mounted ConfigMap**

  Replace its `tle-operator` manual-script contract with a generated YAML
  `ConfigMap` named `keycloak-tle-admin-role-reconciliation`. Its data key is
  `reconcile-tle-admin-roles.sh`; the script reads `TLE_ADMIN_CLIENT_IDS`,
  loops only over those two exact IDs, and uses the manifest's capability and
  composite sets. Make client roles and composite members exact using only
  `clients/<uuid>/roles` and `clients/<uuid>/roles/<bundle>/composites`
  endpoints. Do not emit any realm-role, group, user, or realm-setting logic.

- [ ] **Step 4: Generate `platform/keycloak/config/tle-admin-role-reconciliation.yaml`**

  Run: `python3 platform/keycloak/tle-operator/generate.py apps/tle-dev/deploy-info/role-manifest.json platform/keycloak/config/tle-admin-role-reconciliation.yaml`

  Confirm its header records the source manifest version and counts, and that
  it contains no `tle-operator` client ID or realm-role mutation command.

- [ ] **Step 5: Mount and invoke the generated script from the registration Job**

  In `platform/keycloak/config/client-registration.yaml`, add a read-only
  ConfigMap volume for `keycloak-tle-admin-role-reconciliation`, mount it at
  `/tle-role-reconciler`, expose `TLE_ADMIN_CLIENT_IDS` as the literal ordered
  pair, and invoke `/tle-role-reconciler/reconcile-tle-admin-roles.sh` only
  after both clients have been registered. Keep the existing Job account,
  security context, and PostSync semantics.

- [ ] **Step 6: Run the role-reconciliation tests to verify they pass**

  Run: `python3 platform/keycloak/test_client_registration.py -v`

  Expected: PASS, including exact role/composite convergence and no global
  Keycloak mutations.

- [ ] **Step 7: Commit the generated reconciler**

  ```bash
  git add platform/keycloak/tle-operator/generate.py \
    platform/keycloak/tle-operator/reconcile-roles.sh \
    platform/keycloak/config/tle-admin-role-reconciliation.yaml \
    platform/keycloak/config/client-registration.yaml \
    platform/keycloak/test_client_registration.py
  git commit -m "feat(keycloak): reconcile TLE admin client roles"
  ```

### Task 2: Provision client secrets and register exact TLE clients

**Files:**
- Modify: `platform/vault/configure-vault.sh`
- Modify: `platform/keycloak/config/vault-secrets.yaml`
- Modify: `platform/keycloak/config/client-registration.yaml`
- Modify: `platform/keycloak/test_client_registration.py`

**Interfaces:**
- Produces: Vault `clientSecret` fields at
  `homelab/tle-admin-dev-client` and `homelab/tle-admin-prod-client`.
- Produces: `Secret/keycloak-tle-admin-dev-client` and
  `Secret/keycloak-tle-admin-prod-client` in namespace `keycloak`.
- Consumes: Job environment variables `TLE_ADMIN_DEV_CLIENT_SECRET` and
  `TLE_ADMIN_PROD_CLIENT_SECRET`.

- [ ] **Step 1: Add failing TLE client/Vault contract tests**

  Extend `run_hook` to supply independent dev and prod canary secrets. Add
  assertions that the hook creates and converges both clients with the exact
  origins, `/auth/callback` redirect URIs, and `/` post-logout URIs; each
  receives only its own canary secret. Extend manifest tests to require both
  Job `secretKeyRef`s and VSO projections. Extend Vault tests to require two
  one-key paths, no wildcard policy grant, and no secret in argv or output.

- [ ] **Step 2: Run the TLE client/Vault tests to verify they fail**

  Run: `python3 platform/keycloak/test_client_registration.py -v`

  Expected: FAIL because neither secret path, projection, Job environment
  variable, nor TLE client allowlist entry exists.

- [ ] **Step 3: Add idempotent Vault seeding and least-privilege reads**

  In `platform/vault/configure-vault.sh`, add a focused seed block that creates
  a random alphanumeric 48-character `clientSecret` at each TLE path only when
  absent, passes values with `clientSecret=@file`, and removes its private
  temporary files. Add exactly the two `homelab/data/tle-admin-*-client` paths
  to `vso-keycloak-read`; do not broaden any other Vault policy or role.

- [ ] **Step 4: Add one VSO projection per client**

  In `platform/keycloak/config/vault-secrets.yaml`, add two
  `VaultStaticSecret`s using `vaultAuthRef: keycloak`, `kv-v2`,
  `refreshAfter: 300s`, `excludeRaw: true`, and destination names
  `keycloak-tle-admin-dev-client` and `keycloak-tle-admin-prod-client`.

- [ ] **Step 5: Add both explicit allowlist entries to the registration hook**

  In `platform/keycloak/config/client-registration.yaml`, make both TLE secret
  environment variables required and call `reconcile_client` for:

  - `tle-admin-dev`, display name `The Last Exam Admin (dev)`, origin
    `https://tle-dev-operator.taildf6cd4.ts.net`, callback `/auth/callback`,
    logout `/`;
  - `tle-admin-prod`, display name `The Last Exam Admin (prod)`, origin
    `https://tle-prod-operator.taildf6cd4.ts.net`, callback `/auth/callback`,
    logout `/`.

  Preserve existing portal and optional-Penpot behavior. Ensure the TLE role
  reconciler is called only once both required TLE secrets and both client
  records have converged.

- [ ] **Step 6: Run the TLE client/Vault tests to verify they pass**

  Run: `python3 platform/keycloak/test_client_registration.py -v`

  Expected: PASS, proving exact URI/client-secret isolation, idempotency,
  narrow Vault access, and no secret leak.

- [ ] **Step 7: Commit the credential and registration wiring**

  ```bash
  git add platform/vault/configure-vault.sh \
    platform/keycloak/config/vault-secrets.yaml \
    platform/keycloak/config/client-registration.yaml \
    platform/keycloak/test_client_registration.py
  git commit -m "feat(keycloak): register TLE admin clients"
  ```

### Task 3: Document regeneration, administration, and verification

**Files:**
- Modify: `platform/keycloak/README.md`
- Modify: `platform/keycloak/test_client_registration.py`
- Modify: `docs/runbooks/tle-dev-recovery.md`

**Interfaces:**
- Consumes: the generated ConfigMap command from Task 1 and the Vault ceremony
  from Task 2.
- Produces: an operator procedure that regenerates the checked-in ConfigMap
  after vendoring a new role manifest, then syncs Keycloak; assigning a group
  to `support`, `operator`, or `admin` remains a Keycloak-console operation.

- [ ] **Step 1: Add a failing documentation-contract test**

  Add a focused test requiring the README to name both `tle-admin-*` clients,
  the generated ConfigMap regeneration command, manifest source, and the
  manual group-to-composite assignment boundary. Require the recovery runbook
  to list automatic role reconciliation as part of release vendoring.

- [ ] **Step 2: Run the documentation-contract test to verify it fails**

  Run: `python3 platform/keycloak/test_client_registration.py -v`

  Expected: FAIL because documentation still describes manual
  `tle-operator` reconciliation.

- [ ] **Step 3: Replace the manual `tle-operator` documentation**

  Update `platform/keycloak/README.md` to describe both automatically
  registered clients, their role-manifest regeneration command, exact safety
  boundary, and the remaining manual group assignment. Update
  `docs/runbooks/tle-dev-recovery.md` to regenerate the ConfigMap after
  vendoring deployment information and to validate the PostSync Job.

- [ ] **Step 4: Run documentation and local Keycloak verification**

  Run: `python3 platform/keycloak/test_client_registration.py -v && yamllint --strict -c .yamllint.yaml platform/keycloak platform/vault apps/tle-dev`

  Expected: all tests and linting pass. The repository's `kubeconform`
  workflow validates raw Keycloak manifests; this directory intentionally has
  no `kustomization.yaml` and must not be rendered with `kustomize build`.

- [ ] **Step 5: Commit documentation and validation**

  ```bash
  git add platform/keycloak/README.md docs/runbooks/tle-dev-recovery.md \
    platform/keycloak/test_client_registration.py
  git commit -m "docs(keycloak): document TLE admin client reconciliation"
  ```

## Final Verification

- [ ] Run `python3 platform/keycloak/test_client_registration.py -v`.
- [ ] Run `yamllint --strict -c .yamllint.yaml apps bootstrap environments infrastructure observability platform .github`.
- [ ] Confirm GitHub Actions' `kubeconform` **Validate manifests** job passes;
  it schema-validates raw manifests below `platform/keycloak/config`.
- [ ] In the cluster after Vault ceremony and Argo sync, confirm both
  `VaultStaticSecret`s are `Synced=True`, the `keycloak-client-registration`
  Job succeeds, each client has its exact URI values, and a manifest-drift
  rehearsal removes only a deliberately added TLE-client role.
