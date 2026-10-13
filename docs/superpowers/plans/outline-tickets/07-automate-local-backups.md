# Ticket 07 — Automate Local Backups

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 02 and 03
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Create daily, private, application-consistent Outline recovery sets under
`/backups/services/outline`, with validation, retention, atomic publication,
and failure alerts.

## Scope

- Run a host-side systemd service/timer. The service acquires a host lock,
  temporarily pauses Argo self-heal for the Outline Application, scales the
  Outline Deployment to zero, and waits until no Outline pod is running.
- With Outline quiescent, stream `pg_dump` from PostgreSQL and stream a
  read-only attachment archive from a short-lived restricted helper pod that
  mounts `outline-data`; neither Kubernetes pod mounts host `/backups`.
- Use a trap to restore one replica and Argo self-heal on success, failure,
  interruption, or validation error.
- Produce a custom-format PostgreSQL dump, attachment archive, checksums, and a
  manifest tying them to the Outline release and runbook version.
- Publish only complete validated sets using private temporary paths and atomic
  rename.
- Retain 14 daily, 8 weekly, and 12 monthly recovery points.
- Prevent the Outline pod from reading, changing, or deleting backups.
- Keep all `/backups` writes in the host-side process; the application and
  helper pods never receive the host path.
- Alert on failure and on recovery-point age greater than 24 hours.

## Out of scope

- Off-host replication.
- Restoring production in place.
- Treating a workspace export as a full backup.

## Expected files and systems

- Create `apps/outline/operations/backup-outline.sh`.
- Create operator-installable systemd service/timer templates under
  `apps/outline/operations/`.
- Create `tests/test_outline_backup.py`.
- Update `docs/runbooks/outline-recovery.md`.
- Add alert configuration under the repository's monitoring ownership path.

## Interfaces

- Produces immutable-by-convention recovery-set directories consumed by
  Tickets 08 and 09.
- Each manifest exposes release, timestamps, dump/archive filenames, byte sizes,
  hashes, backup-script revision, and validation results without secrets.

## Implementation checklist

- [ ] Write failing tests for `umask 077`, private directory modes, host lock,
  self-heal pause/restore, scale-down/up, zero-running-pod confirmation,
  read-only helper mount, temporary naming, dump/archive validation, checksums,
  manifest schema, atomic publication, retention, and failure cleanup.
- [ ] Implement the host-side producer and systemd service/timer with a trap
  that restores the Deployment replica and Argo self-heal state.
- [ ] Validate PostgreSQL completeness and archive readability before publish.
- [ ] Seed a document and attachment, run the backup twice, and prove no partial
  set is visible during a forced failure.
- [ ] Exercise daily/weekly/monthly retention against fixed test timestamps.
- [ ] Add freshness/failure alerting and record installation/ownership steps.
- [ ] Verify the Outline Service account and pod cannot reach `/backups`.

## Acceptance

- A scheduled run creates one complete private set and no plaintext secrets.
- Database and attachments share a proven quiescent boundary.
- Failed runs preserve the last good recovery point and publish nothing partial.
- Forced failure at every phase restores one Outline replica and the original
  Argo self-heal setting.
- Retention keeps exactly the agreed daily, weekly, and monthly classes.
- Backup age over 24 hours and a failed scheduled run alert.
- The operation can be reinstalled from Git plus documented operator steps.

## Rollback

Disable the timer/schedule before removing its scripts. Preserve all complete
recovery sets and the last failure logs. Re-enable manual backup procedures
until corrected automation passes the same tests.
