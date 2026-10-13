# Ticket 04 — Integrate Keycloak Admission

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Landed TLE Keycloak-client work and Tickets 01–03
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Register Outline as an exact confidential OIDC client and enforce admission in
Keycloak so only `homelab-users` and `homelab-admins` can authenticate to it.

## Scope

- Provision the OIDC client secret before either Keycloak or Outline consumes
  it, eliminating the existing optional-secret/two-sync trap.
- Add the `outline` client to the explicit registration allowlist.
- Configure exact callback, origin, post-logout URI, authorization-code flow,
  and default scopes.
- Create client role `outline-access` and map both admitted groups to it.
- Reconcile a copied client-specific browser flow that denies users lacking the
  role, then bind it only to the Outline client.
- Add reciprocal Outline-to-Keycloak network policy on TCP/8080.
- Configure Outline's public authorization/logout and internal token/userinfo
  endpoints.

## Out of scope

- Automatic mapping from `homelab-admins` to Outline workspace administrator.
- Realm-wide browser-flow changes.
- Public ingress or portal publication.

## Expected files and systems

- Modify `platform/vault/configure-vault.sh`.
- Modify `platform/keycloak/config/vault-secrets.yaml`.
- Modify `platform/keycloak/config/client-registration.yaml`.
- Create a focused Keycloak flow reconciliation manifest/script under
  `platform/keycloak/config/`.
- Modify `platform/keycloak/config/networkpolicy.yaml`.
- Modify `apps/outline/config/deployment.yaml` and network policy.
- Modify `platform/keycloak/test_client_registration.py`.

## Interfaces

- Produces client ID `outline`, client role `outline-access`, and an exact
  client-specific authentication-flow binding.
- Consumes exact Outline pod labels from Ticket 03.
- Produces the live OIDC contract consumed by Ticket 05.

## Implementation checklist

- [ ] Add failing fake-Keycloak tests for exact client fields, independent
  secret handling, role/group mappings, flow binding, convergence, duplicate
  client rejection, and prohibition on unrelated realm/client mutations.
- [ ] Add secret seeding and both least-privilege namespace projections with
  deterministic ordering.
- [ ] Extend the explicit client-registration allowlist and required scopes.
- [ ] Implement the copied conditional flow with a negated role condition and
  explicit deny execution; never edit the realm's default browser flow.
- [ ] Bind the flow only through the Outline client's authentication-flow
  override.
- [ ] Add the four manual OIDC endpoint variables and reciprocal network flows.
- [ ] Run fake tests twice and prove the second run produces no mutation.
- [ ] Sync Keycloak and Outline and inspect live configuration without printing
  the client secret.

## Acceptance

- Callback is exactly
  `https://outline.taildf6cd4.ts.net/auth/oidc.callback`; no wildcard exists.
- Browser auth/logout use the tailnet Keycloak URL; token/userinfo use the
  cluster Service.
- Both admitted groups receive `outline-access`; unrelated groups do not.
- The flow is client-specific and the default realm browser flow is unchanged.
- A clean ordering test proves no manual second Keycloak sync is needed.
- Keycloak and Outline network policies match the exact workload selectors and
  TCP/8080 only.

## Rollback

Remove the client-specific flow binding before deleting its executions or role.
Disable the Outline client, preserve its secret until rollback completes, and
revert only allowlisted Outline mutations. Never replace the realm browser flow
as part of rollback.
