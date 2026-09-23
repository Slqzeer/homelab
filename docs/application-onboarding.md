# Personal application onboarding contract (v1)

This repository owns site integration. An application repository owns its
product source, tests, build, release pipeline, image, and reusable deployment
package. A third-party chart retains its own generic packaging. This contract
records the outcomes both shapes must meet; it is not a manifest generator.
Use [the reference scaffold](../apps/_template/README.md) to plan the files,
then add only the resources the application needs.

## Ownership and release

Record the source repository or upstream chart, maintainer, release version,
immutable source revision, image tag and SHA-256 digest, support window,
upgrade constraints, and known rollback target in the application's runbook.
Pin GitHub Actions by commit and charts by exact version. Every deployed
container image, including init and hook containers, uses a release tag **and**
digest such as `registry.example/app:v1.2.3@sha256:<64 nonzero hex digits>`.
`latest`, branch revisions, wildcard chart versions, tag-only images, and the
all-zero digest cannot be promoted. Render the exact deployable revision in CI
and inspect the images in that render; pinning a Git tag alone is insufficient.

## Placement and Argo lifecycle

Give the application a dedicated namespace unless a written ownership reason
justifies sharing one. Declare it once in
`bootstrap/namespaces/namespaces.yaml`, not in a product overlay or through
`CreateNamespace=true`. Set `pod-security.kubernetes.io/{enforce,audit,warn}`
to `restricted` and their `-version` labels to `v1.36`. Child `Application`
objects live only in `environments/homelab/apps/`; site-specific manifests
and values live under `apps/<application>/`. Choose the lowest sync wave above
the application's actual dependencies. Keycloak clients depend on wave 24,
so portal and Nextcloud use wave 25. Require automated prune/self-heal and
`ServerSideApply=true` in the Application.

Annotate each child Application with:

```yaml
homelab.io/onboarding-contract: v1
homelab.io/owner: personal-applications
homelab.io/state: stateless  # or durable
```

`stateless` means no application-owned persistent volume. For durable state,
use `homelab.io/state: durable` and add
`backup.homelab.io/restore-runbook: docs/runbooks/<application>-recovery.md`.
That relative path must resolve to a checked-in file in this repository.

## Secrets and sign-in

Keep secret values only in Vault and namespace-local VSO projections. Give
each consumer a least-privilege Vault policy, namespace-local ServiceAccount
and `VaultAuth`. Every `VaultStaticSecret` must set
`spec.destination.transformation.excludeRaw: true`, project only the needed
keys, and define how rotated credentials restart their consumers. Do not
commit a Kubernetes `Secret`, put credentials in a ConfigMap or command-line
argument, or log tokens, cookies, headers, or environment dumps.

When the product supports OIDC, register a confidential Keycloak client with
exact redirect and logout URIs and only the claims it needs. Document a local
recovery route where the product provides one. The realm seed is first-import
only; live client changes need an explicit registration step.

## Exposure and publication

Place every Tailscale Ingress in `infrastructure/ingress/config/`, never in
the application package. Use `spec.ingressClassName: tailscale`, the
`tailscale.com/proxy-class: homelab` annotation, and a short tailnet TLS host.
Route only the user-facing HTTP Service port. Operations, metrics, health,
admin, and database ports remain in-cluster. No Internet or LAN ingress is
part of this contract.

Publication in the Homelab Portal is a separate, deliberate decision after
the target's authentication and authorization have been verified. An Ingress
is unlisted by default. To publish one, commit
`portal.homelab.io/enabled: "true"` plus `name`, `description`, `category`,
`icon`, `access`, and `order` annotations. Access is one of `public`,
`authenticated`, `groups`, or `admin`; `groups` requires a nonempty CSV
`portal.homelab.io/groups`, and other modes must omit it. Administrative UIs
are not assumed public. The portal excludes invalid metadata rather than
guessing access. Neither a Service nor a Pod implies publication.

## Isolation and operations

Apply namespace-wide default-deny ingress and egress NetworkPolicies. Add
only needed selected proxy, monitoring, DNS, Kubernetes API, Keycloak,
database, cache, and peer flows with port-specific rules. Avoid namespace-wide
or all-port allows. Define startup, readiness, and liveness behavior;
structured logs; metrics; alerts; CPU and memory requests and limits; and
measure idle and peak resource use after rollout.

Classify each state path as durable, reconstructible, or secret-derived. For
durable state, document backup scope, consistency, retention, restore order,
and a seeded restore drill before production acceptance. Local-path storage
is single-node and does not itself supply a backup. Record upgrade steps and
the immutable rollback revision. Stateful rollback must account for database
and file-format compatibility; restoring data may be required.

Retirement starts by disabling publication and taking a final backup for
durable applications. Revoke OIDC and Vault access, remove the child
Application and its Ingress, inspect retained PVCs, then remove the namespace
only after data retention is decided. Deleting the namespace cascades to its
resources, including PVCs.

## Acceptance and validator

Before promotion, render the pinned release and site manifests; validate the
Application, namespace, workloads, VSO objects, NetworkPolicies, and
centralized Ingress together. The Python interface is:

```python
from pathlib import Path
from scripts.validate_application_onboarding import validate_application

errors = validate_application(
    Path("environments/homelab/apps/example.yaml"),
    [Path("bootstrap/namespaces/namespaces.yaml"), Path("/tmp/example-rendered.yaml")],
    Path("infrastructure/ingress/config/example-ingress.yaml"),
)
```

An empty list passes the machine-checkable portion; errors are sorted and
YAML parse failures are errors. CI runs the fixture suite with
`python3 -m unittest -v tests.test_application_onboarding`. The validator
does not replace a live acceptance check. Verify OIDC login and denial,
tailnet-only reachability, allowed and denied network flows, secret rotation,
metrics and alerts, measured resources, rollback, and a restore drill for
durable state. Record the evidence and any justified exceptions in the
application's runbook before promotion.
