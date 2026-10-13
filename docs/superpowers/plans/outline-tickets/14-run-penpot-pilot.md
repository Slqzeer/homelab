# Ticket 14 — Run the Penpot Migration Pilot

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 13
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Migrate Penpot's representative documentation into its approved Outline
collection, validate the complete migration contract, and leave Git canonical
until the owner explicitly signs off.

## Scope

- Freeze only the Penpot documentation batch at a recorded source commit.
- Dry-run and review its inventory selection and target hierarchy.
- Import architecture, deployment, Keycloak, recovery, operational caveats,
  decisions, code blocks, links, and attachments.
- Validate permissions, metadata, search, export, and secret scanning.
- Obtain named owner sign-off.
- Exercise rollback before changing any Git source.

## Out of scope

- Migrating other applications.
- Removing or replacing Git documents before Ticket 15 accepts pilot findings.
- Treating pilot-specific manual edits as undocumented tooling behavior.

## Expected files and systems

- Update Penpot rows in `docs/outline-migration/inventory.yaml`.
- Add a bounded pilot selector/config under `docs/outline-migration/pilots/`.
- Store redacted import/validation evidence under the migration documentation
  path.
- Do not modify Penpot source docs during this ticket except to correct a
  confirmed source defect through its own reviewed change.

## Implementation checklist

- [ ] Record the source commit and announce the bounded edit freeze.
- [ ] Run the migration plan and have the owner approve destinations,
  permissions, and lifecycle values.
- [ ] Import with a new run ID and retain the mapping/report.
- [ ] Validate every heading, table, code fence, diagram, attachment, internal
  link, repository link, metadata field, and permission boundary.
- [ ] Search for seeded terms and export the complete Penpot collection.
- [ ] Run secret scanning on source, transformed content, reports, and export.
- [ ] Exercise batch rollback in a disposable copy, then restore/import again.
- [ ] Collect owner sign-off and reopen editing only after the source commit and
  results are recorded.

## Acceptance

- Every Penpot inventory row is accounted for with no duplicate document.
- Formatting, links, attachments, metadata, permissions, search, and export
  pass the Ticket 13 validator.
- No secret or machine-required Git content was moved incorrectly.
- Owner sign-off names the accepted hierarchy and any remaining defects.
- Git remains canonical pending Ticket 15 process correction and later cutover.

## Rollback

Use the run mapping to remove or archive only pilot-created documents. Revoke
the pilot token, unfreeze Git, and mark all Penpot inventory rows as
Git-canonical until a corrected pilot passes.
