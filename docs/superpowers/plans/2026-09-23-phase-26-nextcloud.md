# Phase 26 Production Nextcloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a fresh production Nextcloud instance that proves the Phase 26 onboarding contract for a stateful third-party application with a 150 GB operational file ceiling.

**Architecture:** Build a minimal immutable Nextcloud 34.0.4 derivative containing `user_oidc` 8.11.0, deploy pinned chart 9.2.5 with external PostgreSQL and a dedicated no-eviction Redis, and keep site policy in `homelab`. Separate application/config and user-data PVCs, restrict all network paths, and prove a maintenance-mode backup by restoring it into an isolated disposable instance.

**Tech Stack:** Nextcloud 34.0.4-apache, user_oidc 8.11.0, Nextcloud Helm chart 9.2.5, Kubernetes 1.36/k3s, PostgreSQL 18.6, Redis 8, Keycloak 26.7.4, Vault/VSO, Prometheus Operator, Tailscale, GHCR.

**Spec:** `docs/superpowers/specs/2026-09-23-phase-26-personal-applications-design.md`

## Global Constraints

- This is a brand-new instance; no user or file migration is performed.
- Core bundled Nextcloud only: no office suite, antivirus, object storage, Imaginary, or external storage.
- Pin chart `9.2.5`, application `34.0.4-apache`, `user_oidc` `8.11.0`, and every runtime image by digest before promotion.
- Disable SQLite, bundled PostgreSQL/MariaDB/Redis, chart Ingress, autoscaling, Collabora, and Imaginary.
- Reuse existing PostgreSQL through role/database `nextcloud`; never reuse the existing `allkeys-lru` Redis for locks.
- Use one replica, `Recreate`, a retained 5 Gi application/config PVC, and a retained 150 Gi user-data PVC.
- Treat 150 GB as an operational boundary, not an enforced local-path quota; warn at 120 Gi and alert at 135 Gi.
- Nextcloud and its CronJob get no arbitrary Internet egress.
- Manual backup/restore is required now; scheduled execution remains Phase 28.
- A schema-changing rollback restores image, database, configuration, apps, themes, and files as one set.

## Review Focus

- Missing/delayed VSO Secrets must not cause SQLite or default credentials; Tasks 2 and 4 test failure.
- Redis must use `noeviction`, authentication, and no PVC; Task 3 proves effective runtime configuration.
- RWO volumes must not deadlock the five-minute CronJob against the app pod; Tasks 4 and 8 test scheduling.
- OIDC identity must remain stable across repeated login and group changes without locking out local recovery; Tasks 5 and 8 test it.
- Partial database/file backups must never receive a final name; Tasks 7 and 8 inject both failures.

---

### Task 1: Build the immutable Nextcloud image

**Files (new sibling repository `/srv/projects/homelab/homelab-nextcloud`):**
- Create: `Dockerfile`
- Create: `versions.env`
- Create: `scripts/fetch-user-oidc.sh`
- Create: `scripts/verify-image.sh`
- Create: `.github/workflows/test.yaml`
- Create: `.github/workflows/release.yaml`
- Create: `README.md`

**Interfaces:**
- Consumes: official `nextcloud:34.0.4-apache` manifest digest and official `user_oidc` 8.11.0 archive/checksum.
- Produces: a signed multi-architecture GHCR tag-and-digest reference emitted by the protected release workflow, with `user_oidc` preinstalled.

- [ ] **Step 1: Write failing image verification.**

Assert exact Nextcloud version, `user_oidc` version from `appinfo/info.xml`, no archive/cache residue, and both `linux/amd64` and `linux/arm64`.

Run: `bash scripts/verify-image.sh local/homelab-nextcloud:test`

Expected: failure because the image is absent.

- [ ] **Step 2: Resolve immutable inputs.**

Use `docker buildx imagetools inspect nextcloud:34.0.4-apache --raw` to record the upstream multi-arch digest. Download the official 8.11.0 archive, verify tag/provenance once, calculate SHA-256, and store versions, URL, and checksum in `versions.env`.

