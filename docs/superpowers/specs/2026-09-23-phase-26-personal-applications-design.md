# Phase 26 — Personal Applications Design

## 1. Purpose

Phase 26 turns the homelab from a platform with individual worked examples
into a platform that can onboard personal applications repeatably. It delivers
two applications:

1. the existing Homelab Portal as the reference application; and
2. a new production Nextcloud instance as the independent, stateful proof that
   the onboarding pattern works beyond the reference.

The phase does not rebuild the portal. Its application repository at
`git@github.com:Slqzeer/homelab-portal.git` already owns the Go/Astro product,
tests, release workflow, and deployable Kustomize package. This repository
owns the site integration.

The phase is complete only when both applications pass the same onboarding
contract, Nextcloud can hold up to 150 GB of user files operationally, and a
tested backup can restore a seeded Nextcloud instance.

## 2. Scope

### Included

- A written application-onboarding contract, reference scaffold, and automated
  conformance tests in this repository.
- An idempotent, narrowly scoped Keycloak client-registration mechanism for
  Phase 26 clients.
- Production release and GitOps integration of `homelab-portal`.
- A fresh production Nextcloud installation for core file synchronization and
  sharing, with standard bundled applications only.
- Dedicated namespaces, Vault/VSO delivery, OIDC, centralized Tailscale
  Ingresses, default-deny network policies, monitoring, alerts, resource
  measurements, rollback, retirement, and acceptance procedures.
- A manual, validated, atomic backup and a restore drill for Nextcloud.
- Updating `README.md`, `docs/workstation-plan.md`, and operational runbooks
  with measured deployed facts.

### Excluded

- Migrating users or files from an existing Nextcloud installation.
- Collabora, OnlyOffice, antivirus, external object storage, Imaginary, and
  other non-core Nextcloud integrations.
- Internet or LAN exposure; both applications remain tailnet-only.
- Multi-node availability. The cluster and storage are single-node, so one
  replica with explicit recovery is the honest availability model.
- Scheduled backup automation, which remains Phase 28.
- A generic manifest generator or a new Argo CD ApplicationSet framework.
- Fine-grained application authorization in the portal beyond its existing
  Keycloak-group model.

## 3. Existing constraints

- Argo CD reconciles this repository and only
  `environments/homelab/root.yaml` is applied by hand.
- Child `Application` objects live only in `environments/homelab/apps/`.
- Tailscale Ingresses live only in `infrastructure/ingress/config/`, whose
  Application is wave 21. Each uses the `homelab` ProxyClass.
- Keycloak is wave 24, uses the `homelab` realm, and imports its seed only on
  first start. Editing `realm.yaml` does not update the live realm.
- The default `local-path` StorageClass writes beneath
  `/srv/kubernetes/storage`, uses `WaitForFirstConsumer`, and cannot expand or
  enforce the requested capacity.
- PostgreSQL is the durable shared database server. The existing shared Redis
  is a bounded `allkeys-lru` cache and is not safe for Nextcloud transactional
  file locks.
- Vault configuration is versioned in `platform/vault/configure-vault.sh` but
  applied deliberately by an operator. Secret values never enter Git.
- The host is memory-constrained and has experienced pressure-related
  failures. Every new workload and Tailscale proxy needs explicit resources
  and post-deployment measurement.
- Application images and chart versions are immutable. `latest`, floating
  chart versions, and tag-only deployment are forbidden.

## 4. Chosen architecture

### 4.1 Contract rather than generator

The reusable pattern is a contract enforced by documentation and tests. It is
not a shared Kustomize component and not an ApplicationSet. The portal is a
custom application shipped by its own repository, while Nextcloud is a
third-party Helm release with site-specific manifests. Forcing those shapes
through one generated manifest would hide important exceptions and make the
abstraction harder to review than the applications.

The contract therefore fixes outcomes and ownership while allowing Helm or
Kustomize packaging. The checked-in scaffold demonstrates the expected file
roles. Each registered application has explicit test cases; negative fixtures
prove that missing safeguards fail the validator.

### 4.2 Repository ownership

| Owner | Responsibilities |
| --- | --- |
| `homelab-portal` | Portal source, tests, Dockerfile, release pipeline, immutable image, generic Kustomize package |
| `homelab` | Namespaces, Argo registration, site values, Vault/VSO, Keycloak clients, network policy, centralized Ingress, monitoring, backups, acceptance |
| `homelab-nextcloud` image repository | Minimal derivative image recipe and release evidence for a pinned Nextcloud base plus checksum-pinned `user_oidc` |
| Upstream Nextcloud Helm repository | Generic Kubernetes chart, pinned to `9.2.5` |

