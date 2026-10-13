# Ticket 24 — Evaluate Scoped Autonomous Publishing

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Ticket 23 plus at least 30 days and 20 reviewed real proposals
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Use measured approval-only evidence to decide whether the documentation agent
may publish directly inside explicitly delegated collections while continuing
to propose changes everywhere else.

## Scope

- Analyze placement, factual, concision, metadata, duplication, secret,
  conflict, edit-distance, rejection, rollback, and owner-response outcomes.
- Define collection-specific risk and owner willingness.
- Run shadow autonomous decisions without publication and compare them with
  human outcomes.
- Threat-test direct-write permissions, compromised credentials, prompt
  injection, stale inputs, and conflicting human edits.
- Decide separately for each candidate collection: adopt, continue approval
  only, or reject autonomy.
- Require a new implementation plan for any adopted direct-publish capability.

## Out of scope

- Workspace-wide autonomous publishing.
- Workspace-admin access.
- Automatic Git modification or deletion.
- Treating low proposal volume as evidence of safety.

## Expected files and systems

- Create a dated evaluation/decision record.
- Update the Ticket 22 agent spec and runbook only after a decision is approved.
- Keep shadow-mode outputs separate from canonical Outline documents.

## Evaluation checklist

- [ ] Verify the evidence window includes at least 30 days and 20 reviewed real
  proposals across every candidate document type.
- [ ] Compute accept-without-edit, accept-with-edit, reject, misplacement,
  duplicate, secret-block, conflict, and rollback rates.
- [ ] Review every high-severity failure qualitatively; averages cannot mask a
  secret leak or wrong canonical-source decision.
- [ ] Run shadow autonomy and compare proposed publications with final human
  decisions.
- [ ] Test credential compromise and prove the identity remains limited to the
  candidate collection and cannot change permissions.
- [ ] Obtain explicit collection-owner approval for each adopt decision.
- [ ] Publish adopt/defer/reject decisions and required implementation gates.

## Acceptance

- No autonomous write occurs during evaluation.
- Evidence covers the minimum time/proposal threshold and all candidate content
  types.
- Any secret leak, unauthorized target, repository mutation, or unexplained
  canonical-source conflict forces rejection until redesigned and reevaluated.
- Adoption is collection-specific, owner-approved, reversible, and retains
  full audit/rollback.
- Approval-only mode remains the default outside explicitly delegated scopes.

## Rollback or abandonment

Keep approval-only mode. Archive the evaluation and rejected approach with its
reason. If a later implementation is reverted, revoke direct-write permission
without disabling proposal generation.
