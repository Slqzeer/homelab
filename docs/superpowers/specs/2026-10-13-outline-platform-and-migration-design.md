# Outline Platform and Documentation Migration Design

Date: 2026-10-13
Status: approved 2026-10-14

## Purpose

Deploy Outline as the homelab's human documentation system of record, make it
reachable only through the tailnet, authenticate eligible users through the
existing Keycloak realm, and establish a controlled migration from repository
documentation into Outline.

Success is broader than a running container. Outline is production-capable
only when authentication and admission are enforced, durable state is backed
up off-host, a clean restore completes within the recovery target, upgrades
and rollback are documented, and the first documentation collection has been
migrated and validated without creating two canonical copies.

This design deliberately separates platform delivery, documentation migration,
and deferred automation into independently releasable tickets. The current TLE
Keycloak-client work must land before Outline changes the shared Keycloak and
Vault integration files.

## Goals

- Serve Outline at `https://outline.taildf6cd4.ts.net` through centralized
  Tailscale Ingress.
- Admit members of `homelab-users` and `homelab-admins`; deny other Keycloak
  users even when they already have a valid realm SSO session.
- Bootstrap one trusted Outline administrator and keep administrator changes
  deliberate and auditable.
- Run a pinned official Outline image through repository-owned Kubernetes
  manifests that satisfy the personal-application onboarding contract.
- Preserve all durable Outline state: PostgreSQL data, attachments, encryption
  keys, and the metadata required to reconstruct the service.
- Meet a 24-hour recovery point objective and four-hour recovery time objective.
- Make Outline the canonical home for human knowledge while retaining the
  repository as the canonical home for machine-enforced and code-coupled
  information.
- Migrate documentation in bounded, owner-approved batches after a Penpot pilot.
- Preserve important rejected and superseded decisions without presenting them
  as current guidance.
- Produce issue-ready work that can be scheduled independently.

## Non-goals

- Implement Outline as part of this design-and-roadmap change.
- Fold Outline into the active TLE Keycloak-client plan.
- Expose Outline to the LAN or public Internet.
- Add email, invitations, arbitrary Internet integrations, link previews, or
  public/guest access in the initial release.
- Make Outline highly available while PostgreSQL and the k3s node remain
  single-instance dependencies.
- Build or maintain an Outline fork.
- Reproduce Git commit history, original authorship, or every transient plan
  revision inside Outline.
- Store credentials, recovery codes, private keys, or secret values in Outline.
- Use File Browser Quantum as Outline's attachment backend.
- Use GitHub as either the operational or off-host backup target.
- Give coding agents or a future documentation agent workspace-administrator
  privileges.

## Canonical-source policy

Outline is canonical for human-facing project, architectural, operational, and
decision knowledge. Git remains canonical when a document is consumed or
enforced by code, CI, GitOps, recovery tooling, or coding agents. This exception
includes:

- application recovery runbooks required by `docs/application-onboarding.md`;
- `AGENTS.md`, `CONTEXT.md`, and equivalent agent instructions;
- configuration-coupled explanations whose review must accompany code changes;
- generated references and deployment artifacts; and
- concise pointers that make the corresponding Outline page discoverable.

Every document has exactly one declared canonical source. A migrated Git file is
removed or reduced to a pointer only after its Outline replacement has passed
content, permission, link, export, and recovery validation. Git history remains
the pre-migration archive and rollback source.

Configuration comments remain beside their configuration. Generated artifacts
are not imported. Historical material is retained only when it records a
decision, constraint, failed approach, incident lesson, or reusable reasoning.
Rejected and superseded decisions carry an explicit lifecycle state and a link
to their replacement.

## Platform architecture

### Packaging and release ownership

Outline does not publish an official Helm chart. The homelab therefore owns
plain Kubernetes/Kustomize resources under `apps/outline/config`, wrapped by a
child Argo CD Application in `environments/homelab/apps/outline.yaml`.

The planning baseline is upstream Outline `v1.10.1`. Before deployment, the
release-resolution ticket must verify the selected supported release, record
its immutable image digest, inspect the rendered workload, and prove that the
image runs under the namespace's Kubernetes `restricted` Pod Security policy.
No `latest`, mutable tag, or unofficial chart may be promoted.