Nextcloud values and supplementary site manifests live in
`apps/nextcloud/`, the directory this repository already reserves for
per-application values and manifests. Its Argo object lives in
`environments/homelab/apps/nextcloud.yaml`.

The phase uses one design and two implementation plans:

- onboarding foundation plus portal integration;
- production Nextcloud plus final contract hardening.

This keeps each plan independently reviewable and testable.

### 4.3 Dependency waves

Both new applications depend on the Phase 25 identity provider and therefore
run at wave 25, after Keycloak at wave 24. This intentionally replaces the
current statement that nothing follows wave 24. The rule remains
dependency-based: later applications use the lowest wave above their real
dependencies rather than incrementing waves by convention.

Ingresses remain in the centralized wave-21 application. A Tailscale Ingress
can acquire its hostname before its backend exists, so portal and Nextcloud
may briefly return an upstream error while their wave-25 backends arrive; they
must not block the backend applications from being created.

## 5. Application onboarding contract

An application is onboarded only when all applicable gates below are met.

### 5.1 Ownership and immutability

- Record the source repository or upstream chart, owning maintainer, release
  version, immutable revision, image digest, and rollback target.
- Pin GitHub Actions by commit, charts by exact version, and images by digest.
- Record the upstream support window and upgrade constraints.
- Render the deployable revision in CI; a pinned repository revision whose
  manifest still references `latest` is not immutable.

### 5.2 Placement and lifecycle

- Use a dedicated namespace unless a written ownership reason justifies an
  existing one.
- Declare the namespace once in `bootstrap/namespaces/namespaces.yaml` with
  restricted Pod Security labels.
- Put the child Argo object in `environments/homelab/apps/` at the lowest wave
  above its real dependencies.
- Document upgrade, rollback, and retirement. Stateful retirement takes a
  final backup and handles retained PVCs explicitly before namespace removal.

### 5.3 Secrets and identity

- Give each namespace a least-privilege Vault policy and namespace-local
  ServiceAccount and `VaultAuth`.
- Project only named keys through `VaultStaticSecret`; set `excludeRaw: true`
  and define restart behavior for rotated values.
- Never put a secret in Git, a ConfigMap, a command-line argument, or a logged
  environment dump.
- If the application supports OIDC, create one confidential client with exact
  redirect/logout URLs and minimum claims. Preserve a documented local
  recovery route when the application supports one.

### 5.4 Exposure and publication

- Expose HTTP only through a centralized Tailscale Ingress using the bounded
  `homelab` ProxyClass.
- Do not expose administrative metrics, health, or database ports through the
  Ingress.
- Add `portal.homelab.io/*` metadata only after target authentication has been
  verified. Publication is always explicit and Git-versioned.
- Never infer that a Service or Pod is safe to publish.

### 5.5 Isolation and operation

- Apply default-deny ingress and egress.
- Permit only selected ingress proxy and monitoring pods, then only the DNS,
  API, identity, database, cache, or other peers the workload actually uses.
- Define startup, readiness, and liveness behavior; structured logs; metrics;
  alerts; CPU/memory requests and limits; and a post-rollout measurement.
- Classify every state path as durable, reconstructible, or secret-derived.
- For durable state, document and prove backup and restore before production
  acceptance.

### 5.6 Conformance artifacts

`docs/application-onboarding.md` is the human contract and
`apps/_template/` is the non-deployed reference scaffold. Tests inspect the
registered Argo applications and their rendered resources. Negative fixtures
cover at least:

- floating image or chart version;
- missing restricted namespace labels;
- plaintext or over-broad secret delivery;
- missing default-deny policy;
- Tailscale Ingress without the bounded ProxyClass;
- portal publication without an explicit access policy;
- stateful application without backup and restore classification;
- unsafe exposure of operations or metrics ports.

The template contains no deployable credentials, hostnames, or placeholder
application that Argo could accidentally reconcile.

## 6. Keycloak client registration

Phase 25's realm seed is first-import-only and the Grafana client uses a
manual secret-copy ceremony. Repeating that ceremony does not scale to
personal applications. Phase 26 adds a narrow, idempotent client-registration
Job after Keycloak.

The Job:

- authenticates to Keycloak with the existing Vault-held administrative
  credential;
- reads Phase 26 client secrets from namespace-local VSO projections;
- creates or updates only an allowlist of named clients;
- enforces exact redirect URIs, logout URIs, scopes, protocol mappers, and
  enabled flow settings;
- does not reconcile users, passwords, TOTP state, groups, roles, the Grafana
  client, or unrelated realm settings;
