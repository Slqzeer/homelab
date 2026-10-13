# Ticket 19 — Evaluate S3-compatible Attachment Storage

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Ticket 06 and stable production measurements
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Decide, with a reversible migration proof, whether Outline attachments should
remain on the local PVC or move to an S3-compatible service.

## Scope

- Measure attachment size, growth, upload/download behavior, backup duration,
  restore duration, and PVC operating burden.
- Evaluate at least a managed S3-compatible option and a self-hosted option.
- Confirm Outline compatibility for upload method, private ACLs, CORS,
  presigned URLs, path style, limits, and supported release behavior.
- Prototype migration on a restored non-production Outline instance.
- Compare security, availability, cost, backup, provider exit, and operational
  load against keeping local storage.
- Produce an adopt/defer/reject decision with evidence.

## Out of scope

- Using File Browser Quantum, WebDAV, or a shared filesystem as an S3 API.
- Migrating production attachments before a separately approved implementation
  plan.
- Selecting a service merely because it already has a web file browser.

## Expected files and systems

- Create a dated decision record under `docs/outline-migration/decisions/` or
  the repository's chosen ADR location.
- Update `docs/runbooks/outline-recovery.md` only after a decision changes the
  recovery model.
- Use disposable buckets and restored data for the prototype.

## Implementation checklist

- [ ] Capture current local-storage and recovery measurements.
- [ ] Define weighted criteria before evaluating providers.
- [ ] Verify official Outline storage semantics for the pinned release.
- [ ] Configure disposable managed and self-hosted candidates with private
  access and exact CORS.
- [ ] Migrate a restored attachment set, validate checksums and document links,
  then exercise rollback to local storage.
- [ ] Compare complete backup/restore and credential-compromise behavior.
- [ ] Publish the decision and a separate implementation-plan requirement when
  adoption wins.

## Acceptance

- The decision includes measured current-state costs and at least two credible
  candidates.
- Prototype migration and rollback preserve all seeded attachment checksums.
- Backup/restore covers database plus object state as one logical set.
- Provider exit and credential rotation are explicit.
- “Keep local PVC” is an acceptable result when S3 adds more burden than value.

## Rollback or abandonment

Delete disposable buckets/credentials after preserving non-secret evidence. A
failed candidate produces a rejection record, not a partially migrated
production service.