- [ ] **Step 3: Implement checksum-verified assembly.**

The fetch script uses `curl --fail --location --proto '=https'`, `sha256sum -c`, validates one `user_oidc/` archive root and no traversal, then extracts it. The Dockerfile starts from the recorded digest, copies it with `www-data` ownership, and adds no runtime package.

- [ ] **Step 4: Add pinned CI/release workflows.**

Mirror the portal boundary: tests; multi-architecture build; SBOM; Trivy scan; cosign signature; provenance; protected production promotion. Pin actions by full commit.

- [ ] **Step 5: Build, test, release, and commit.**

```bash
docker build -t local/homelab-nextcloud:test .
bash scripts/verify-image.sh local/homelab-nextcloud:test
git add .
git commit -m "feat: build reproducible Nextcloud OIDC image"
```

Tag the reviewed release and take the deployable digest only from workflow output.

### Task 2: Add namespace, Vault identities, and projections

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Modify: `platform/vault/configure-vault.sh`
- Modify: `platform/keycloak/config/vault-secrets.yaml`
- Create: `apps/nextcloud/config/vault-secrets.yaml`
- Create: `apps/nextcloud/test_nextcloud_manifests.py`
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: onboarding validator from portal Plan Task 1.
- Produces: restricted namespace `nextcloud`; named Secrets for app admin, database, Redis, OIDC, and metrics.

- [ ] **Step 1: Write failing secret-boundary tests.**

Assert namespace-local VaultAuth, `excludeRaw: true`, 60s refresh, explicit destinations, no literal `data`/`stringData`, and no cross-namespace Secret consumption.

- [ ] **Step 2: Add idempotent Vault seeds and roles.**

Generate separate alphanumeric credentials through private files for local admin, database, Redis, OIDC, and metrics. Define `vso-nextcloud-read`, `vso-nextcloud-db-read`, and extend `vso-keycloak-read` only for its client-secret copy. Bind each to one ServiceAccount/namespace pair.

- [ ] **Step 3: Add namespace and projections.**

Label `nextcloud` restricted/v1.36. Create application VaultConnection/Auth and separate database-job VaultAuth in `databases`. Map only exact keys and configure restart targets for start-time consumers.

- [ ] **Step 4: Test and commit.**

```bash
python3 apps/nextcloud/test_nextcloud_manifests.py -v
git add bootstrap/namespaces/namespaces.yaml platform/vault/configure-vault.sh platform/keycloak/config/vault-secrets.yaml apps/nextcloud .github/workflows/validate.yaml
git commit -m "feat: provision Nextcloud secret boundaries"
```

### Task 3: Create the PostgreSQL database and dedicated Redis

**Files:**
- Create: `apps/nextcloud/config/database-job.yaml`
- Create: `apps/nextcloud/config/redis.yaml`
- Modify: `platform/databases/postgres/config/networkpolicy.yaml`
- Modify: `apps/nextcloud/test_nextcloud_manifests.py`

**Interfaces:**
- Consumes: `postgres-credentials`, `nextcloud-db`, and `nextcloud-redis`.
- Produces: role/database `nextcloud`; Service `nextcloud-redis:6379`; authenticated ephemeral Redis with `maxmemory 256mb` and `noeviction`.

- [ ] **Step 1: Write failing database/cache tests.**

Require an Argo Sync hook with deadline, `job: postgres-client`, guarded creation, unconditional stdin password rotation, and `ON_ERROR_STOP=1`. Require Redis with no PVC/persistence, authenticated probes, `noeviction`, explicit limits, and ClusterIP only.

- [ ] **Step 2: Implement the database hook.**

Use `postgres:18.6-alpine`, existing PostgreSQL Service, role/database `nextcloud`, and the `\getenv` stdin technique from Keycloak's database Job. Never put password in `psql -v`, SQL argv, or logs.

- [ ] **Step 3: Implement dedicated Redis.**

