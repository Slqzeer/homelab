# Ticket 17 — Validate the Complete Migration

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Completion of every batch created by Ticket 16
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Prove that the entire approved inventory is accounted for and that migrated
knowledge is owned, searchable, exportable, correctly linked, permissioned, and
free of leaked secrets before any final canonical-source cutover.

## Scope

- Reconcile every inventory row against its final disposition and batch report.
- Crawl owned internal links, repository permalinks, and attachment references.
- Verify collection/document permissions with representative identities.
- Find documents missing owners, lifecycle, provenance, or review dates.
- Detect duplicate canonical copies and ambiguous current/superseded status.
- Run full-workspace export including private collections under administrator
  control.
- Secret-scan source mappings, Outline exports, and migration reports.
- Obtain collection-owner sign-off for every completed batch.

## Out of scope

- Deleting or replacing Git documentation.
- Fixing a failed batch inside this validation ticket; reopen or create a
  bounded correction ticket instead.
- Declaring migration complete from item counts alone.

## Expected files and systems

- Extend the validation command in `scripts/outline_migration/`.
- Extend `tests/test_outline_migration.py`.
- Create `docs/outline-migration/final-validation.md` with redacted evidence.
- Update inventory review/sign-off fields without changing dispositions merely
  to make counts pass.

## Implementation checklist

- [ ] Add failing tests for unassigned rows, duplicate canonical targets,
  missing owner/status/review/provenance, broken links, missing attachments,
  permission drift, and secret findings.
- [ ] Run inventory-to-report reconciliation across every batch ID.
- [ ] Crawl and classify all links; distinguish intentionally external links
  from broken owned links.
- [ ] Test read/edit boundaries with member, maintainer, operator, and
  administrator identities.
- [ ] Run seeded search queries for every collection and document type.
- [ ] Export all collections including private collections and validate counts,
  hierarchy, attachments, and checksums.
- [ ] Secret-scan the export and all final reports.
- [ ] Collect named owner approval or reopen the owning batch.

## Acceptance

- Every inventory row has one final disposition and, when migrated, one mapped
  Outline document.
- No unexplained duplicate, broken owned link, missing attachment, secret
  finding, permission mismatch, or unowned current document remains.
- Search and complete export work for every collection.
- Every collection has owner sign-off and a last-reviewed policy.
- Git has not yet been destructively changed by this ticket.

## Rollback

Validation is read-only except evidence and inventory sign-off. Remove incorrect
sign-off, reopen the responsible migration batch, and keep Git canonical until
the full validation reruns cleanly.
