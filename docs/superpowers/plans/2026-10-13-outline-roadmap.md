# Outline Platform and Documentation Migration Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this roadmap ticket-by-ticket. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Outline as the tailnet-only human documentation system of
record, prove its recovery, and migrate owned documentation without creating
conflicting canonical copies.

**Architecture:** Repository-owned Kubernetes manifests run one pinned Outline
replica with shared PostgreSQL, dedicated Redis, local attachment storage,
Keycloak-enforced admission, and centralized Tailscale ingress. Delivery is
split into platform, migration, and deferred-capability epics; each linked
ticket has its own acceptance and rollback boundary.

**Tech Stack:** k3s/Kubernetes 1.36, Argo CD, Kustomize, Vault KV v2, Vault
Secrets Operator, PostgreSQL 18, Redis, Keycloak 26, Tailscale Ingress, Outline,
restic, Backblaze B2 candidate, Python `unittest`, shell, and the Outline API.

**Spec:**
`docs/superpowers/specs/2026-10-13-outline-platform-and-migration-design.md`

## Global Constraints

- The active TLE Keycloak-client plan lands before Ticket 04 changes shared
  Keycloak or Vault files.
- Outline is tailnet-only at `https://outline.taildf6cd4.ts.net`; no LAN or
  Internet ingress is allowed.
- `homelab-users` and `homelab-admins` may enter; other realm users are denied
  by a client-specific Keycloak authentication flow.
- Generic Outline OIDC does not map Keycloak groups to workspace roles. Initial
  administrator promotion remains a controlled manual operation.
- The initial workload is one replica using a pinned official image tag and
  digest, repository-owned manifests, and restricted Pod Security.
- PostgreSQL is shared through a dedicated `outline` role/database with an
  initial pool minimum of one and maximum of five.
- Redis is dedicated, single-replica, and reconstructible.
- Attachments initially use a 10 GiB `local-path` RWO PVC mounted at
  `/var/lib/outline/data`.
- All secrets originate in Vault and are projected with least privilege and
  `excludeRaw: true`; no secret may enter Git, argv, logs, or a ConfigMap.
- Arbitrary Internet egress, SMTP, remote imports, embeds, and third-party
  integrations are excluded initially.
- Recovery objectives are 24-hour RPO, four-hour RTO, 14 daily, 8 weekly, and
  12 monthly recovery points, with a restore drill at least every six months.
- `/backups` is only the local first copy. Production promotion requires an
  encrypted off-host copy and a restore that does not depend on the k3s host.
- Backblaze B2 plus restic is the preferred off-host candidate, not a provider
  lock. GitHub is not a backup target.
- Migration starts only after platform production acceptance.
- Outline is canonical for human knowledge; Git stays canonical for
  machine-enforced, agent-required, generated, and configuration-coupled
  information.
- Penpot is the migration pilot. Remaining migrations are independently
  instantiated by project or bounded document class.
- No coding agent receives Outline access initially. The future documentation
  agent begins in approval-only mode and never receives workspace-admin access.

## Review Focus

- A clean-cluster sync must not require an undocumented second Keycloak sync;
  all client-secret projections and registration ordering must converge.
- A realm user outside both admitted groups, including one with an existing SSO
  session, must be denied without changing realm-wide browser authentication.
- A database migration must not be treated as safely reversible by deploying an
  older image; rollback evidence must classify when full data restore is needed.
- A recovery point must keep database references and attachment files
  consistent; unrelated `pg_dump` and filesystem timestamps do not prove that.
- Migrating a document must never leave both Git and Outline claiming to be the
  current canonical copy, nor remove files required by CI, GitOps, or agents.

## Dependency Graph

```text
TLE Keycloak work ───────────────► 04 ─► 05 ───────────────┐
                                      ▲                    │
01 ─► 02 ─► 03 ──────────────────────┘                    │
          └────────► 07 ─► 08 ─► 09                       │
                 03 ─────────► 10                         │
                 05 + 08 + 09 + 10 ─► 06 (production) ───┤
                                                          ▼
                 11 ─► 12 ─► 13 ─► 14 ─► 15 ─► 16 ─► batches
                                                           │
                                                           ▼
                                                          17 ─► 18

06 ─► 19
06 ─► 20
06 ─► 21
18 ─► 22 ─► 23 ─► 24
```

Ticket numbers identify backlog items; the graph, not numerical order alone,
controls execution. Ticket 06 is intentionally late because portal publication
is the production gate.

## Epic 1 — Outline Platform

- [ ] **Ticket 01:**
  [Resolve and pin the Outline release](outline-tickets/01-resolve-outline-release.md)
  — prove image, configuration, probes, resources, and restricted-security
  compatibility before committing deployment resources.
- [ ] **Ticket 02:**
  [Provision Outline foundations](outline-tickets/02-provision-outline-foundations.md)
  — namespace, Vault/VSO, PostgreSQL role/database, dedicated Redis, and PVC.
- [ ] **Ticket 03:**
  [Deploy the Outline workload](outline-tickets/03-deploy-outline-workload.md)
  — Deployment, Service, Kustomize, Argo Application, and default-deny network
  policy without user-facing publication.
- [ ] **Ticket 04:**
  [Integrate Keycloak admission](outline-tickets/04-integrate-keycloak-admission.md)
  — exact OIDC client, clean secret ordering, client role, group mapping, and
  client-specific denial flow.