The `outline` namespace is declared only in
`bootstrap/namespaces/namespaces.yaml` and carries `restricted` enforce, audit,
and warn labels for Kubernetes `v1.36`. The child Application is annotated:

```yaml
homelab.io/onboarding-contract: v1
homelab.io/owner: personal-applications
homelab.io/state: durable
backup.homelab.io/restore-runbook: docs/runbooks/outline-recovery.md
```

It uses the lowest sync wave above PostgreSQL, dedicated Redis, Vault secret
projection, and the Keycloak registration dependency. Automated prune,
self-heal, and `ServerSideApply=true` are required.

### Workload shape

The initial deployment is one Outline replica with a `Recreate` update strategy.
Single-replica maintenance downtime is accepted. Recreate avoids concurrent
database migration processes and prevents a rolling replacement from contending
for a ReadWriteOnce attachment volume.

Initial resource bounds are 100 millicores and 512 MiB requested, with one CPU
and 1 GiB memory as limits. These are rollout baselines, not permanent guesses:
idle, login, import, search-index, and representative collaborative-edit loads
must be measured before production promotion, and the committed values must be
updated from evidence.

Startup, readiness, and liveness probes must test distinct failure modes. A
readiness failure removes user traffic; it must not restart a process that is
merely waiting on PostgreSQL or Redis. Application logs are centralized, but
tokens, cookies, headers, document bodies, OIDC claims, environment dumps, and
secret values must never be logged.

### PostgreSQL

Outline uses the existing PostgreSQL service in the `databases` namespace, with
its own `outline` database and `outline` role. An idempotent database-bootstrap
Job owns creation and password reconciliation. Credentials originate in Vault,
are projected through namespace-scoped VSO resources, and never appear in SQL,
argv, manifests, or logs.

The shared server has a 50-connection ceiling. Outline must set an explicit
connection pool rather than accept an unbounded or upstream default. The initial
ceiling is five connections with a minimum of one. A measured need to raise the
ceiling must include a whole-server connection budget covering PostgreSQL,
Keycloak, Penpot, Outline, administrative sessions, upgrade overlap, and backup
jobs.

Schema migrations are part of application startup. A container rollback is not
considered safe after an incompatible migration. Every upgrade takes a complete
pre-upgrade backup, records the previous image revision, and documents whether
rollback means redeploying that image or restoring the pre-upgrade data set.

### Redis

Outline receives a dedicated, single-replica Redis instance in the `outline`
namespace. Redis is reconstructible and ephemeral unless a restore test proves
that the pinned Outline release stores non-reconstructible state there. This
avoids shared-cache eviction and workload interference without creating a false
claim of Redis durability.

Only Outline may reach this Redis Service. Authentication material lives in
Vault and is projected only into the Outline namespace.

### Attachments

The initial file backend is Outline's local storage mode mounted at
`/var/lib/outline/data` on a 10 GiB `local-path` ReadWriteOnce PVC. The PVC is
durable application state but is not a backup. Capacity alerts must fire before
uploads exhaust the node filesystem.

File Browser Quantum exposes filesystem sources and WebDAV; it does not provide
the S3-compatible object API Outline requires. It is a separate application and
must never directly modify Outline-managed attachment files.

A deferred storage ticket evaluates a real S3-compatible backend and a tested
migration away from the local PVC. It may select MinIO, a managed object store,
or another compatible service, but must justify the extra operational surface.

## Secrets

Vault owns all generated values, including:

- Outline `SECRET_KEY` and utility secret;
- PostgreSQL and Redis credentials;
- the Keycloak OIDC client secret;
- backup repository credentials and encryption password; and
- later, narrowly scoped migration or documentation-agent credentials.

Each consumer receives a namespace-local ServiceAccount, `VaultAuth`, and
`VaultStaticSecret` with exact paths and projected keys,
`spec.destination.transformation.excludeRaw: true`, and a documented rotation
restart path. The Keycloak namespace and Outline namespace receive independent
projections of the OIDC client secret from controlled Vault paths; neither
namespace receives secrets used only by the other.

`SECRET_KEY` is part of the disaster-recovery contract. Its recoverability is
tested during restore without committing its plaintext value. Backup decryption
material lives in Vault and in a separately controlled offline recovery copy,
never alongside the backup repository.