Use a pinned Redis 8 alpine digest, one Deployment/Service, emptyDir runtime state, AOF/RDB disabled, `requirepass` from a Secret file, `maxmemory 256mb`, and `maxmemory-policy noeviction`. Set the memory limit above the data bound.

- [ ] **Step 4: Open PostgreSQL narrowly.**

Add namespace `nextcloud` to `postgres-clients` and retain the local `job: postgres-client` selector. Do not allow all namespaces.

- [ ] **Step 5: Test and commit.**

```bash
python3 apps/nextcloud/test_nextcloud_manifests.py -v
git add apps/nextcloud/config platform/databases/postgres/config/networkpolicy.yaml
git commit -m "feat: add Nextcloud database and locking cache"
```

### Task 4: Render the pinned workload and persistence

**Files:**
- Create: `apps/nextcloud/values.yaml`
- Create: `apps/nextcloud/config/persistence.yaml`
- Create: `apps/nextcloud/config/networkpolicy.yaml`
- Create: `apps/nextcloud/config/kustomization.yaml`
- Create: `environments/homelab/apps/nextcloud.yaml`
- Modify: `apps/nextcloud/test_nextcloud_manifests.py`
- Modify: `scripts/validate_application_onboarding.py`

**Interfaces:**
- Consumes: chart 9.2.5, Task 1 digest, Task 2 Secrets, Task 3 database/Redis.
- Produces: one Recreate app pod, five-minute non-root CronJob, retained 5Gi/150Gi claims, wave-25 Application.

- [ ] **Step 1: Write failing rendered-chart tests.**

Render exact chart/version. Assert one replica, Recreate, digest, external DB/Redis, probes, resources, CronJob mode/schedule, and existing claims. Assert no SQLite, bundled DB/cache, chart Ingress, HPA, Collabora, or Imaginary.

- [ ] **Step 2: Add retained PVCs.**

Create `nextcloud-app` 5Gi and `nextcloud-data` 150Gi, RWO `local-path`, Helm keep policy, durable-state annotation, and recovery-runbook annotation. Pass them as existing claims.

- [ ] **Step 3: Write production values.**

Set trusted host/proxy and HTTPS overwrite, upload/timeouts, external credential Secrets, APCu, Redis cache/locking, maintenance window, exact image, tolerant startup probe, and non-root CronJob with same-node affinity for RWO volumes.

- [ ] **Step 4: Add default-deny and allowlists.**

Separate proxy-to-HTTP, Prometheus-to-metrics, DNS, PostgreSQL, Redis, and Keycloak rules. Never combine proxy/monitoring peers and add no Internet CIDR.

- [ ] **Step 5: Add the multi-source Argo Application.**

Use chart repository, exact `9.2.5`, `$values/apps/nextcloud/values.yaml`, and this repo's config path. Add wave 25, onboarding metadata, durable state/runbook, prune/self-heal, ServerSideApply, and no `CreateNamespace`.

- [ ] **Step 6: Render, test, and commit.**

```bash
helm template nextcloud nextcloud/nextcloud --version 9.2.5 -f apps/nextcloud/values.yaml > /tmp/nextcloud-rendered.yaml
python3 apps/nextcloud/test_nextcloud_manifests.py -v
python3 -m unittest -v tests.test_application_onboarding
git add apps/nextcloud environments/homelab/apps/nextcloud.yaml scripts/validate_application_onboarding.py
git commit -m "feat: define production Nextcloud workload"
```

### Task 5: Reconcile OIDC and expose Nextcloud

**Files:**
- Modify: `platform/keycloak/config/client-registration.yaml`
- Modify: `platform/keycloak/test_client_registration.py`
- Create: `apps/nextcloud/config/oidc-bootstrap.yaml`
- Create: `infrastructure/ingress/config/nextcloud-ingress.yaml`
- Modify: `apps/nextcloud/test_nextcloud_manifests.py`

