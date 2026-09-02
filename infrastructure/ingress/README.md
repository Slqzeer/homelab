# Ingress

Services are reached over the **tailnet**, not the LAN. The Tailscale
Kubernetes operator provides `IngressClass: tailscale`; TLS terminates at
a Tailscale proxy pod with a real Let's Encrypt certificate, and the hop
from there to the Service is plain HTTP inside the cluster.

There is no LAN ingress and no `.home.arpa`. cert-manager is deliberately
not installed — see the design spec, §11.

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

The `tailscale.com/proxy-class: homelab` annotation gives the Ingress a
dedicated proxy pod — it registers as an ordinary tailnet device and
never touches the Service layer. There is no pool to share: **every
Ingress exposed this way costs its own proxy pod**, measured at roughly
30Mi (see Memory below). That is a real, per-service memory cost on this
host, not a rounding error — budget for it before adding another one.

A service at sync-wave 10 or later may instead own its Ingress alongside
its own manifests. Argo CD's Ingress lives here only because Argo CD runs
at wave -1; see the comment in `config/argocd-ingress.yaml`.

## The `operator-oauth` Secret

The operator authenticates to the Tailscale API with an OAuth client
stored as the Secret `operator-oauth` in the `tailscale` namespace, keys
`client_id` and `client_secret`, mounted at `/oauth`.

**This Secret exists only in the cluster and in no repository.** If it is
lost or the client is revoked, all tailnet ingress stops. It is
backup-worthy state until Vault takes over in phase 16.

To recreate it, make an OAuth client at
<https://login.tailscale.com/admin/settings/oauth> with exactly these
scopes, each read and write, all tagged `tag:k8s-operator`:

- General → Services
- Devices → Core
- Keys → Auth Keys

Do not grant `all`; it confers every scope plus all device tags.

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
