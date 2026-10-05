# Ingress

Services are reached over the **tailnet**, not the LAN. The Tailscale
Kubernetes operator provides `IngressClass: tailscale`; TLS terminates at
a Tailscale proxy pod with a real Let's Encrypt certificate, and the hop
from there to the Service is plain HTTP inside the cluster.

There is no LAN ingress and no `.home.arpa`. cert-manager is deliberately
not installed — see the design spec, §11.

## Prerequisites

**HTTPS Certificates must be enabled on the tailnet**: Tailscale admin
console → DNS → HTTPS Certificates → Enable. This is tailnet-wide, done
once by hand in the admin console — it is not a Kubernetes object, does
not live in this repository, and a cluster rebuild does not recreate it.
Without it, an Ingress still provisions and gets a `.ts.net` hostname, but
no certificate is ever issued for it, so every browser hitting that
hostname gets a TLS error with no corresponding error anywhere in the
cluster or in Argo CD. Verify it is on with:

```bash
tailscale status --json | jq .CertDomains
```

`null` means it is disabled and no certificate will ever issue; a list of
domains means it is on.

| File | Purpose |
| --- | --- |
| `values.yaml` | Operator Helm values. **Not** applied as a manifest |
| `config/` | Manifests applied by the `ingress-config` Application |

`config/` must contain only valid Kubernetes manifests. The Application
applies every YAML in it, and a Helm values file has no `kind`.

## Exposing a new service

Every Ingress joins the shared `ingress` ProxyGroup (`config/proxygroup.yaml`,
two replicas). Each hostname is published as a **Tailscale Service**
(`svc:<service>`) that every pool replica advertises, so adding a service
adds no pod:

    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: <service>
      namespace: <namespace>
      annotations:
        tailscale.com/proxy-group: ingress
    spec:
      ingressClassName: tailscale
      defaultBackend:
        service:
          name: <service>
          port:
            number: 80
      tls:
        - hosts:
            - <service>

`spec.tls[0].hosts[0]` is a **short** name. `<service>` becomes
`<service>.taildf6cd4.ts.net`. Writing the full FQDN there is wrong.

**The `tailscale.com/proxy-group: ingress` annotation is not decorative and
must not be dropped when copying this template.** Without it the operator
falls back to its default: a dedicated proxy pod for that one Ingress, with
the chart's `resources: {}`, with no memory ceiling. The Ingress still comes
up, still gets a working hostname and certificate, and Argo CD still reports
it Synced/Healthy, so nothing flags the mistake.
`scripts/validate_application_onboarding.py` rejects it for onboarded apps.

**NetworkPolicies must admit the pool, not a per-Ingress proxy.** Pool pods
carry `tailscale.com/parent-resource: ingress` and
`tailscale.com/parent-resource-type: proxygroup`. A policy that selects the
old dedicated-proxy labels (`parent-resource: <service>`,
`parent-resource-type: ingress`) matches nothing, and the hostname returns
**502** with `connection refused` in the pool pod's log. Selecting
`tailscale.com/managed: "true"` also works.

Only HTTPS (443) is served. Dedicated proxies also answered on port 80; pool
Services do not.

### History: the pool was abandoned once

A shared ProxyGroup was the original design and was abandoned on
2026-09-02: DNS resolved the Service VIP, but `tailscale ping` reported "no
matching peer" and connections got "No route to host" from two machines
(design spec §10). It was re-tested on 2026-10-05 with the operator and
clients on 1.102.3, using a throwaway `pg-test` Ingress: the VIP answered
over IPv4 and IPv6 from this host and from a second tailnet PC. The tailnet
policy had not changed in between, so the fix was most likely in Tailscale
itself. If routing regresses, the fallback is still to replace the
annotation with `tailscale.com/proxy-class: homelab`, which gives that
Ingress a dedicated, memory-bounded proxy that never touches the Service
layer.

**Every Ingress in this repository lives here, at wave 21 — not alongside
the service it fronts.** This was reconsidered during phase 16 (Vault) and
decided the other way: a service owning its own Ingress at its own wave
would reintroduce, per-service, exactly the wave-coupling decision V8
removed for `ingress-config` itself (see the design spec §6 and §8). The
Vault Ingress (`config/vault-ingress.yaml`) is the worked example — Vault
is a wave-10 platform component, but its Ingress lives here at wave 21 with
everything else, not beside `platform/vault/`. Argo CD's own Ingress lives
here for the same reason, on top of the wave -1 bootstrapping problem
described in the comment in `config/argocd-ingress.yaml`.

