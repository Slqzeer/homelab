# Ticket 22 — Design the Specialized Documentation Agent

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Ticket 18 and stable post-migration governance
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Produce and approve a dedicated architectural specification for an agent that
places concise, accurate documentation in the correct Outline location without
erasing or duplicating information that belongs in Git or another system.

## Scope

- Define user journeys, authorized sources, collection routing, document types,
  metadata, lifecycle, ownership, and approval workflow.
- Define what the agent must leave in Git and what it may propose for Outline.
- Start with draft/proposal creation only; no autonomous publication.
- Define least-privilege identity, credential rotation, audit, prompt/input
  trust boundaries, secret filtering, conflict detection, duplicate detection,
  rollback, rate limits, and failure handling.
- Define evaluation fixtures and quantitative readiness gates.
- Compare direct Outline API use, an intermediate review queue, and a dedicated
  service boundary.

## Out of scope

- Implementing the agent.
- Granting workspace-admin access.
- Allowing the agent to edit/delete repository context.
- Assuming arbitrary repository or user content is trusted instructions.

## Expected files and systems

- Create a dated design spec under `docs/superpowers/specs/`.
- After written-spec approval, create a separate implementation plan for Ticket
  23.
- Create only read-only/disposable probes before design approval.

## Design checklist

- [ ] Inventory concrete documentation tasks and failure costs.
- [ ] Define canonical-source and placement decision rules with counterexamples.
- [ ] Define the approval queue and owner interaction.
- [ ] Define interfaces for sources, classifier/router, draft generator, Outline
  adapter, audit log, and rollback.
- [ ] Threat-model prompt injection, secrets, malicious documents, excessive
  permissions, duplicate writes, and stale context.
- [ ] Define test corpora for correct placement, concision, provenance, secret
  rejection, conflicts, and unsupported actions.
- [ ] Present approaches and obtain section-by-section design approval.
- [ ] Write, self-review, and obtain approval for the specification and plan.

## Acceptance

- The approved spec names exact boundaries and supported workflows.
- The agent has no workspace-admin or repository-write capability.
- Every draft has provenance, target, owner, diff/preview, and rollback data.
- Secret/prompt-injection failures are fail-closed and auditable.
- Metrics define when approval-only operation is successful enough for Ticket
  24 evaluation.

## Rollback or abandonment

This ticket creates no production agent. If a safe permission or audit model
cannot be designed, record the rejected approaches and keep documentation human
managed.
