# Ticket 20 — Deploy File Browser Quantum Independently

**Epic:** Deferred capabilities
**Status:** Planned
**Depends on:** Ticket 06; a separately approved File Browser design spec
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

If still desired after Outline is production-capable, design and deploy File
Browser Quantum as its own tailnet application with independent ownership,
storage, authentication, recovery, and permissions.

## Scope

- Run a separate architectural design process for File Browser Quantum.
- Select a stable pinned release rather than a preview/beta tag.
- Define its own namespace, persistent database/config, source mounts, OIDC,
  ingress, network policies, backups, and restore acceptance.
- Decide which user-managed filesystem sources it may expose.
- Document whether it complements Outline through links or separate user-owned
  files only.
- Enforce a hard boundary around Outline-managed attachments.

## Out of scope

- Providing Outline's S3 attachment API.
- Mounting or modifying `/var/lib/outline/data`.
- Sharing Outline's API token, database, Redis, or workspace permissions.
- Starting implementation before its design spec is approved.

## Expected files and systems

- Create a dedicated File Browser design spec and implementation plan through
  the repository's Superpowers workflow.
- Later implementation uses `apps/filebrowser-quantum`, namespace
  `filebrowser-quantum`, its own child Application, centralized ingress, and
  recovery runbook.
- Link the resulting decision from Outline's attachment-storage ADR.

## Implementation checklist

- [ ] Reconfirm the user need after Outline has real usage data.
- [ ] Brainstorm and approve a dedicated File Browser design, including source
  ownership and write boundaries.
- [ ] Verify a stable release, OIDC behavior, restricted Pod Security, and
  storage semantics.
- [ ] Write and approve its own implementation plan.
- [ ] Implement and verify it as an independent durable application.
- [ ] Prove through mounts, permissions, and network tests that it cannot modify
  Outline's attachment data.

## Acceptance

- File Browser has its own approved spec, plan, release, secrets, storage,
  backup, restore, and access model.
- No pod, volume, credential, or API capability grants it write access to
  Outline-managed state.
- Any cross-link is informational and does not create shared canonical content.
- Deployment satisfies the personal-application onboarding contract.

## Rollback or abandonment

“No longer needed after Outline” is a valid outcome. If deployed, disable portal
publication and ingress first, take a final backup, revoke OIDC/Vault access,
and retain/delete its state only through its own runbook.