The existing first-sync trap, where Keycloak runs before an application-created
secret exists, is not accepted. Secret creation, Keycloak projection, client
registration, and application projection must have explicit ordering and
converge from a clean cluster without a remembered second Argo sync.

## Keycloak authentication and authorization

### OIDC client

The Keycloak registration hook manages a confidential client with these exact
properties:

| Property | Value |
| --- | --- |
| Client ID | `outline` |
| Origin | `https://outline.taildf6cd4.ts.net` |
| Redirect URI | `https://outline.taildf6cd4.ts.net/auth/oidc.callback` |
| Post-logout URI | `https://outline.taildf6cd4.ts.net/` |
| Flow | Authorization code only |
| Requested scopes | `openid profile email groups` |
| Keycloak default client scopes | `profile email groups` |

No wildcard redirect, origin, or logout value is allowed. Direct grants,
implicit flow, and service accounts are disabled.

Outline is configured in manual OIDC mode because pods cannot resolve the
tailnet MagicDNS hostname. Browser-facing endpoints use the public tailnet URL:

```text
OIDC_AUTH_URI=https://keycloak.taildf6cd4.ts.net/realms/homelab/protocol/openid-connect/auth
OIDC_LOGOUT_URI=https://keycloak.taildf6cd4.ts.net/realms/homelab/protocol/openid-connect/logout
```

Back-channel calls use the cluster Service:

```text
OIDC_TOKEN_URI=http://keycloak.keycloak.svc.cluster.local:8080/realms/homelab/protocol/openid-connect/token
OIDC_USERINFO_URI=http://keycloak.keycloak.svc.cluster.local:8080/realms/homelab/protocol/openid-connect/userinfo
```

The inspected `v1.10.1` implementation supports this split. Manual mode does
not enable PKCE; that limitation is accepted for the initial confidential,
tailnet-only client and must be recorded and live-tested. Discovery mode is not
used until cluster DNS can resolve and securely reach the public issuer URL.

### Admission

Outline's generic OIDC provider does not use the `groups` claim to decide who
may sign in. Keycloak therefore owns admission.

An `outline-access` client role is mapped to both `homelab-users` and
`homelab-admins`. A copied, client-specific browser authentication flow checks
for that role and explicitly denies access when it is absent. The flow is bound
only to the Outline client; it must not alter realm-wide browser login behavior.
Its live acceptance suite covers:

- an eligible `homelab-users` member;
- an eligible `homelab-admins` member;
- a realm user in neither group;
- a denied user who already holds a valid Keycloak SSO session; and
- repeated login and logout in a fresh private browser session.

The flow, role, group mappings, and client binding require explicit live-realm
reconciliation. Editing the import-only realm seed is insufficient.

### Outline administrators

Generic Outline OIDC does not map a Keycloak group to the Outline workspace
administrator role. The first workspace user becomes an administrator.

Before public ingress and portal publication, a preselected member of
`homelab-admins` performs the first login through controlled access. Operators
verify the resulting administrator role, then test all admission cases. The
person's identity is not hardcoded in Git.

Later promotions and demotions are manual Outline operations recorded in the
runbook and audited against `homelab-admins`. Automatic role reconciliation is
a deferred spike, not an initial dependency. No fork is maintained to obtain
this behavior.

## Network and exposure

The namespace starts with default-deny ingress and egress. Allow only:

- DNS to the cluster resolver;
- Outline to its PostgreSQL database on TCP/5432;
- Outline to its dedicated Redis on TCP/6379;
- Outline to Keycloak on TCP/8080, with reciprocal Keycloak ingress for the
  exact Outline pod selector;
- the Tailscale ingress proxy to Outline's user-facing HTTP port;
- monitoring traffic when a supported metrics endpoint exists; and
- backup traffic from the dedicated backup producer or replicator.

Arbitrary Internet egress is denied. Email, imports that fetch remote URLs,
embeds, link previews, telemetry, updates, and third-party integrations require
separate allowlisted work.

The Tailscale Ingress lives in `infrastructure/ingress/config`, routes only the
user-facing HTTP Service port, supports WebSockets, and exposes no operations,
metrics, database, or Redis ports. It is initially unlisted. The portal entry is
enabled only after OIDC, admission, bootstrap, backup, and smoke-test evidence
passes, using group-based access metadata for `homelab-users,homelab-admins`.