## Portal publication

An Ingress is absent from the Homelab Portal catalogue unless it carries the
complete `portal.homelab.io/*` annotation contract. Publication controls only
whether the portal reveals a card; the target service continues to enforce its
own authentication and authorization.

Published metadata requires `enabled: "true"`, a non-empty `name`,
`description`, and `category`, a bundled icon identifier, an integer `order`
from 0 through 9999, and one of these access modes:

- `public`: visible without a portal session;
- `authenticated`: visible to any valid portal session;
- `groups`: visible only to an exact group named in the unique, non-empty CSV
  `portal.homelab.io/groups` value;
- `admin`: visible only to the exact Keycloak group `portal-admin`.

Only `groups` may set `portal.homelab.io/groups`. Administrative targets must
never use `public`. Unknown icons fall back in the application, but manifests
in this repository use only the portal's bundled identifiers so mistakes are
caught before reconciliation.

The initial catalogue is intentionally narrow:

| Target | Portal access | Policy basis |
| --- | --- | --- |
| Vault | `groups: homelab-admins` | root-token administrative UI |
| Argo CD | `groups: homelab-admins` | local administrator only; Dex is disabled |
| Keycloak | `groups: homelab-admins` | identity administration console |

Credential-free checks on 2026-09-24 confirmed that the Vault and
Keycloak administrative APIs reject anonymous requests. Argo CD's
session endpoint returned an explicitly logged-out identity. Those checks
used no token, cookie, or Secret. The checked-in service policies above are
the source of each catalogue access decision; change the metadata only with a
matching policy review and a fresh authentication check.

The three administrative cards use the existing exact `homelab-admins` group
rather than portal `admin` access. Portal `admin` is reserved for the separate
`portal-admin` diagnostic role, which is not part of the checked-in realm
seed. Card visibility does not grant access to a target.

`config/portal-ingress.yaml` is intentionally unlisted, even though it meets
the same Tailscale exposure rules. It routes only `/` to Service
`homelab-portal`'s named `public` port. The `operations` port is never an
Ingress backend, and the portal must not publish a card that redirects to
itself.

## The `operator-oauth` Secret

The operator authenticates to the Tailscale API with an OAuth client
stored as the Secret `operator-oauth` in the `tailscale` namespace, keys
`client_id` and `client_secret`, mounted at `/oauth`.

**This Secret exists only in the cluster and in no repository.** If it is
lost or the client is revoked, all tailnet ingress stops. It is
backup-worthy state permanently, not until some future migration — it can
**never** move into Vault, phase 17 (the Vault Secrets Operator)
notwithstanding: the operator mounts this Secret at sync-wave 2, and Vault
does not exist until wave 10, so a rebuild would wait forever for a secret
that itself requires Vault to be up. See
`docs/superpowers/specs/2026-09-05-vault-secrets-operator-design.md` §10 and
`docs/troubleshooting.md` entry 6.

To recreate it, make an OAuth client at
<https://login.tailscale.com/admin/settings/oauth> with exactly these
scopes, each read and write, all tagged `tag:k8s-operator`:

- General → Services
- Devices → Core
- Keys → Auth Keys

Do not grant `all`; it confers every scope plus all device tags.

`services` is load-bearing: the shared ProxyGroup publishes every Ingress
hostname as a Tailscale Service, which the operator creates through this
scope. Do not trim it.

    kubectl create secret generic operator-oauth -n tailscale \
      --from-literal=client_id=<id> --from-literal=client_secret=<secret>

The tag must already exist in the tailnet policy before the console will
attach it — see `infrastructure/networking/README.md`.

## Memory

`ProxyClass` bounds each pool pod to 128Mi. That bound is deliberate: this
host has a history of swap exhaustion. Read `docs/troubleshooting.md`
before raising it. Each pool pod now serves every hostname, so it is the
pod to watch against that limit.

**Measured:** with dedicated proxies, each pod used 25-30Mi, so the eleven
tailnet Ingresses cost about 275Mi. After the 2026-10-05 migration, with
all eleven hostnames on the pool and traffic settled, `ingress-0` used 57Mi
and `ingress-1` 29Mi: **86Mi for the whole pool, about 190Mi saved.** The
replicas are not symmetric; `-0` also obtains the certificates. Both are
well inside the 128Mi limit. The operator pod used 50Mi (85Mi briefly
during the switch-over reconciles).
