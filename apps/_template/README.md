# Application site-integration scaffold

This directory is a planning aid, never an Argo source. It contains no
deployable manifests, credentials, or hostnames. Copy the *roles* below into
an application-specific directory only after deciding how that product is
packaged. Do not point an Application at `apps/_template/`.

| Owner / location | Role |
| --- | --- |
| Product repository or pinned upstream chart | Source, tests, release pipeline, generic deployment package, immutable image |
| `bootstrap/namespaces/namespaces.yaml` | Exactly one restricted namespace declaration |
| `environments/homelab/apps/<name>.yaml` | Child Application, dependency wave, immutable revision, onboarding annotations |
| `apps/<name>/` | Site values, Vault/VSO projection, network policies, monitoring, and product-specific integration |
| `infrastructure/ingress/config/<name>-ingress.yaml` | Centralized tailnet route; portal publication only after approval |
| `docs/runbooks/<name>-recovery.md` | Operations, upgrade, rollback, retirement, and restore drill for durable state |

Follow [the versioned onboarding contract](../../docs/application-onboarding.md)
and add application-specific conformance fixtures. Keep product build logic in
the product repository and site policy here. Render first, validate the
rendered resources, then record live acceptance evidence before publication.
