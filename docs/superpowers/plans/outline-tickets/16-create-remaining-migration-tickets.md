# Ticket 16 — Create Remaining Migration Tickets

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 15
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Turn the approved inventory into one issue-ready migration ticket per remaining
project or bounded document class, with no oversized catch-all migration.

## Scope

- Group remaining inventory rows only when they share an owner, target
  collection, permission boundary, and safe cutover window.
- Generate a ticket that lists exact source IDs/paths, source owner, target,
  dependencies, freeze, validation, rollback, and sign-off authority.
- Keep machine-required Git content and historical conversion work explicit.
- Establish execution order for cross-project links and shared decisions.
- Prove every remaining migratable inventory row belongs to exactly one batch.

## Out of scope

- Executing the generated migration tickets.
- Combining unrelated projects for convenience.
- Assigning ownership to administrators by default.

## Expected files and systems

- Create `docs/superpowers/plans/outline-migrations/README.md`.
- Create one dated or stably numbered Markdown ticket per approved batch under
  `docs/superpowers/plans/outline-migrations/`.
- Update `docs/outline-migration/inventory.yaml` with batch IDs and order.
- Add a validation command/test for one-to-one row-to-batch assignment.

## Required ticket template

Each generated ticket includes outcome, exact inventory selector, owner,
dependencies, edit-freeze procedure, dry run, import, validation, export,
secret scan, owner sign-off, Git treatment, rollback, and evidence location.

## Implementation checklist

- [ ] Add failing checks for missing batch IDs, duplicate batch membership,
  mixed permission boundaries, missing owners, and unresolved cross-batch links.
- [ ] Propose batches from the inventory and review them with collection owners.
- [ ] Split every batch that crosses owner, permission, or safe-cutover
  boundaries.
- [ ] Write issue-ready tickets from the required template.
- [ ] Order batches so shared platform and decision targets exist before inbound
  links are rewritten.
- [ ] Validate full inventory coverage and publish a dependency table.

## Acceptance

- Every remaining `Outline-canonical`, decision-conversion, or archive row is in
  exactly one executable migration ticket.
- No ticket mixes unrelated owners or permissions.
- Every ticket names exact acceptance evidence and rollback behavior.
- Git-canonical, generated, excluded, and duplicate/noise rows are not imported
  accidentally.
- The final-validation ticket can enumerate all generated batch IDs
  deterministically.

## Rollback

No content is migrated. Revert incorrect batch definitions, clear their IDs
from the inventory, and regenerate only after owners approve the corrected
boundaries.