## Backup and disaster recovery

### Recovery objectives

- RPO: 24 hours.
- RTO: four hours.
- Restore drills: once before production promotion and at least every six
  months thereafter, plus after material storage or backup changes.
- Retention: 14 daily, 8 weekly, and 12 monthly recovery points.

### Backup set and consistency

An Outline recovery point is a single reconciled set containing:

- an application-consistent PostgreSQL dump of the `outline` database;
- the complete attachment tree from `/var/lib/outline/data`;
- a manifest with release revision, schema context, timestamps, sizes, and
  checksums;
- the Vault paths and recovery procedure needed to recover `SECRET_KEY` and
  service credentials, without copying their plaintext values into Git; and
- the runbook version used to create and validate the set.

A brief daily write outage is acceptable to guarantee that database references
and attachment files agree. The backup implementation must demonstrate its
quiescence mechanism; a best-effort `pg_dump` plus unrelated filesystem copy is
not accepted. Backup publication is atomic: partial artifacts are private and
never become the newest recovery point.

### Local first copy

The dedicated `/backups` disk is the first restore layer, not the production
durability boundary. The producer receives write access only to a private
`/backups/services/outline` location. The Outline pod cannot delete or modify
backups. Files and directories use least-privilege ownership and modes, and
scheduled failure or staleness raises an alert.

### Off-host copy

Production promotion requires an encrypted off-host copy and a restore that
does not depend on `/srv` or `/backups`. The preferred candidate is restic with
a private Backblaze B2 bucket because restic supports B2, B2 supports scoped
application keys, and the provider offers object immutability. The provider is
not locked by this design: its ticket must recheck pricing, account/data
location, credentials, retention semantics, Object Lock compatibility, restore
throughput, and documented export/exit before selection.

Routine backup credentials are bucket-scoped and cannot administer the storage
account. Provider administration and any privileged retention/pruning
credentials are separate. Object immutability is enabled only after proving it
does not strand restic locks or prevent the agreed retention workflow.

GitHub repositories, Git LFS, releases, and Actions artifacts are explicitly
rejected as backup targets. A workspace Markdown export may be used during a
migration validation, but it is evidence and portability output, not a service
backup.

### Restore acceptance

The seeded restore drill uses an isolated namespace and must prove that:

- database, hierarchy, collections, users, permissions, and attachments are
  intact;
- restored encrypted state is readable with the recovered `SECRET_KEY`;
- OIDC login and logout work through a non-production route;
- search returns known seeded documents;
- no production routing or state changed;
- artifact checksums and retention metadata validate; and
- measured recovery completes within four hours.

The runbook records evidence without secrets. A restore that merely starts a
pod does not pass.

## Observability and operations

Initial monitoring covers Deployment availability, readiness, restart loops,
resource saturation, PVC usage, tailnet endpoint availability, backup age,
backup failure, off-host replication failure, and restore-drill age. Centralized
logs are retained with the repository's existing Grafana Cloud path and must be
checked for accidental document or authentication content.

Application-specific metrics are added only when the selected Outline release
exposes a supported and useful endpoint. Kubernetes workload health and backup
freshness are not delayed while waiting for application metrics.

Releases are reviewed monthly. Upgrades require release-note and migration
review, an exact image tag and digest, a fresh verified backup, a rollback
classification, a maintenance window, smoke tests, OIDC tests, and an updated
release record. Automatic version upgrades are prohibited.

## Documentation information architecture

Outline uses a hybrid collection model:

- **Homelab Platform** for shared infrastructure, standards, onboarding, and
  operations;
- one collection per substantial project or bounded domain;
- **Decisions** for cross-project decisions and rejected alternatives; and
- **Archive** for valuable superseded material that must not appear current.

Project-specific decisions stay with their project unless their effect is
cross-cutting. Top-level collection names, document types, and lifecycle states
are controlled. Project owners may add useful tags without central approval.

All admitted users can read ordinary internal documentation. Project
maintainers edit their collections; platform operators maintain operational
content; Outline administrators manage workspace structure and permissions.
Private or unusually sensitive collections must name their audience explicitly.