- fails if a named client has an unexpected type or ownership marker;
- is safe to run repeatedly and proves on a second execution that no material
  change remains.

Git owns client structure. Vault owns the corresponding client secrets. The
same secret value is projected to the registration Job and the consuming
application, eliminating manual copying and generated-secret drift.

## 7. Homelab Portal integration

The portal's product behavior remains governed by
`docs/superpowers/specs/2026-09-16-homelab-portal-design.md` here and its
implementation documents in the application repository.

Phase 26 integration performs the following:

1. Run the portal repository's complete verification suite and create a tagged
   release through its existing protected workflow.
2. Record the reviewed Git revision, GHCR tag, multi-architecture digest,
   signature, SBOM, vulnerability report, and promotion approval.
3. Add namespace `portal` with restricted Pod Security labels.
4. Extend Vault configuration for the portal OIDC client secret, current
   session key, and optional previous session key. Project only those keys
   through VSO.
5. Register confidential client `homelab-portal` with callback
   `https://portal.taildf6cd4.ts.net/auth/callback`, the application's logout
   route, scopes `openid profile groups`, the JSON `groups` claim, and the
   exact `portal-admin` group contract.
6. Patch the portal homelab overlay with the real base URL, Keycloak issuer,
   immutable image, API endpoints, selectors, Vault path, and monitoring
   labels. The generic documentation-only network addresses must not survive
   rendering.
7. Add the wave-25 Argo application and the centralized `portal` Tailscale
   Ingress. Only the named public Service port is exposed.
8. Add publication metadata only to owner-approved Ingresses. The portal's own
   Ingress is excluded by namespace/name so it does not list itself.

Portal acceptance covers anonymous, authenticated, group-restricted, and
`portal-admin` views; invalid metadata; initial-list failure; stale and expired
watch state; forbidden Secret reads and Ingress writes; and denied network
flows. Authorization remains server-side and fail-closed.

## 8. Nextcloud design

### 8.1 Version and packaging

Use Nextcloud Helm chart `9.2.5` with Nextcloud `34.0.4-apache`. Nextcloud 35.0.0
was released only one week before this design; the supported 34.0.4 maintenance
release is the lower-risk production baseline. Major upgrades occur one at a
time and only after verified backup.

The upstream chart is community-maintained rather than commercially supported
by Nextcloud GmbH. Therefore the phase stores the exact upstream values used,
renders every resource in tests, rejects unwanted bundled databases and
Ingresses, and pins the application image separately from the chart's default
`appVersion`.

The derivative application image contains only:

- the official pinned Nextcloud base image;
- `user_oidc` `8.11.0`, as an exact release archive verified by checksum; and
- no office, antivirus, preview, or object-storage additions.

The image is built for `linux/amd64` and `linux/arm64`, scanned, signed,
attested, and published to GHCR. The running pod has no need for general
Internet egress to install code.

### 8.2 Runtime topology

```text
Tailnet user
    |
    v
Tailscale Ingress nextcloud (wave 21)
    |
    v
Nextcloud Service -> one Nextcloud pod (wave 25, Recreate)
                         |       |         |
                         |       |         +-> Keycloak homelab realm
                         |       +------------> dedicated Redis, noeviction
                         +--------------------> PostgreSQL nextcloud database
                         |
                         +-> 5Gi application/config PVC
                         +-> 150Gi user-data PVC

CronJob every five minutes -> same retained volumes + PostgreSQL + Redis
Prometheus -> metrics endpoint/exporter only
```

The chart's internal SQLite, bundled PostgreSQL, bundled MariaDB, bundled
Redis, built-in Ingress, autoscaling, Collabora, and Imaginary are disabled.
One application replica matches the single-node RWO storage and avoids
pretending to offer high availability.

The background task uses the chart's Kubernetes CronJob mode every five
minutes, runs as the same application user, and mounts the same RWO volumes.
It does not use the root-requiring cron sidecar.

### 8.3 Durable state and 150 GB ceiling

- A retained 5 Gi `local-path` PVC holds application/configuration state,
  installed app state, and themes.
- A separate retained 150 Gi `nextcloudData` PVC holds user files.
- PostgreSQL holds application metadata in a dedicated `nextcloud` database
  owned by a dedicated role created by an idempotent sync-hook Job.
- Redis is reconstructible and has no PVC. It is dedicated because
  transactional file locks must not share the existing `allkeys-lru` eviction
  policy. It uses authentication, a memory bound, and `noeviction`.

