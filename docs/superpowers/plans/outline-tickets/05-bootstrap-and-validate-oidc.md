# Ticket 05 — Bootstrap and Validate OIDC

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 03 and 04
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Perform the controlled first login, establish the initial Outline administrator,
and record live proof that eligible users enter while every other realm user is
denied.

## Scope

- Keep portal publication disabled during bootstrap.
- Select a human member of `homelab-admins` outside Git and have that person log
  in first.
- Verify the first user becomes the only initial Outline administrator.
- Test both admitted groups, a user in neither group, and a denied user with an
  existing Keycloak SSO session.
- Test logout, repeat login, username/email identity stability, and manual OIDC
  split routing.
- Document administrator promotion/demotion and audit procedures.

## Out of scope

- Automatic administrator-role reconciliation.
- Portal publication.
- Documentation migration.

## Expected files and systems

- Create `docs/runbooks/outline-recovery.md` with bootstrap and access sections.
- Create or update a secret-free acceptance evidence section/file under the
  runbook conventions used by this repository.
- Update `platform/keycloak/README.md` with the Outline-specific flow ownership
  and troubleshooting contract.

## Implementation checklist

- [ ] Confirm no Outline user exists and no portal card is published.
- [ ] Give the selected administrator controlled temporary reachability without
  enabling the final portal entry.
- [ ] Complete first login and verify the resulting workspace role.
- [ ] Test `homelab-users` and `homelab-admins` in separate private sessions.
- [ ] Test denial for an unrelated realm user with and without an existing SSO
  cookie.
- [ ] Test full logout, repeat login, and stable account identity.
- [ ] Confirm logs contain no tokens, cookies, claims, emails, or document
  content from the test.
- [ ] Record dated pass/fail evidence and the manual admin audit procedure.

## Acceptance

- One chosen `homelab-admins` member is the initial administrator.
- Both admitted groups can log in; a user in neither group cannot.
- Existing realm SSO does not bypass the client-specific admission flow.
- Login and logout return to the intended tailnet URLs.
- Repeated login reuses the same Outline identity.
- Manual OIDC mode and its no-PKCE limitation are recorded in the runbook.

## Rollback

Keep ingress unpublished, disable the Keycloak client, and remove test users
from Outline only when doing so cannot delete authored content. If the wrong
person became first administrator, correct the role through Outline before
allowing any additional users.