- [ ] **Ticket 05:**
  [Bootstrap and validate OIDC](outline-tickets/05-bootstrap-and-validate-oidc.md)
  — first administrator ceremony plus allowed and denied live-login evidence.
- [ ] **Ticket 06:**
  [Publish tailnet ingress and portal entry](outline-tickets/06-publish-tailnet-ingress.md)
  — expose the canonical hostname and list it only after every production gate
  passes.
- [ ] **Ticket 07:**
  [Automate local backups](outline-tickets/07-automate-local-backups.md)
  — consistent database-plus-attachment sets under private `/backups` storage.
- [ ] **Ticket 08:**
  [Prove an isolated restore](outline-tickets/08-prove-isolated-restore.md)
  — seeded local restore with content, permission, key, search, and RTO evidence.
- [ ] **Ticket 09:**
  [Add encrypted off-host backups](outline-tickets/09-add-off-host-backups.md)
  — validate a provider, replicate recovery points, and restore without the
  homelab disks.
- [ ] **Ticket 10:**
  [Harden and operate Outline](outline-tickets/10-harden-and-operate-outline.md)
  — monitoring, measured resources, rotation, upgrade, rollback, retirement,
  and security evidence.

### Epic 1 exit gate

Tickets 01–05 and 07–10 are accepted, then Ticket 06 publishes Outline. The
service is production-capable only after a remote restore meets the four-hour
RTO. A running unlisted workload is not production acceptance.

## Epic 2 — Documentation Migration

- [ ] **Ticket 11:**
  [Inventory and classify documentation](outline-tickets/11-inventory-documentation.md)
  — assign every source item a disposition, owner, target, and canonical source.
- [ ] **Ticket 12:**
  [Define information architecture](outline-tickets/12-define-information-architecture.md)
  — collections, permissions, templates, lifecycle, metadata, and review rules.
- [ ] **Ticket 13:**
  [Build migration tooling](outline-tickets/13-build-migration-tooling.md)
  — dry-run inventory, idempotent import, link rewriting, attachment handling,
  secret scanning, and reports.
- [ ] **Ticket 14:**
  [Run the Penpot migration pilot](outline-tickets/14-run-penpot-pilot.md)
  — prove the migration contract on representative documentation.
- [ ] **Ticket 15:**
  [Correct the migration process](outline-tickets/15-correct-migration-process.md)
  — turn pilot findings into updated templates, tooling, and acceptance rules.
- [ ] **Ticket 16:**
  [Create remaining migration tickets](outline-tickets/16-create-remaining-migration-tickets.md)
  — instantiate one bounded issue per remaining project or document class.
- [ ] **Ticket 17:**
  [Validate the complete migration](outline-tickets/17-validate-complete-migration.md)
  — reconcile the inventory, ownership, links, exports, search, and secrets.
- [ ] **Ticket 18:**
  [Cut over canonical sources](outline-tickets/18-cut-over-canonical-sources.md)
  — replace eligible Git prose with pointers and close the migration record.

### Epic 2 exit gate

Every inventory row has a disposition and owner approval. Git retains required
machine and agent content; every moved document has one current canonical source
and a verified Outline export.

## Epic 3 — Deferred Capabilities

- [ ] **Ticket 19:**
  [Evaluate S3-compatible attachment storage](outline-tickets/19-evaluate-s3-attachment-storage.md)
  — compare migration, recovery, cost, and operating burden against the PVC.
- [ ] **Ticket 20:**
  [Deploy File Browser Quantum independently](outline-tickets/20-deploy-file-browser-quantum.md)
  — create a separate application with no write access to Outline attachments.
- [ ] **Ticket 21:**
  [Evaluate automatic admin-role reconciliation](outline-tickets/21-evaluate-admin-role-reconciliation.md)
  — decide whether manual audits remain sufficient or a supported reconciler is
  justified.
- [ ] **Ticket 22:**
  [Design the documentation agent](outline-tickets/22-design-documentation-agent.md)
  — define sources, placement, drafts, permissions, audit, conflicts, and
  secret-handling before implementation.
- [ ] **Ticket 23:**
  [Implement the approval-only documentation agent](outline-tickets/23-implement-approval-only-agent.md)
  — generate reviewable drafts without autonomous publication.
- [ ] **Ticket 24:**
  [Evaluate scoped autonomous publishing](outline-tickets/24-evaluate-scoped-autonomous-publishing.md)
  — use measured approval-only evidence to decide whether delegated direct
  publishing is safe.

## Roadmap-wide Verification

After any platform ticket that changes deployable resources:

```bash
python3 -m unittest -v tests.test_application_onboarding
python3 platform/keycloak/test_client_registration.py -v
python3 -m unittest -v tests.test_metric_allowlist
```

Render the exact Kustomize/Argo sources named by the ticket and run the
repository's configured YAML/policy checks. If a command is absent at execution
time, the ticket implementer must add a deterministic equivalent rather than
recording an unverifiable manual claim.

After each migration ticket, rerun the migration dry-run and require zero
unexplained duplicates, missing targets, broken owned links, secret findings,
or inventory rows with conflicting canonical-source status.

## Commit and Review Strategy

- Implement one ticket per branch or pull request.
- Do not combine TLE client work, Outline platform work, and migration content
  edits in one change.
- Keep the ticket number in the branch/PR title and link its evidence.
- Require a fresh review after every ticket; downstream tickets consume only
  accepted interfaces documented by their dependencies.
- A failed spike closes with evidence and a replacement recommendation. It does
  not silently expand into production implementation.