Each collection has a primary owner and optional backup owner. Every governed
document records:

- owner;
- lifecycle state: `Proposed`, `Accepted`, `Rejected`, `Superseded`,
  `Archived`, or `Current` as appropriate;
- document type;
- canonical source;
- related project;
- source repository permalink when migrated;
- migration date; and
- last-reviewed date.

Critical runbooks and security procedures are reviewed every six months.
Ordinary documentation is reviewed when its project releases, architecture
changes, or an incident exposes a gap.

## Required templates

The information-architecture ticket creates templates for:

- project overview;
- architecture;
- deployment and operations;
- runbook;
- troubleshooting;
- ADR/decision record;
- onboarding;
- reference; and
- archived or superseded content.

Every project documents purpose and boundaries, owners, architecture and
dependencies, setup/deployment, operations/recovery, security/access, known
limitations, decisions, and links to source and issue tracking. An explicit
`Not applicable` is allowed so omissions remain intentional.

## Migration workflow

### Inventory and classification

The migration inventory covers main-worktree Markdown and README files plus
human knowledge embedded in other tracked formats. It excludes
`.superpowers/worktrees/**`, generated deployment artifacts, secret material,
and transient agent progress files.

Each item is classified as:

- canonical in Outline;
- canonical in Git;
- converted into a decision record;
- archived for provenance;
- generated and excluded; or
- duplicate/noise eligible for removal.

The inventory also records owner, target collection, permissions, attachments,
links, sensitivity, and migration dependencies.

### Pilot

Penpot is the pilot because its documentation spans deployment, Keycloak,
persistent state, recovery, security caveats, and measured operations. The pilot
establishes the final templates and migration contract before bulk automation.

Manual editing is permitted during the pilot. The pilot must validate headings,
tables, fenced code, diagrams, internal links, repository links, attachments,
permissions, metadata, search, export, secret scanning, and source provenance.
Findings update the tooling contract before any bulk batch.

### Migration tooling

Bulk migration uses an idempotent tool with a dry-run inventory and a durable
source-to-destination mapping. It detects duplicates, rewrites internal links,
preserves code and tables, uploads attachments, assigns metadata, and emits a
machine-readable success/failure report. Re-running a completed batch updates
its owned document rather than creating a duplicate.

The tool authenticates with a dedicated migration identity or API token stored
in Vault. The credential is scoped as narrowly as Outline allows and revoked
after each migration phase. Migration credentials are never reused by future
agents.

Each batch records its source commit, enters a short repository edit freeze,
imports into Outline, passes owner validation, updates allowed Git files to
pointers, and then reopens editing in Outline. A failed batch leaves Git
canonical and removes or clearly marks incomplete Outline material.

### Batch acceptance

A collection owner signs off only after proving:

- every in-scope document and attachment is accounted for;
- hierarchy, formatting, diagrams, and code blocks are usable;
- internal and repository links work;
- permissions, owner, lifecycle, and provenance are correct;
- secret scanning passes;
- search finds seeded terms;
- a collection export succeeds; and
- the batch has a tested rollback path.

Only then may duplicate Git prose be removed. Migration proceeds one project or
bounded document class per ticket; there is no single bulk-cutover ticket.

## Agent access

Coding agents receive no Outline credentials during installation or migration.
Agent-critical context remains in Git.

A deferred project may create a specialized documentation agent. It begins in
approval-only mode: it proposes drafts for a named collection owner. Before it
may publish directly to explicitly delegated collections, it must demonstrate
correct placement, minimal duplication, secret filtering, auditability,
conflict handling, and rollback. Outside delegated collections it continues to
propose changes.

The agent never receives workspace-administrator access and never removes or
rewrites project-local agent context. Repository changes remain a separate,
reviewed Git change. Its credentials are distinct from migration credentials,
stored in Vault, rotated, and scoped to the smallest supported permission set.

## Delivery decomposition

The roadmap contains three epics and 24 independently reviewable tickets:

1. **Outline platform:** release resolution; namespace and dependencies;
   workload and isolation; Keycloak client/admission; administrator bootstrap;
   tailnet publication; local backups; restore drill; off-host backups; and
   production hardening.