**Interfaces:**
- Consumes: both copies of the Nextcloud client secret and immutable `user_oidc`.
- Produces: Keycloak client `nextcloud`, provider identifier `keycloak`, and group-restricted portal item.

- [ ] **Step 1: Write failing exact-client tests.**

Require redirect `https://nextcloud.taildf6cd4.ts.net/apps/user_oidc/code`, logout return `https://nextcloud.taildf6cd4.ts.net/`, back-channel URL ending `/backchannel-logout/keycloak`, PKCE, scopes `openid profile email groups`, and no wildcard.

- [ ] **Step 2: Extend the allowlisted client hook.**

Add only `nextcloud`; keep portal output unchanged. Run twice against a disposable/mocked Keycloak and prove unrelated clients/users unchanged.

- [ ] **Step 3: Add idempotent provider bootstrap.**

Use `occ user_oidc:provider keycloak` with the Keycloak discovery URL, client ID, secret read from file, mappings `sub`, `name`, `email`, `groups`, auto-provision, PKCE, and multiple backends so `?direct=1` preserves local recovery. Verify a redacted provider view.

- [ ] **Step 4: Add centralized Ingress and portal metadata.**

Use class `tailscale`, ProxyClass `homelab`, host `nextcloud`, chart HTTP Service port, and exact metadata: enabled, name Nextcloud, core-files description, category Personal, local icon, `groups` access, groups `homelab-users,homelab-admins`, and order.

- [ ] **Step 5: Test and commit.**

```bash
python3 platform/keycloak/test_client_registration.py -v
python3 apps/nextcloud/test_nextcloud_manifests.py -v
git add platform/keycloak apps/nextcloud/config/oidc-bootstrap.yaml infrastructure/ingress/config/nextcloud-ingress.yaml
git commit -m "feat: integrate Nextcloud with Keycloak and portal"
```

### Task 6: Add monitoring and operational boundaries

**Files:**
- Create: `apps/nextcloud/config/monitoring.yaml`
- Modify: `apps/nextcloud/config/networkpolicy.yaml`
- Modify: `apps/nextcloud/test_nextcloud_manifests.py`
- Create: `apps/nextcloud/README.md`

**Interfaces:**
- Consumes: Vault metrics credential and Prometheus CRDs.
- Produces: internal metrics, ServiceMonitor, and availability/cron/errors/restarts/storage alerts.

- [ ] **Step 1: Write failing monitoring tests.**

Require selected Service/port, 15s scrape, no metrics Ingress, exact target labels, warning at 120 Gi, critical at 135 Gi, host-space guard, cron staleness, OOM/restart, and application-availability rules.

- [ ] **Step 2: Add metrics and alerts.**

Use a pinned exporter digest or the verified 34.0.4 OpenMetrics endpoint. Supply only a scoped Vault token. Disable expensive app-detail metrics initially and write explicit remediation annotations.

- [ ] **Step 3: Document health and measurement.**

Document probe meaning, metric rotation, Redis `noeviction` proof, PostgreSQL proof, PVC/host thresholds, and idle/upload/cron measurement commands.

- [ ] **Step 4: Test and commit.**

```bash
python3 apps/nextcloud/test_nextcloud_manifests.py -v
git add apps/nextcloud
git commit -m "feat: monitor Nextcloud production boundaries"
```

### Task 7: Implement atomic backup and isolated restore

**Files:**
- Create: `scripts/backup-nextcloud.sh`
- Create: `scripts/restore-nextcloud.sh`
- Create: `tests/test_nextcloud_backup.py`
- Create: `docs/runbooks/nextcloud-recovery.md`

**Interfaces:**
- Consumes: production pod/PVCs/database and `/backups/services/nextcloud`.
- Produces: private atomic backup set and isolated restore report.

- [ ] **Step 1: Write failing state-machine tests.**

Mock `kubectl`, `rsync`, `pg_dump`, and checksums. Prove maintenance-on, dump, copy, validation, atomic rename, maintenance-off order. Inject DB, rsync, validation, collision, SIGINT, and maintenance-off failures; none may publish a final set.

