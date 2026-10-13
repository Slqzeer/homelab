# Ticket 21 — Evaluate Automatic Admin-role Reconciliation

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Ticket 06 and at least one completed manual administrator audit
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Decide whether manual Outline administrator management remains adequate or a
supported, narrowly scoped reconciler should mirror `homelab-admins` into
workspace roles.

## Scope

- Measure administrator count, change frequency, audit burden, and role drift.
- Recheck current Outline release/API support for listing users and changing
  workspace roles.
- Prototype against a disposable restored workspace when a supported API exists.
- Define removal behavior that preserves authored content and never removes the
  last recoverable administrator.
- Compare a scheduled reconciler, event-driven integration, continued manual
  audit, and an Outline fork.
- Produce an adopt/defer/reject decision and implementation plan if needed.

## Out of scope

- Patching/forking Outline in this ticket.
- Mapping project/collection edit permissions automatically.
- Deleting users or authored documents when a Keycloak group changes.

## Expected files and systems

- Create a dated role-reconciliation decision record.
- Update the administrator-audit section of
  `docs/runbooks/outline-recovery.md`.
- Use disposable credentials/workspace for any prototype.

## Implementation checklist

- [ ] Run and record a manual audit comparing Keycloak `homelab-admins` with
  Outline administrators.
- [ ] Verify current official API and permission behavior from primary sources.
- [ ] Define invariants for eligibility, last-admin safety, offboarding,
  suspension, audit, and rollback.
- [ ] Prototype the smallest non-fork option against restored data.
- [ ] Test add, remove, unchanged, API failure, missing group, and last-admin
  cases without deleting content.
- [ ] Compare ongoing complexity and security with a six-month/manual-on-change
  audit.
- [ ] Publish the decision and, when adopted, require a separate implementation
  plan.

## Acceptance

- The decision is based on observed drift/burden and current supported APIs.
- No option grants broad Keycloak or Outline administrator credentials to an
  unrelated workload.
- Removal preserves content and last-admin recovery.
- Continued manual audit is accepted explicitly when automation is not
  justified.
- Maintaining an Outline fork remains rejected unless separately approved.

## Rollback or abandonment

Delete disposable prototype credentials and data. If automation is rejected,
retain the documented manual audit and promotion/demotion procedure.
