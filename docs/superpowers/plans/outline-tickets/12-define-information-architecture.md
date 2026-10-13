# Ticket 12 — Define Information Architecture

**Epic:** Documentation migration
**Status:** Planned
**Depends on:** Ticket 11
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Create the approved Outline collections, permission model, templates, metadata,
lifecycle states, ownership rules, and review policy that all migrations use.

## Scope

- Create `Homelab Platform`, per-project/domain, `Decisions`, and `Archive`
  collection structure.
- Grant ordinary admitted users read access, scoped maintainers edit access,
  platform operators operational ownership, and administrators workspace
  management only.
- Define controlled document types and lifecycle vocabulary.
- Create the nine templates required by the spec.
- Define ownership, last-review, provenance, canonical-source, repository-link,
  and migration-date metadata conventions supported by Outline.
- Map Ticket 11 inventory targets to approved collections and owners.

## Out of scope

- Importing source documents.
- Granting coding agents access.
- Inventing a fully controlled tag taxonomy.

## Expected files and systems

- Create `docs/outline-migration/information-architecture.md`.
- Create `docs/outline-migration/templates/` with one Markdown source per
  template for review and repeatability.
- Update `docs/outline-migration/inventory.yaml` target/owner fields.
- Create the matching Outline collections/templates through a controlled human
  or API process and record non-secret IDs in a mapping file.

## Implementation checklist

- [ ] Add schema checks for allowed collection classes, document types,
  lifecycle values, required metadata, owners, and permission classes.
- [ ] Draft the hierarchy and map every inventory target; reject orphaned or
  catch-all destinations.
- [ ] Write project overview, architecture, deployment/operations, runbook,
  troubleshooting, ADR, onboarding, reference, and archive templates.
- [ ] Create collections with least-privilege permissions and named owners.
- [ ] Create templates and verify required metadata is visible and usable.
- [ ] Test read/edit denial with representative member, maintainer, operator,
  and administrator accounts.
- [ ] Record collection/template IDs and permissions without tokens or user
  secrets.

## Acceptance

- Every migratable inventory item has one valid target collection and owner.
- All admitted users can read ordinary documentation; only scoped maintainers
  can edit project collections.
- Operational and sensitive collections enforce their explicit audience.
- Templates contain the required project fields and accept explicit
  `Not applicable` values.
- Rejected, superseded, archived, and current material are visually and
  semantically distinguishable.

## Rollback

Do not delete collections containing user-authored content. For empty setup
errors, remove the incorrect collection/template and restore the mapping file.
Inventory remains unchanged until the corrected hierarchy is approved.
