# Ticket 13 — Build Migration Tooling

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Tickets 11 and 12
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Build a testable, idempotent importer that turns approved inventory items into
Outline documents while preserving structure, attachments, links, metadata,
permissions, provenance, and a complete audit report.

## Scope

- Parse the inventory and support dry-run planning without Outline writes.
- Maintain a durable source-ID to Outline-document mapping.
- Create or update owned documents without creating duplicates.
- Rewrite in-batch links and preserve repository permalinks for Git-owned
  targets.
- Preserve headings, tables, fenced code, and supported diagrams.
- Upload attachments with checksum validation.
- Apply approved collection, hierarchy, metadata, owner, and lifecycle values.
- Run secret detection before sending content.
- Emit machine-readable and human-readable reports with no content secrets.
- Authenticate through a dedicated, revocable Vault-backed migration token.

## Out of scope

- Deleting or replacing Git source files.
- Migrating every project in this ticket.
- Reusing migration credentials for a future agent.

## Expected files and systems

- Create a focused package under `scripts/outline_migration/`.
- Create `tests/test_outline_migration.py` and representative fixtures under
  `tests/fixtures/outline-migration/`.
- Create `docs/outline-migration/mapping.json` with non-secret stable IDs.
- Document CLI and report schemas in `docs/outline-migration/README.md`.
- Add a dedicated Vault path/identity only when the live pilot is ready.

## Tool interface

The CLI provides at least `plan`, `import`, and `validate` commands; accepts an
inventory path, source commit, bounded selector, mapping path, and report path;
and returns non-zero for secret findings, ambiguous targets, broken owned links,
duplicate mappings, or partial writes.

## Implementation checklist

- [ ] Write failing tests for inventory parsing, dry run, deterministic plans,
  create/update idempotency, duplicate detection, link rewriting, formatting,
  attachments, metadata, secret rejection, partial API failure, and redacted
  reports.
- [ ] Implement the pure plan/transform layer without network access.
- [ ] Implement an Outline API adapter behind a fakeable interface and cover it
  with recorded-schema fixtures rather than live secrets.
- [ ] Implement transactional-enough batch behavior: incomplete writes are
  reported and clearly marked, never silently accepted.
- [ ] Implement mapping and validation reports with stable ordering.
- [ ] Add Vault-backed credential loading without placing token values in argv,
  logs, fixtures, or reports.
- [ ] Run the full suite twice against a disposable collection and prove the
  second import creates no duplicates.

## Acceptance

- Dry run makes no API mutations and is deterministic.
- Re-import updates only the mapped owned document.
- Broken links, ambiguous mappings, missing attachments, and secret findings
  fail before cutover.
- API failures leave an actionable report and never mark the batch accepted.
- Reports identify every source item and destination without leaking document
  bodies or tokens.
- The migration token can be revoked without affecting later agents or users.

## Rollback

Delete only documents created by the failed batch and identified by its mapping
and run ID. Never delete pre-existing or human-authored documents. Revoke the
migration token and keep Git canonical until validation passes.