The 150 Gi PVC request is not a quota: `local-path` does not enforce it.
Prometheus therefore warns at 120 Gi (80 percent) and alerts critically at
135 Gi (90 percent), with a separate host-filesystem free-space alert. The
runbook treats 150 GB as the stop boundary. Per-user quotas govern users; the
aggregate alerts protect the host.

### 8.4 Identity and access

Register confidential client `nextcloud` with the exact redirect URI
`https://nextcloud.taildf6cd4.ts.net/apps/user_oidc/code`, post-logout return
`https://nextcloud.taildf6cd4.ts.net/`, and back-channel logout URI
`https://nextcloud.taildf6cd4.ts.net/index.php/apps/user_oidc/backchannel-logout/keycloak`.
The `user_oidc` app uses Authorization Code with PKCE, validates TLS and token
signatures, and auto-provisions from a stable Keycloak subject. It maps display
name, email, and groups.

Members of `homelab-users` and `homelab-admins` may enter through Keycloak.
The portal catalog item uses `groups` access with those exact groups. A local
Nextcloud administrator credential remains in Vault and the direct-login route
remains documented for recovery; it is tested without weakening normal OIDC
login.

### 8.5 Network and security

The `nextcloud` namespace is default-deny. Explicit rules permit:

- the selected Tailscale proxy to the public Nextcloud port;
- the selected Prometheus scraper to the metrics port;
- Nextcloud and its CronJob to kube-dns, PostgreSQL, dedicated Redis, and the
  Keycloak service;
- the database bootstrap Job to PostgreSQL;
- only necessary intra-namespace Nextcloud-to-Redis traffic.

There is no arbitrary Internet egress and no public operations endpoint. The
pod and CronJob use restricted-compatible security contexts, dropped
capabilities, seccomp `RuntimeDefault`, explicit resources, and the narrowest
writable mounts the upstream image supports. Any required exception to a
read-only root filesystem is rendered and tested rather than hidden in a broad
Pod Security exemption.

### 8.6 Monitoring and health

Startup allows the first initialization and database migrations without
liveness restarts. Readiness proves the local application can serve requests;
loss of PostgreSQL or Redis makes Nextcloud unready rather than falling back to
SQLite or file locking. Liveness detects a wedged local process without making
Keycloak availability a restart condition.

Monitoring includes application availability, background-job age, database
and Redis connectivity, HTTP errors, pod restarts/OOM kills, PVC consumption,
host free space, and backup age once Phase 28 schedules backups. Metrics
credentials come from Vault and are not exposed through Tailscale.

Initial resource values are conservative starting bounds, not claimed facts.
The rollout records idle and upload/sync peak memory and CPU for Nextcloud,
Redis, CronJob, metrics exporter, and both new Tailscale proxies, then replaces
the estimates in manifests and documentation with measured values.

## 9. Backup, restore, upgrades, and retirement

### 9.1 Manual backup

The destination is `/backups/services/nextcloud`, mode 0700. A backup set is
published atomically only after all members validate:

1. Enable Nextcloud maintenance mode.
2. Dump only the `nextcloud` PostgreSQL database to a private temporary file.
3. Copy config, custom apps, themes, and the user-data tree with ownership,
   permissions, timestamps, ACLs, and extended attributes preserved.
4. Generate a manifest containing version metadata, file counts, byte counts,
   and checksums for seeded verification files and the database dump.
5. Validate the SQL completion marker, compressed streams, manifest, and
   expected paths.
6. Rename the complete temporary set to its timestamped final name without
   overwriting an existing set.
7. Disable maintenance mode in cleanup handling even when validation fails;
   if that cleanup fails, emit a prominent recovery command and return failure.

Redis is not backed up because it contains reconstructible caches and locks.
The backup includes database, configuration, installed app state, themes, and
files because Nextcloud restoration is incomplete without all of them.

### 9.2 Restore drill

Restore into an isolated disposable validation namespace and database, never
over the production instance. The drill restores the file trees and database,
runs Nextcloud repair/upgrade commands required by the pinned version, and
verifies:

- a seeded user's metadata;
- a seeded file's contents and checksum;
- application and `user_oidc` state;
- database integrity and background jobs;
- HTTP readiness and login redirect configuration.

The validation namespace has no production Ingress. Successful validation is
recorded before its resources are retired.

### 9.3 Upgrade and rollback

Every upgrade begins with a fresh validated backup. Chart and image changes
are reviewed separately in rendered diffs. Nextcloud major versions are never
skipped. After a schema migration, rollback means restore of the prior image,
database, config, app state, and files as one set; changing only the image is
not a rollback.

