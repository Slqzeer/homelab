# Ticket 06 — Publish Tailnet Ingress and Portal Entry

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 05, 08, 09, and 10
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Expose the production-accepted Outline Service at its canonical tailnet hostname
and publish a correctly restricted Homelab Portal entry.

## Scope

- Add centralized Tailscale Ingress for the single user-facing HTTP port.
- Verify WebSocket upgrade and large upload behavior through the proxy.
- Add portal metadata only after authentication, authorization, backup,
  off-host restore, observability, and hardening evidence is current.
- Restrict portal visibility to `homelab-users,homelab-admins`.
- Verify there is no LAN, public Internet, metrics, health, PostgreSQL, or Redis
  exposure.

## Out of scope

- Creating a second hostname for administration or metrics.
- Public or guest access.
- Weakening Keycloak admission because the portal hides a card.

## Expected files and systems

- Create `infrastructure/ingress/config/outline-ingress.yaml`.
- Modify `infrastructure/ingress/config/kustomization.yaml` when present.
- Extend onboarding/portal fixtures in `tests/test_application_onboarding.py`.
- Update `docs/runbooks/outline-recovery.md` with exposure and retirement steps.

## Implementation checklist

- [ ] Add failing tests for canonical host, ingress class, proxy group, Service
  port, WebSocket path behavior, and exact portal group metadata.
- [ ] Verify every dependency ticket's evidence is present and current; stop if
  remote restore or access denial has not passed.
- [ ] Add the centralized ingress initially with publication metadata disabled.
- [ ] Validate TLS, WebSockets, uploads, login, logout, and denied access through
  `https://outline.taildf6cd4.ts.net`.
- [ ] Enable the portal entry with `access: groups` and both allowed group names.
- [ ] Verify an eligible user sees and opens the card while an unrelated realm
  user cannot authenticate even with the direct URL.
- [ ] Verify no non-tailnet route reaches the Service.

## Acceptance

- The only user-facing URL is `https://outline.taildf6cd4.ts.net`.
- TLS and WebSocket collaboration work through Tailscale Ingress.
- The portal lists Outline only for the two admitted groups.
- Direct URL knowledge does not bypass Keycloak admission.
- No operational or dependency port is externally routed.
- The runbook starts retirement by disabling portal publication and ingress.

## Rollback

Set `portal.homelab.io/enabled: "false"` or remove publication metadata first,
then remove the Outline Ingress. Leave the internal workload and recovery data
intact while investigating.
