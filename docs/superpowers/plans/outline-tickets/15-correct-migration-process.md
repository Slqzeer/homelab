# Ticket 15 — Correct the Migration Process

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 14
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Convert every Penpot pilot finding into an explicit template, inventory,
tooling, permission, or acceptance-rule correction and prove the corrected
process on the pilot before scaling migration.

## Scope

- Triage every pilot defect or manual intervention.
- Fix systemic issues in templates, transforms, API behavior, link rules,
  attachments, reports, permissions, or review instructions.
- Add a regression test for every tooling defect.
- Re-run Penpot from a clean disposable target and compare results.
- Freeze the migration contract used by downstream batch tickets.

## Out of scope

- Adding unrelated migration features not exposed by the pilot.
- Beginning remaining project migrations before corrections pass.
- Hiding accepted limitations in operator notes only.

## Expected files and systems

- Modify `scripts/outline_migration/`, its tests, fixtures, templates,
  information architecture, inventory schema, and migration README as findings
  require.
- Create `docs/outline-migration/pilot-findings.md` with disposition and evidence.
- Update Penpot reports/mapping after the clean rerun.

## Implementation checklist

- [ ] List every pilot finding with severity, owner, systemic/local class, and
  chosen disposition.
- [ ] Write a failing regression test for each systemic tooling defect.
- [ ] Update templates, permissions, metadata, or review rules for non-code
  defects and add schema checks where possible.
- [ ] Implement the smallest corrections and run all migration tests.
- [ ] Delete the disposable target, rerun the complete Penpot plan/import/
  validate cycle, and compare reports with the accepted pilot.
- [ ] Document any accepted limitation and the manual acceptance evidence it
  requires in every downstream batch.
- [ ] Obtain owner approval for the frozen migration contract.

## Acceptance

- Every pilot finding has an explicit disposition; none is silently deferred.
- Every fixed tooling defect has a regression test.
- A clean Penpot rerun passes without undocumented manual repair.
- Templates, schema, reports, and acceptance checklists agree.
- Downstream ticket authors can identify all mandatory steps from checked-in
  documentation alone.

## Rollback

Revert individual corrections that regress the clean pilot, restore the prior
accepted tooling release, and keep remaining migrations blocked. Penpot stays
Git-canonical until the corrected process passes again.
