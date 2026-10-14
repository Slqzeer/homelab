# TLE Admin Keycloak Clients Design

## Purpose

Close homelab issue #1 by automatically registering the TLE administration
clients in the `homelab` Keycloak realm and reconciling their permissions from
the release-pinned TLE role manifest. This replaces the present manual,
`tle-operator`-only procedure for the two deployed environments.

## Scope

The Keycloak PostSync registration Job will manage exactly these confidential
OpenID Connect clients:

| Client ID | Origin | Redirect URI | Post-logout URI |
| --- | --- | --- | --- |
| `tle-admin-dev` | `https://tle-dev-operator.taildf6cd4.ts.net` | `https://tle-dev-operator.taildf6cd4.ts.net/auth/callback` | `https://tle-dev-operator.taildf6cd4.ts.net/` |
| `tle-admin-prod` | `https://tle-prod-operator.taildf6cd4.ts.net` | `https://tle-prod-operator.taildf6cd4.ts.net/auth/callback` | `https://tle-prod-operator.taildf6cd4.ts.net/` |

Both clients consume the same role manifest for now. The manifest remains a
versioned, vendored release artifact; promotion updates it deliberately before
the registration hook is synced.

## Architecture

`platform/vault/configure-vault.sh` owns creation and preservation of one
random OIDC secret per client at narrowly scoped Vault paths. The existing
`vso-keycloak` identity receives read access only to the two additional client
secret paths, and `platform/keycloak/config/vault-secrets.yaml` projects each
as a separate Secret in the `keycloak` namespace.

The existing `keycloak-client-registration` PostSync Job receives the two
secrets through `secretKeyRef` and extends its explicit allowlist with both
client definitions. It reconciles only client fields it owns: confidential OIDC
settings, standard authorization-code flow, exact redirect/logout/origin
values, client secret, and its required default scopes. Client URIs are exact;
no wildcard host or path is allowed.

The role manifest is transformed by the existing TLE generator into a separate
generated reconciliation script mounted by the same Job. It runs once per TLE
admin client. It ensures the manifest capability roles and `support`,
`operator`, and `admin` composites exist, and makes the role and composite
membership sets exact by removing members that the manifest no longer names.
It operates only through `clients/<uuid>/...` endpoints for those two client
IDs.

## Safety Constraints

- The reconciliation allowlist is exactly `homelab-portal`, `penpot`,
  `tle-admin-dev`, and `tle-admin-prod`; no discovery-based mutation is
  permitted.
- TLE role reconciliation can mutate only client roles and client-role
  composites on `tle-admin-dev` and `tle-admin-prod`.
- It must not create, update, or delete users, groups, realm settings, realm
  roles, default realm roles, or roles on any other client.
- Group-to-composite assignment remains a documented manual Keycloak-console
  operation.
- Secrets must be passed by environment/secret files only: never logged,
  embedded in manifests, or exposed as command-line arguments.
- Each client uses its own Vault path, VSO projection, Kubernetes Secret, and
  environment variable.

## Network and Ordering

The existing `tle-dev`-to-Keycloak TCP/8080 egress and reciprocal Keycloak
ingress policy satisfy issue #1's current network requirement and must remain
covered by tests. The registration Job stays a Keycloak PostSync hook. It may
register production before that application is deployed because the exact,
tailnet-only production hostname is intentional and no ingress is created by
this change.

## Generated Artifact Contract

The generated role reconciliation script is derived from the vendored
`apps/tle-dev/deploy-info/role-manifest.json`. Its header records manifest
version and capability/composite counts. Regeneration is required whenever the
vendored manifest changes. The checked-in generated artifact is the hook's
auditable input and must not be edited by hand.

## Tests and Acceptance Evidence

`platform/keycloak/test_client_registration.py` must exercise both TLE clients
with a fake Keycloak API and assert:

- their exact origins, redirect URIs, post-logout URIs, confidential-client
  settings, and independent secrets;
- convergence on a second run without writes;
- role and composite additions and removal of stale entries for each TLE
  client;
- no mutation of unrelated clients, realm state, users, or groups;
- narrow Vault paths, policies, VSO projections, and Job environment wiring;
- continued Keycloak port-8080 network-policy access only for intended TLE
  peers.

Documentation must replace the manual `tle-operator` procedure with the
automatic, manifest-regeneration workflow and preserve the documented manual
group-assignment step.
