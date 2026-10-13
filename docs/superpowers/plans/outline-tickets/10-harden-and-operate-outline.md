# Ticket 10 — Harden and Operate Outline

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 03, 04, and 07
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Turn the functional workload into an operable service with measured resources,
useful alerts, safe secret rotation, documented upgrades/rollback/retirement,
and a completed security review.

## Scope

- Measure idle, login, import, search, attachment upload/download, and
  representative collaborative-edit workloads.
- Commit resource requests/limits from evidence and verify PostgreSQL pool
  consumption.
- Monitor availability, readiness, restarts, saturation, PVC usage, backup age,
  replication status, and restore-drill age.
- Add an Outline ServiceMonitor only if the pinned release exposes a supported,
  useful, non-sensitive metrics endpoint.
- Verify centralized logs exclude secrets, auth data, and document bodies.
- Exercise rotation for each Vault-projected secret.
- Document monthly upgrade review, pre-upgrade backup, schema-aware rollback,
  maintenance, retirement, and final-backup procedures.

## Out of scope

- Creating dashboards without an actionable signal.
- Automatic version upgrades.
- High availability or horizontal scaling.

## Expected files and systems

- Modify `apps/outline/config/deployment.yaml` and network policy.
- Create monitoring targets/rules under `observability/monitoring/` only for
  supported signals.
- Modify `scripts/sync_metric_allowlist.py` and
  `tests/test_metric_allowlist.py` if new series are exported.
- Extend Outline/onboarding tests.
- Complete `docs/runbooks/outline-recovery.md`.

## Implementation checklist

- [ ] Add failing assertions for required workload, storage, backup, and
  endpoint alerts and for any new metrics allowlist.
- [ ] Capture reproducible resource measurements and whole-server PostgreSQL
  connection counts under each defined workload.
- [ ] Set requests/limits and pool values from the measurements without
  exceeding the spec's shared-server budget.
- [ ] Add only supported monitoring and alert resources; test each alert by
  inducing its condition.
- [ ] Search centralized logs during login/import/upload for forbidden content
  and correct configuration before recording evidence.
- [ ] Rotate application, database, Redis, and OIDC credentials one at a time;
  prove the intended consumer restarts/converges and other consumers do not.
- [ ] Rehearse a pinned upgrade with a fresh backup and classify rollback based
  on schema compatibility.
- [ ] Document retirement order and a final-backup gate.

## Acceptance

- Resource and pool settings cite dated measurements.
- Alerts fire for unavailable workload, restart loop, resource/PVC pressure,
  stale/failed backup, failed replication, and overdue restore drill.
- No unsupported or permanently-down ServiceMonitor is committed.
- Secret rotation succeeds without leakage or cross-namespace access.
- The runbook gives exact upgrade, rollback, backup, restore, and retirement
  decisions; it never implies image rollback reverses schema changes.

## Rollback

Revert one operational control at a time. Never remove backup or availability
alerts merely because they fire. Restore previous measured resource values and
the previous compatible release/data pair when an upgrade fails.