Retirement first disables portal publication and login, takes a final backup,
verifies retention intent, then removes the Argo application using the
repository's documented finalizer order. Namespace or PVC deletion is a
separate, explicit decision.

## 10. Failure behavior

- Missing Vault material prevents the affected workload or registration Job
  from starting; no default credential is substituted.
- Keycloak loss blocks new SSO logins. Existing application sessions follow
  their local lifetime, and Nextcloud's direct local-admin recovery remains.
- PostgreSQL or Redis loss makes Nextcloud unready. It never initializes an
  accidental SQLite database or silently changes locking backend.
- Tailscale or Ingress loss removes remote reachability without changing
  application state.
- Portal authentication or authorization uncertainty hides restricted catalog
  items. Kubernetes watch errors retain only the portal's already validated
  snapshot under its existing stale/expiry rules.
- Invalid publication metadata appears only in portal admin diagnostics.
- Storage warnings do not claim enforcement. Critical capacity alert response
  stops uploads or expands/reclaims storage before the 150 GB boundary.
- A failed backup never receives a final backup-set name.

## 11. Verification and acceptance

### 11.1 Static and CI verification

- Portal's Go, frontend, manifest, contract, and release tests pass at the
  released revision.
- Helm and Kustomize output renders deterministically and passes schema checks.
- Conformance tests reject all negative fixtures in section 5.6.
- Nextcloud rendering contains no internal database, bundled cache, chart
  Ingress, floating image, unrestricted secret, or unbounded container.
- NetworkPolicy and RBAC tests assert allowed and forbidden peers/verbs.
- The derivative image verifies its base digest and `user_oidc` archive
  checksum, then passes vulnerability policy before signing.

### 11.2 Cluster acceptance

1. All pre-existing Argo applications remain `Synced` and `Healthy`.
2. Keycloak registration creates both clients; a second sync makes no material
   change and leaves unrelated clients/users untouched.
3. VSO resources are synced/healthy/ready and expose only intended keys.
4. Portal anonymous, authenticated, grouped, and admin sessions see exactly
   their allowed catalog items; hidden URLs are absent from responses.
5. Portal ServiceAccount can list/watch Ingresses but cannot read Secrets or
   mutate an Ingress.
6. Nextcloud OIDC first login auto-provisions a test user with expected name,
   email, and groups; an unauthorized group is refused.
7. Nextcloud direct local-admin recovery works in a separate session.
8. A browser and WebDAV client upload and download a seeded file with matching
   checksum; the file survives pod restart.
9. The five-minute CronJob completes and background-job age stays current.
10. PostgreSQL and dedicated Redis are used; SQLite and file locking are not.
11. Unauthorized namespaces cannot reach PostgreSQL, Redis, operations ports,
    or application ports. Approved proxy and scraper paths work.
12. Storage alerts evaluate against real usage and host capacity at 120/135 Gi
    thresholds.
13. The manual backup publishes a complete set and the isolated restore drill
    passes every check in section 9.2.
14. Resource measurements cover idle, upload/WebDAV activity, initialization,
    cron, and proxy overhead; limits are updated from evidence.
15. Both applications pass the onboarding contract, and Nextcloud appears in
    the portal only for the two approved Keycloak groups.

## 12. Implementation-plan boundaries

The first plan owns the contract, tests, Keycloak registration foundation,
portal release, homelab portal integration, portal acceptance, and its
operational documentation.

The second plan owns the derivative Nextcloud image, pinned chart/site
configuration, database and cache, storage, OIDC client, network isolation,
monitoring, backup/restore drill, Nextcloud acceptance, final conformance
feedback, and Phase 26 roadmap update.

Neither plan is complete merely because manifests render. The first ends with
a working portal; the second ends with restored data from a production-shaped
Nextcloud backup and measured cluster health.

## 13. Upstream references

- Nextcloud Helm chart `9.2.5` and values:
  <https://github.com/nextcloud/helm/tree/main/charts/nextcloud>
- Nextcloud maintenance schedule:
  <https://github.com/nextcloud/server/wiki/Maintenance-and-Release-Schedule>
- Nextcloud `user_oidc` application:
  <https://github.com/nextcloud/user_oidc>
- Nextcloud 35 backup procedure (also applicable to the retained 34 baseline):
  <https://docs.nextcloud.com/server/stable/admin_manual/maintenance/backup.html>
- Nextcloud restore procedure:
  <https://docs.nextcloud.com/server/stable/admin_manual/maintenance/restore.html>
- Nextcloud background jobs:
  <https://docs.nextcloud.com/server/stable/admin_manual/occ_system.html#background-job-worker>
