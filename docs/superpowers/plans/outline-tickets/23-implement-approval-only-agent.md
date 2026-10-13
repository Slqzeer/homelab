# Ticket 23 — Implement the Approval-only Documentation Agent

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Approved Ticket 22 design and implementation plan
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Deploy the specialized agent in approval-only mode so it can analyze authorized
sources and prepare correctly placed drafts, while a named human owner remains
the only publisher.

## Scope

- Implement only the interfaces and workflows approved by Ticket 22.
- Use a dedicated non-admin Outline identity and separate Vault credentials.
- Produce draft content, placement rationale, provenance, metadata, and a
  reviewable diff/preview.
- Require owner approval before any publication operation.
- Fail closed on secrets, ambiguous canonical source, unowned target, placement
  uncertainty, conflict, duplicate, stale source, or malicious instructions.
- Emit redacted audit events and operational metrics.
- Evaluate against the approved corpus before exposing real proposals.

## Out of scope

- Autonomous publishing.
- Workspace administration or permission management.
- Repository writes/deletes.
- Reusing migration credentials.

## Expected files and systems

- Defined by the separately approved Ticket 22 implementation plan.
- Include focused unit/integration/adversarial tests, Vault/VSO resources,
  least-privilege network policy, operational runbook, and evaluation reports.
- Deployment follows the personal-application onboarding contract when it runs
  as a cluster service.

## Implementation checklist

- [ ] Follow the approved plan test-first, preserving its exact interfaces and
  task review gates.
- [ ] Prove the Outline identity cannot administer the workspace or publish
  outside the draft/approval workflow.
- [ ] Run the full placement, concision, provenance, secret, injection,
  duplicate, conflict, stale-source, and rollback corpus.
- [ ] Deploy behind default-deny policy with only approved source and Outline
  flows.
- [ ] Rotate credentials and verify redacted audit continuity.
- [ ] Run a controlled proposal period with owners and record accept/edit/reject
  outcomes.

## Acceptance

- No content is published without an authenticated named owner approval.
- Every proposal carries target, owner, provenance, canonical-source result,
  metadata, and diff/preview.
- Secret, injection, ambiguity, and conflict cases produce no draft write and an
  actionable redacted audit event.
- The identity cannot administer Outline or modify Git.
- Evaluation and live proposal outcomes are retained for Ticket 24.

## Rollback

Disable the workload and revoke only its dedicated credentials. Drafts remain
unpublished or are archived by owners. No canonical document or repository file
requires reversal because the agent never publishes autonomously.
