# Ticket 11 — Inventory and Classify Documentation

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 06 (platform production acceptance)
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Produce a complete, reviewable inventory in which every documentation source has
an owner, sensitivity, target, and canonical-source disposition.

## Scope

- Enumerate tracked Markdown/README files and human knowledge in other tracked
  formats from the main worktree.
- Exclude `.superpowers/worktrees/**`, generated artifacts, secret material, and
  transient agent progress.
- Record size, links, attachments, document type, lifecycle, owner, target
  collection, permission class, migration dependencies, and current canonical
  source.
- Classify each item as Outline-canonical, Git-canonical, decision conversion,
  archive, generated/excluded, or duplicate/noise.
- Identify required checked-in recovery and agent-context files that cannot be
  replaced by Outline.

## Out of scope

- Importing or deleting content.
- Designing final collection hierarchy beyond recording candidate targets.
- Treating YAML/JSON source as migratable merely because it contains text.

## Expected files and systems

- Create `docs/outline-migration/inventory.yaml` or an equivalently diffable,
  machine-readable inventory.
- Create `docs/outline-migration/README.md` describing schema and review rules.
- Create `scripts/outline_inventory.py` and focused unit tests if generation is
  automated.
- Add a generated human-readable report only when its regeneration is
  deterministic.

## Inventory interface

Every row has stable source ID/path, source commit, owner, document type,
lifecycle, sensitivity, links/attachments, target collection/path, permission
class, disposition, canonical source before/after, and review status.

## Implementation checklist

- [ ] Define the inventory schema and add failing tests for required fields,
  unique source IDs, valid dispositions, and exclusion rules.
- [ ] Generate the initial inventory from a clean main worktree and fixed source
  commit.
- [ ] Add manually curated non-Markdown knowledge candidates without copying
  configuration or secrets.
- [ ] Mark mandatory Git-owned runbooks, configuration contracts, generated
  artifacts, and agent context.
- [ ] Run secret scanning on paths and sampled content before any migration
  classification is accepted.
- [ ] Assign one accountable owner and review status to every migratable item.
- [ ] Review duplicate and historical classifications; preserve useful rejected
  reasoning rather than raw project-management noise.

## Acceptance

- Every in-scope tracked source appears exactly once or has an explicit tested
  exclusion.
- No row has a missing owner, disposition, sensitivity, or canonical-source
  result.
- Machine-required and agent-required files remain Git-canonical.
- Important rejected/superseded decisions are marked for decision conversion or
  archive, not deletion.
- The inventory can be regenerated without nondeterministic row churn.

## Rollback

This ticket changes no source documentation or Outline content. Revert the
inventory/tooling if its schema is wrong; the repository remains canonical.
