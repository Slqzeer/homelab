# Ticket 03 — Deploy the Outline Workload

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 01 and 02
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Deploy an unlisted, tailnet-unpublished Outline workload that starts against its
real PostgreSQL, Redis, Vault projections, and attachment PVC while remaining
isolated by default-deny network policy.

## Scope

- Add the pinned single-replica Deployment with `Recreate` strategy.
- Mount local attachments at `/var/lib/outline/data` and secrets through their
  file-backed or secret-key interfaces.
- Configure URL, database pool minimum/maximum, Redis, local storage, log level,
  and disabled optional integrations.
- Add Service and distinct startup/readiness/liveness probes.
- Add default-deny policies plus only DNS, PostgreSQL, Redis, and later-Keycloak
  flows needed before ingress.
- Add Kustomize and the durable child Argo Application with the correct wave.
- Extend onboarding validation fixtures.

## Out of scope

- Keycloak client creation or live login.
- Tailscale Ingress or portal metadata.
- Backup and monitoring beyond workload health.

## Expected files and systems

- Create `apps/outline/config/deployment.yaml`.
- Create `apps/outline/config/service.yaml`.
- Create `apps/outline/config/networkpolicy.yaml`.
- Modify `apps/outline/config/kustomization.yaml`.
- Create `environments/homelab/apps/outline.yaml`.
- Modify `tests/test_application_onboarding.py` and focused Outline tests.

## Interfaces

- Consumes all Secret, Service, database, Redis, and PVC names from Ticket 02.
- Produces Service `outline` with only the named user-facing HTTP port for
  Tickets 06 and 10.
- Produces exact pod labels consumed by Keycloak ingress policy, Tailscale
  ingress, backup orchestration, and monitoring.

## Implementation checklist

- [ ] Add failing render tests for exact image tag/digest, one replica,
  `Recreate`, resources, probes, pool bounds, secret references, mounts, Service,
  Argo annotations, and prohibited optional configuration.
- [ ] Add the Deployment and Service from Ticket 01's release contract.
- [ ] Add default-deny ingress/egress and port/peer-specific dependency flows.
- [ ] Add Kustomize and the wave-ordered Argo Application with prune, self-heal,
  and server-side apply.
- [ ] Render the complete app and run onboarding and policy tests.
- [ ] Sync only after existing Argo applications and dependencies are healthy.
- [ ] Record startup, migration, probe, restart-persistence, and denied-network
  evidence without publishing a user-facing route.

## Acceptance

- Argo reports the child Application healthy and synced.
- Exactly one restricted Outline pod becomes ready using the pinned image.
- Database migrations complete once and a restart remains healthy.
- A seeded attachment survives pod restart.
- DNS, PostgreSQL, and Redis flows work; arbitrary namespace and Internet flows
  are denied.
- No ingress or portal card exists.

## Rollback

Revert the child Application and workload manifests. Preserve the database,
Vault values, and PVC. If startup migrations changed the schema, follow the
release contract and restore the pre-sync database rather than merely deploying
an older image.
