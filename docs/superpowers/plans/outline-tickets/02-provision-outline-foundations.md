# Ticket 02 — Provision Outline Foundations

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Ticket 01
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Create the durable and secret foundations Outline needs: a restricted namespace,
least-privilege Vault/VSO wiring, an isolated PostgreSQL role/database, a
dedicated ephemeral Redis, and a 10 GiB attachment PVC.

## Scope

- Declare namespace `outline` once with restricted Pod Security labels.
- Generate and preserve Outline, utility, PostgreSQL, Redis, and OIDC secrets in
  Vault without overwriting existing values.
- Create namespace-local Vault authentication and exact projections.
- Create the `outline` PostgreSQL role/database idempotently and reconcile its
  password without exposing it in SQL or argv.
- Deploy a dedicated, authenticated, single-replica Redis with no durable claim.
- Create the RWO local-path attachment claim.
- Define rotation annotations or restart behavior for each consumer.

## Out of scope

- The Outline Deployment and Service.
- Keycloak client or authentication-flow mutation.
- Backup, ingress, monitoring, or migration.

## Expected files and systems

- Modify `bootstrap/namespaces/namespaces.yaml`.
- Modify `platform/vault/configure-vault.sh`.
- Create `apps/outline/config/vault.yaml`.
- Create `apps/outline/config/postgres-job.yaml`.
- Create `apps/outline/config/redis.yaml`.
- Create `apps/outline/config/storage.yaml`.
- Create or extend `apps/outline/config/kustomization.yaml`.
- Add focused fixtures/assertions under `tests/`.

## Interfaces

- Produces Kubernetes Secrets whose exact names and keys are documented for
  Tickets 03, 04, 07, and 09.
- Produces Service `outline-redis` on TCP/6379.
- Produces PVC `outline-data` mounted later at `/var/lib/outline/data`.
- Produces database `outline`, role `outline`, and an explicit pool contract of
  minimum one and maximum five connections.

## Implementation checklist

- [ ] Add failing tests for namespace labels, Vault path isolation,
  `excludeRaw: true`, projected keys, secret preservation, database idempotency,
  Redis isolation, and PVC size/access mode.
- [ ] Add the namespace and durable-state annotations expected by the onboarding
  validator.
- [ ] Add idempotent Vault seeding using private temporary files and field-level
  updates so rotations do not erase unrelated keys.
- [ ] Add namespace-local VSO identity and projections; keep Keycloak's OIDC
  projection separately owned for Ticket 04.
- [ ] Add the database role/bootstrap Job using `psql` variables or `\getenv`,
  never interpolated credentials.
- [ ] Add restricted-compatible Redis Service/Deployment and the 10 GiB PVC.
- [ ] Render the foundation resources and make all tests pass.

## Acceptance

- A second Vault/bootstrap and Argo reconciliation performs no destructive
  secret or database reset.
- Secret values appear in none of Git, rendered ConfigMaps, command arguments,
  logs, or test output.
- Outline has no access to another application's Vault path or Redis.
- PostgreSQL connection capacity remains explicitly bounded for Outline.
- Redis loss is documented as reconstructible; attachment PVC loss is durable
  data loss requiring restore.
- The namespace and every pod template pass restricted admission checks.

## Rollback

Remove only the new manifests and Vault policies/roles. Preserve Vault values,
the `outline` database, and `outline-data` PVC until an explicit data-retention
decision is recorded.
