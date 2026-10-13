# Ticket 18 — Cut Over Canonical Sources

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 17
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Make the validated Outline documents canonical, replace eligible repository
copies with concise pointers, preserve required Git-owned content, and close the
migration with a reversible record.

## Scope

- Apply each inventory row's approved post-migration Git treatment.
- Keep machine-enforced recovery runbooks, configuration contracts, generated
  references, and agent context canonical in Git.
- Replace discoverability-sensitive migrated prose with a short canonical
  Outline URL and ownership marker where approved.
- Remove only validated duplicate/noise content.
- Re-run repository tests and Outline final validation after Git changes.
- Record the cutover commit and final workspace export/recovery point.
- End migration credentials and edit freezes.

## Out of scope

- Moving mandatory checked-in runbooks solely into Outline.
- Rewriting project-local agent context automatically.
- Keeping full duplicate prose “temporarily” without a dated rollback reason.

## Expected files and systems

- Modify or delete only files explicitly approved by the inventory and owner.
- Update `docs/outline-migration/inventory.yaml` final canonical-source fields.
- Create `docs/outline-migration/cutover-record.md`.
- Revoke the Vault-backed migration token/identity after final export.

## Implementation checklist

- [ ] Generate an exact dry-run Git change list from accepted inventory rows and
  have owners approve it.
- [ ] Take a fresh Outline backup and complete workspace export.
- [ ] Replace eligible documents with pointers or remove approved duplicates in
  small project-owned commits.
- [ ] Preserve and validate every machine/agent/configuration-coupled Git file.
- [ ] Run repository tests, link checks, migration validation, export validation,
  and secret scans after the changes.
- [ ] Record the source commits, Outline mapping/export, rollback commits, and
  owner sign-offs.
- [ ] Revoke migration credentials and remove temporary freezes.

## Acceptance

- Every document has exactly one declared canonical source.
- Required Git runbooks, contracts, generated artifacts, and agent context still
  pass their consumers and tests.
- Every Git pointer opens the correct permitted Outline document.
- No unapproved source file is removed.
- A final export and service recovery point exist before the cutover commits.
- Migration credentials are revoked and the migration is marked closed.

## Rollback

Revert the bounded cutover commits to restore Git prose, mark affected inventory
rows Git-canonical, and keep the Outline copies archived/non-current. Never use
history-rewriting Git operations for rollback.