2. **Documentation migration:** inventory; information architecture; tooling;
   Penpot pilot; process corrections; per-project or per-class migrations;
   final validation; and canonical-source cutover.
3. **Deferred capabilities:** S3 attachment evaluation; independent File
   Browser Quantum deployment; administrator-role reconciliation research;
   documentation-agent design; approval-only documentation-agent
   implementation; and evaluation of scoped autonomous publishing.

The roadmap, not this specification, owns ordering and cross-ticket dependency
metadata. Every ticket must state scope, exclusions, dependencies, affected
systems/files, acceptance evidence, and rollback or abandonment behavior.

## Acceptance gates

### Platform deployed

- Exact release and image digest are rendered and policy-compliant.
- Namespace, secrets, PostgreSQL, Redis, PVC, probes, and network policies are
  healthy.
- Outline is reachable only through controlled tailnet access.
- Eligible users enter; ineligible realm users are denied, including with SSO.
- The chosen bootstrap user is the only initial Outline administrator.
- Rotation of each projected secret reaches its intended consumer without
  leakage or a manual second-sync trap.

### Production-capable

- Portal publication occurs only after authentication and authorization pass.
- Daily local backups meet freshness and retention checks.
- Encrypted off-host replication is healthy.
- An isolated seeded restore meets the four-hour RTO and validates content,
  attachments, permissions, search, and encrypted state.
- Resource bounds come from measurements.
- Alerts, upgrade, rollback, retirement, and recovery procedures are checked in
  and exercised where required.

### Migration-ready

- Collections, templates, permissions, metadata, ownership, and lifecycle are
  approved.
- The inventory classifies every source item without exposing secrets.
- The Penpot pilot passes batch acceptance and updates the migration contract.
- Tooling is idempotent and produces auditable reports.

### Migration complete

- Every inventory item has a final disposition.
- Every migrated collection has owner sign-off.
- Git contains only required canonical material and intentional pointers.
- No secret, broken link, duplicate canonical copy, or unowned current document
  remains.
- Final workspace export and restore evidence are recorded.

## Rejected alternatives

- **Unofficial Helm chart:** rejected because there is no official chart and a
  community chart would add an unnecessary packaging and supply-chain owner.
- **Shared Redis:** rejected because the existing cache is ephemeral and shared
  eviction or workload pressure would couple Outline to unrelated applications.
- **File Browser Quantum as attachment storage:** rejected because WebDAV and a
  filesystem browser are not the S3-compatible API Outline expects.
- **GitHub as backup storage:** rejected because Git repositories and LFS are a
  poor fit for recurring database and binary backup sets and do not establish a
  complete service-restore contract.
- **Outline-side group claims for admission/admin:** rejected because generic
  OIDC does not implement those mappings.
- **Outline fork:** rejected because maintaining authentication and role patches
  would create an avoidable security and upgrade burden.
- **Permanent dual-source documentation:** rejected because conflicting current
  copies undermine the system-of-record goal.
- **Immediate autonomous documentation agent:** rejected until human migration,
  governance, permissions, audit, and rollback are proven.

## References

- [Personal application onboarding contract](../../application-onboarding.md)
- [Keycloak operations and split OIDC routing](../../../platform/keycloak/README.md)
- [Workstation storage and backup design](../../workstation-plan.md)
- [Outline hosting requirements](https://docs.getoutline.com/s/hosting/doc/requirements-ULdYnwi4wG)
- [Outline OIDC configuration](https://docs.getoutline.com/s/hosting/doc/oidc-8CPBm6uC0I)
- [Outline file storage](https://docs.getoutline.com/s/hosting/doc/file-storage-N4M0T6Ypu7)
- [Outline data export](https://docs.getoutline.com/s/guide/doc/export-Da6C7HqL8M)
- [Outline `v1.10.1` OIDC implementation](https://github.com/outline/outline/blob/v1.10.1/plugins/oidc/server/auth/oidcRouter.ts)
- [Keycloak conditional authentication flows](https://www.keycloak.org/docs/latest/server_admin/index.html#conditions-in-conditional-flows)
- [Restic documentation](https://restic.readthedocs.io/en/stable/)
- [Backblaze B2 application keys](https://www.backblaze.com/docs/cloud-storage-application-keys)
- [Backblaze B2 Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock)
