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

A shared `ProxyGroup` pool was tried first to save memory and abandoned:
a ProxyGroup-backed Ingress is published as a **Tailscale Service**, and
that mechanism proved unroutable on this tailnet — DNS resolved a VIP,
but `tailscale ping` reported "no matching peer" and connections got "No
route to host", from two separate machines, while the same proxy pod's
ordinary tailnet device IP cleanly refused connections (proving ordinary
devices route fine and only the Service layer fails). See the design
spec §10 for the full evidence. `infrastructure/ingress/config/proxygroup.yaml`
no longer exists.

The working pattern instead gives every Ingress its own dedicated proxy:

    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: <service>
      namespace: <namespace>
      annotations:
        tailscale.com/proxy-class: homelab
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

A `tailscale` Ingress gets a dedicated proxy pod **by default** — that part
has nothing to do with the annotation. It registers as an ordinary tailnet
device and never touches the Service layer. There is no pool to share:
**every Ingress exposed this way costs its own proxy pod**, measured at
roughly 30Mi (see Memory below). That is a real, per-service memory cost
on this host, not a rounding error — budget for it before adding another
one.

What the `tailscale.com/proxy-class: homelab` annotation actually does is
bind that already-dedicated proxy to the `homelab` ProxyClass, which caps
it at 128Mi (see Memory below). **It is not decorative and must not be
dropped when copying this template.** The chart's own default for a proxy
with no ProxyClass is `resources: {}` — unbounded. Omit the annotation and
the Ingress still comes up, still gets a working hostname and
certificate, and Argo CD still reports it Synced/Healthy — there is no
error anywhere — but the resulting proxy pod has no memory ceiling at all,
on a host that is already swapping.

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
| Grafana | `groups` | exact `homelab-admins,homelab-users` Keycloak groups |
| Nexus | `groups: homelab-admins` | local authentication remains authoritative; the separate Docker endpoint is unlisted |
| Keycloak | `groups: homelab-admins` | identity administration console |

Credential-free checks on 2026-09-24 confirmed that the Vault, Grafana,
Nexus, and Keycloak administrative APIs reject anonymous requests. Argo CD's
session endpoint returned an explicitly logged-out identity. Those checks
used no token, cookie, or Secret. The checked-in service policies above are
the source of each catalogue access decision; change the metadata only with a
matching policy review and a fresh authentication check.

The four administrative cards use the existing exact `homelab-admins` group
rather than portal `admin` access. Portal `admin` is reserved for the separate
`portal-admin` diagnostic role, which is not part of the checked-in realm
seed. Card visibility does not grant access to a target: in particular, Nexus
continues to use its local authentication and is not changed by this metadata.

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

`services` is required by Tailscale's own Kubernetes-operator install
documentation for the operator generally — it is not specific to
ProxyGroups or Tailscale Services, the mechanism this phase tried and
abandoned (see "Exposing a new service" above, and the design spec §10).
It may not be strictly required by the dedicated-proxy design this
repository actually uses, but that has not been tested, and trimming it
would mean regenerating the OAuth client to find out, so all three scopes
are kept as originally granted.

    kubectl create secret generic operator-oauth -n tailscale \
      --from-literal=client_id=<id> --from-literal=client_secret=<secret>

The tag must already exist in the tailnet policy before the console will
attach it — see `infrastructure/networking/README.md`.

## Memory

`ProxyClass` bounds each proxy to 128Mi. That bound is deliberate: this
host runs with almost no free memory and no usable swap. Read
`docs/troubleshooting.md` before raising it.

**Measured**, not estimated: the operator pod uses 35Mi and the Argo CD
Ingress's dedicated proxy pod uses 28Mi — 63Mi total for the whole
ingress stack, against the design spec's 150–250Mi estimate (which
priced in a ProxyGroup and, at the high end, cert-manager). The host had
549Mi available and 15 pods running at measurement time. Each additional
dedicated proxy pod (see "Exposing a new service" above) adds roughly
that same 28–30Mi.