- [ ] **Step 2: Implement backup.**

Use `set -eu`, `umask 077`, validated destination, same-filesystem `mktemp -d`, traps, non-overwriting rename, database-only `pg_dump`, gzip validation, ACL/xattr-preserving rsync, counts, versions, and seeded checksums. Never copy live PostgreSQL files.

- [ ] **Step 3: Implement isolated restore.**

Require explicit backup and disposable namespace/database. Refuse production names. Restore files and SQL, run pinned-version repair/upgrade, verify seeded user/file/checksum/OIDC/jobs/readiness, and emit a report before cleanup.

- [ ] **Step 4: Test and commit.**

```bash
python3 -m unittest -v tests.test_nextcloud_backup
shellcheck scripts/backup-nextcloud.sh scripts/restore-nextcloud.sh
git add scripts/backup-nextcloud.sh scripts/restore-nextcloud.sh tests/test_nextcloud_backup.py docs/runbooks/nextcloud-recovery.md
git commit -m "feat: add verified Nextcloud backup and restore"
```

### Task 8: Deploy, validate the pattern, and close Phase 26

**Files:**
- Modify: `docs/application-onboarding.md`
- Modify: `apps/_template/README.md`
- Modify: `README.md`
- Modify: `docs/workstation-plan.md`
- Modify: `docs/troubleshooting.md` only for observed issues

**Interfaces:**
- Consumes: Tasks 1–7 and completed portal plan.
- Produces: production Nextcloud, passed contract, restored backup, measured resources, completed Phase 26.

- [ ] **Step 1: Record preflight.**

Record memory, CPU, `/srv`/`/backups`, PVCs, pod count, Argo health, and fresh PostgreSQL/Keycloak backups. Stop if any prior Application is unhealthy.

- [ ] **Step 2: Reconcile in dependency order.**

Apply the Vault ceremony; verify VSO status without values; run DB and Keycloak hooks; sync Nextcloud. Verify two retained PVCs, Redis, app pod, CronJob, monitoring, and one Tailscale hostname.

- [ ] **Step 3: Execute functional acceptance.**

Test approved/denied OIDC groups, repeat-login stable ID, direct local admin, browser and WebDAV checksum, app-password flow, pod restart persistence, CronJob, PostgreSQL, Redis locking, and `noeviction`.

- [ ] **Step 4: Execute negative tests.**

From an unauthorized namespace prove denial to app, Redis, PostgreSQL, and metrics. Prove approved proxy/scraper paths. Confirm no Internet egress, plaintext secret, SQLite, or file-lock fallback.

- [ ] **Step 5: Prove backup/restore.**

Create a manual backup, verify private mode/manifest, restore to disposable infrastructure, pass user/file/checksum/OIDC/readiness, retire only disposable resources, retain backup/report.

- [ ] **Step 6: Measure and tune.**

Capture idle, initialization, upload/WebDAV, cron, exporter, Redis, and proxy CPU/memory plus disk growth. Replace estimates with measured headroom and rerun acceptance after limit changes.

- [ ] **Step 7: Harden the shared contract.**

Add only rules proven general across portal and Nextcloud; keep app exceptions local. Re-run both applications' conformance.

- [ ] **Step 8: Run final gates and document reality.**

```bash
python3 -m unittest discover -v
yamllint --strict -c .yamllint.yaml bootstrap environments infrastructure observability platform apps .github
git diff --check
```

Run pinned kubeconform with `Skipped: 0`; require all Argo Applications healthy. Replace Phase 26 roadmap prose with exact versions, measurements, thresholds, URLs, restore result, limitations, and design/plan links.

- [ ] **Step 9: Commit.**

```bash
git add docs/application-onboarding.md apps/_template/README.md README.md docs/workstation-plan.md docs/troubleshooting.md apps/nextcloud
git commit -m "docs: complete phase 26 personal applications"
```
