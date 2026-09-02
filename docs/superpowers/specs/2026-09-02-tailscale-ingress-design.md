# Tailscale Ingress and Tailnet DNS — Design

Covers workstation-plan phases 13 (Ingress) and 14 (DNS). Phase 15
(cert-manager) is deliberately deferred; §11 records why.

## 1. Goal

Reach cluster services over the tailnet at stable names with
browser-trusted TLS, replacing `kubectl port-forward`, with every
Kubernetes-side object reconciled by Argo CD from this repository.

The first and only service exposed in this phase is Argo CD itself, at
`https://argocd.taildf6cd4.ts.net`.

## 2. Measured starting state

Measured 2026-09-02 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| k3s | v1.36.4+k3s1, single node, `192.168.1.201` |
| Node network | WiFi `wlp35s0`, **DHCP lease**, gateway `192.168.1.1` |
| Traefik | Running, chart `traefik-40.1.4+up40.1.0`, k3s-managed |
| IngressClass | `traefik (default)`, holds LAN `:80`/`:443` via servicelb |
| Argo CD | 5/5 pods Running; `argocd`, `namespaces`, `root` all Synced/Healthy |
| Argo CD access | `ClusterIP` only — port-forward is the sole route |
| Tailscale (host) | 1.102.3, `100.96.61.37`, `slqzeer-ms7c56.taildf6cd4.ts.net` |
| MagicDNS | Working — tailnet names resolve on the host |
| Tailnet HTTPS certs | Was disabled (`CertDomains: null`); **enabled by the operator on 2026-09-02** |
| Tailnet devices | 2: this host, and `msi` (Windows) |
| Component namespaces | `argocd`, `cert-manager`, `vault` (last two empty) |
| Pods running | 13 |
| **Memory** | **15Gi total, 1.1Gi available, swap 511Mi FULLY EXHAUSTED** |
| Largest process | ARK `ShooterGameServ`, 7.7GB RSS (47% of RAM) |

Two facts drive most decisions below: HTTPS certificates are off, and
the host has almost no free memory.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Tailnet names, not `.home.arpa` | User's choice. Real Let's Encrypt certs, no CA to install on any device, no DHCP fragility |
| D2 | Tailscale Kubernetes operator, chart `1.102.3` | Exactly matches host Tailscale 1.102.3. GitOps-manageable, unlike host-level `tailscale serve` |
| D3 | Traefik left untouched | k3s owns it. Our Ingresses name `tailscale` explicitly, so Traefik never sees them |
| D4 | Shared `ProxyGroup` **attempted, then abandoned for the §10 fallback**: dedicated per-Ingress proxy via `tailscale.com/proxy-class`, `ProxyClass` retained for its resource bounds | Chart default is a proxy pod per Ingress, and `replicas` defaults to **2** — memory (§2) forbids both, which is why a shared pool was tried first. It was dropped because the Tailscale Service mechanism a ProxyGroup relies on proved unroutable on this tailnet even with `autoApprovers` correctly applied — see §10 |
| D5 | `ProxyClass` with explicit resource limits | Chart default is `resources: {}` — unbounded, unacceptable on this host |
| D6 | Argo CD's Ingress lives with the ingress component, **not** in `bootstrap/argocd/values.yaml` | Avoids a rebuild deadlock. See §7 |
| D7 | Two Applications (operator wave 2, config wave 3) | CRDs must be established before their CRs exist. Waves make this deterministic |
| D8 | OAuth credential is a hand-made Secret | Same pattern as `repo-homelab`. No credential in git |
| D9 | Tailnet policy as HuJSON in this repo | Reviewable and versioned. **Not** Argo-CD-reconciled — see §6 |
| D10 | cert-manager deferred | Tailscale issues the certs. See §11 |
| D11 | Port-forward stays documented | Tailscale must not become the only way in |

## 4. Architecture

```
msi (tailnet device)
      │  https
      ▼
argocd.taildf6cd4.ts.net          ← tailnet node; TLS terminates here,
      │                             cert issued and renewed by Tailscale
      │  http (in-cluster)
      ▼
argocd-server:80                  ← already runs with server.insecure: true
```

The operator runs in the `tailscale` namespace and registers
`IngressClass: tailscale`. An Ingress naming that class causes the
operator to publish it on the tailnet and obtain a certificate.

Traefik keeps `IngressClass traefik (default)` and the LAN ports. It
simply has no Ingress to serve. Reclaiming its ~100Mi would need
`--disable=traefik` on the k3s server — a host-level change, explicitly
out of scope here (§12).

## 5. Repository layout

**New:**

| Path | Contents | Reconciled by |
| --- | --- | --- |
| `infrastructure/ingress/values.yaml` | Operator Helm values; OAuth fields left empty | Argo CD |
| `infrastructure/ingress/config/proxyclass.yaml` | Resource bounds for proxy pods | Argo CD |
| `infrastructure/ingress/config/proxygroup.yaml` | `type: ingress`, `replicas: 1` | Argo CD |
| `infrastructure/ingress/config/argocd-ingress.yaml` | Ingress in the `argocd` namespace | Argo CD |
| `infrastructure/ingress/README.md` | Prerequisites, the Secret, exposing a new service | — |
| `infrastructure/networking/policy.hujson` | Tailnet ACL policy | **Nothing — see §6** |
| `environments/homelab/apps/ingress-operator.yaml` | Application, sync-wave `2` | Argo CD (root) |
| `environments/homelab/apps/ingress-config.yaml` | Application, sync-wave `3` | Argo CD (root) |

**Modified:** `bootstrap/namespaces/namespaces.yaml` (add `tailscale`);
`bootstrap/argocd/values.yaml` (`configs.cm.url` only — a string, no
health implication); `README.md`; `docs/troubleshooting.md`.

`infrastructure/ingress/.gitkeep` is removed once the directory has real
content.

### The `config/` subdirectory is load-bearing

`ingress-config` points at a directory and treats every YAML inside it as
a Kubernetes manifest. Pointing it at `infrastructure/ingress/` would make
it try to apply `values.yaml` — a Helm values file with no `kind` — as an
object. The Helm values file and the plain manifests must not share a
directory.

## 6. The tailnet policy file — a new class of config

`infrastructure/networking/policy.hujson` is the first file in this
repository that **Argo CD does not reconcile and cannot reconcile.** It
targets Tailscale's control plane, not the Kubernetes API.

Committing and pushing it changes nothing on its own. Until phase 20
wires up `tailscale/gitops-acl-action` in GitHub Actions, the file is
documentation-grade source of truth and is applied by hand:

1. Open `https://login.tailscale.com/admin/acls`
2. Reconcile the file against what is live there
3. Save in the console

**The policy file replaces the tailnet policy wholesale.** It is not a
merge. The current live policy must be copied into the repo file first,
then `tagOwners` added — never the reverse.

Required additions to whatever policy is already live:

```hujson
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s":          ["tag:k8s-operator"],
},

"autoApprovers": {
  "services": {
    "tag:k8s": ["tag:k8s"],
  },
},
```

The operator identifies as `tag:k8s-operator`. Proxies it creates are
tagged `tag:k8s`, owned by the operator so it may create them unattended.

Publishing a Tailscale Service (§10) is a double opt-in: the operator
advertises it, and the policy must separately auto-approve it, or the
Service resolves in MagicDNS but nothing answers behind it. The
`autoApprovers` stanza above grants that approval for anything tagged
`tag:k8s` — which is how the operator tags both the ProxyGroup devices and
the Services they advertise.

Automating this in phase 20 needs a *second* OAuth client with
`policy_file` write scope, distinct from the operator's.

## 7. Sync waves, and the deadlock this design avoids

| Wave | Application | Creates |
| --- | --- | --- |
| -1 | `argocd` | Argo CD's own release |
| 0 | `namespaces` | `cert-manager`, `vault`, **`tailscale`** |
| 2 | `ingress-operator` | Operator, CRDs, `IngressClass` |
| 3 | `ingress-config` | ProxyClass, ProxyGroup, Argo CD's Ingress |

**Why D6 exists.** Enabling `server.ingress` in `bootstrap/argocd/values.yaml`
is the obvious approach and it deadlocks a rebuild:

- Argo CD self-manages at wave -1; root waits for it to become Healthy
  before starting wave 0.
- Argo CD's health check reports an Ingress as `Progressing` until
  `status.loadBalancer.ingress` is populated.
- Only the Tailscale operator populates it — and the operator installs at
  wave 2, which root never reaches, because it is still waiting on
  wave -1.

The Ingress blocks the installation of the controller that would unblock
the Ingress. This works on the *current* cluster, where the operator is
added before the Ingress, and fails on the next rebuild from scratch —
the scenario `README.md` explicitly promises.

Keeping Argo CD's release Ingress-free lets wave -1 go Healthy
unconditionally. Services at wave 10+ are past this ordering problem and
may own their Ingress normally; Argo CD is special-cased solely because it
lives at wave -1.

## 8. Prerequisites (operator actions, in this order)

Order matters: a tag must exist in the policy before an OAuth client can
be tagged with it.

1. **Enable HTTPS Certificates** — admin console → DNS → HTTPS
   Certificates → Enable. **Done 2026-09-02.** Without it no certificate
   is ever issued and the phase produces nothing usable. Tailnet-wide;
   affects `msi` benignly.
2. **Apply the tag owners** — per §6, merging into the live policy.
3. **Create the OAuth client** — `https://login.tailscale.com/admin/settings/oauth`
   → *Generate OAuth client*. It is an **OAuth client, not an OIDC
   client**; OIDC on that settings page is for user SSO and is unrelated.

   Exactly three scopes, each **Read and Write**, and no others:

   | Console category | Scope | Access |
   | --- | --- | --- |
   | General → Services | `services` | Read + Write |
   | Devices → Core | `devices:core` | Read + Write |
   | Keys → Auth Keys | `auth_keys` | Read + Write |

   In the **Tags** box on the same form, select `tag:k8s-operator` — this
   is why step 2 must come first; the tag cannot be attached until it
   exists in the policy.

   Do **not** grant `all`, which confers every scope plus all device tags.

   Why each is needed: `auth_keys` lets the operator mint keys for itself
   and the proxies it creates; `devices:core` lets it register, tag and
   remove those devices; `services` backs the Tailscale Services that
   publish per-Ingress hostnames (§10).

   The client secret is displayed once, at creation.
4. **Create the Secret** — after the `tailscale` namespace exists:

   ```bash
   kubectl create secret generic operator-oauth -n tailscale \
     --from-literal=client_id=<id> --from-literal=client_secret=<secret>
   ```

   Keys are exactly `client_id` and `client_secret`, mounted at `/oauth`;
   the chart reads them via `CLIENT_ID_FILE` / `CLIENT_SECRET_FILE`. The
   chart's `oauth.*` values are left empty so it templates no Secret.

## 9. Credentials and single points of failure

`operator-oauth` is the **second** piece of cluster-only state that exists
in no repository, joining `repo-homelab`:

| Secret | Namespace | Loss impact |
| --- | --- | --- |
| `repo-homelab` | `argocd` | All Argo CD reconciliation stops |
| `operator-oauth` | `tailscale` | All tailnet ingress stops; every URL dies |

Both are backup-worthy until Vault takes over in phase 16.
`docs/troubleshooting.md` entry 6 is extended to cover the new one.

## 10. Resolved: the shared ProxyGroup failed, the fallback is the design

**Partially resolved.** With a shared `ProxyGroup`, pods are named from
`hostnamePrefix` (`<prefix>-0`), while a per-Ingress hostname such as
`argocd.taildf6cd4.ts.net` is published through a separate mechanism
layered on top. The operator's required `services` OAuth scope (§8)
identifies that mechanism as **Tailscale Services**, which is what makes
one pool of proxy pods able to serve several distinct hostnames.

**Resolved from documentation.** The hostname comes from
`spec.tls[0].hosts[0]` on the Ingress as a *short* name — `argocd` yields
`argocd.taildf6cd4.ts.net`. The Ingress attaches to a pool via the
`tailscale.com/proxy-group` annotation. With a ProxyGroup, the `-0`
replica is always the one that obtains the Let's Encrypt certificate,
so `replicas: 1` is sufficient for certificate issuance.

Both `ProxyClass` and `ProxyGroup` are **cluster-scoped** — their
manifests carry no `namespace`.

**Confirmed by observation, then found to fail one layer deeper.** The
ProxyGroup hostname mechanism itself works as documented: the operator
logged "exposing Ingress over tailscale" and "Updating serve config" for
`argocd`, and `argocd.taildf6cd4.ts.net` resolved via MagicDNS to a
tailnet VIP. The first failure looked like a policy gap — a
ProxyGroup-backed Ingress publishes a **Tailscale Service**, and
advertising a Service requires separate auto-approval in the tailnet
policy (§6, §8); without `autoApprovers.services` the hostname resolved
in DNS but nothing routed to it. That stanza was added and the user
applied it in the admin console, and the netmap provably picked it up —
the host gained the capability `services/argocd`, which it lacked before,
and the proxy's serve config was confirmed correct:
`https://argocd.taildf6cd4.ts.net (tailnet only) (svc:argocd)` proxying to
`http://10.43.151.249:80/`.

**It still did not route.** DNS resolved to the Service VIP
`100.92.232.41`, but that VIP was unreachable from two separate machines
(including `msi`, ruling out a same-node hairpin): `tailscale ping`
reported "no matching peer" and `nc` reported "No route to host". The
decisive datum came from probing the proxy pod's own ordinary tailnet
device IP instead of the Service VIP: `nc 100.85.234.26:443` returned
"Connection refused" — the packet arrived and nothing was listening,
which is a live, routable device behaving normally. Ordinary tailnet
devices route fine on this tailnet; only the Tailscale Service layer
(the VIP/`svc:` mechanism a ProxyGroup depends on to multiplex one pool
of proxies across several hostnames) does not. This is deeper than the
policy gap the autoApprovers fix addressed, and no further policy or
config change was found to unblock it.

**Fallback taken.** Per the fallback below, D4 was dropped: the
`tailscale.com/proxy-group` annotation was removed from the Argo CD
Ingress and replaced with `tailscale.com/proxy-class: homelab`, giving it
a dedicated proxy that registers as an ordinary tailnet device and never
touches the Service layer. The now-unused `ProxyGroup` manifest
(`infrastructure/ingress/config/proxygroup.yaml`) was deleted; Argo CD
prunes the corresponding StatefulSet. The `ProxyClass` is retained and
now referenced directly by the Ingress, so the dedicated proxy stays
memory-bounded exactly as it was inside the pool. The `autoApprovers`
policy stanza is also retained even though the current design no longer
needs it — see `infrastructure/networking/README.md` — since it is
harmless and would be needed again if ProxyGroups are ever revisited.

This history is kept rather than deleted so a future reader does not
re-attempt the shared ProxyGroup expecting it to work: the hostname
mechanism functions, but the underlying Tailscale Service routing does
not, on this tailnet, as of this measurement.

**Fallback (as originally authorised, now the active design):** drop D4,
remove the `tailscale.com/proxy-group` annotation, and let each Ingress
create its own standalone proxy via `tailscale.com/proxy-class`. That
path is unambiguous and works. The cost is one pod per exposed service
instead of one pool — which §13 flags as significant on this host, but it
is a memory cost, not a functional loss. The user-visible result is
identical.

## 11. Why phase 15 (cert-manager) is deferred

The workstation plan places cert-manager after Ingress because it assumes
LAN names needing a private CA. D1 solves certificates a different way:
Tailscale issues and renews genuine Let's Encrypt certificates at the
proxy.

Installing cert-manager now would run three deployments (~150–200Mi) with
**no Certificate resource to issue** — on a host with 1.1Gi available and
swap exhausted.

It is deferred, not cancelled. `infrastructure/cert-manager/` and the
`cert-manager` namespace stay in place. It gets installed when something
genuinely needs internal PKI — most likely Vault (phase 16), Vault Secrets
Operator (phase 17), or a later decision to serve the LAN as well.

## 12. Out of scope

- cert-manager (§11)
- Disabling Traefik — a host-level k3s flag, worth ~100Mi, decide separately
- LAN/`.home.arpa` access — explicitly rejected in favour of D1
- Tailscale Funnel (public internet exposure)
- Exposing anything other than Argo CD
- `tailscale/gitops-acl-action` in CI — phase 20
- Moving either Secret into Vault — phase 16/17
- The Tailscale API-server proxy (`apiServerProxyConfig` stays `false`)

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **Memory exhaustion.** 1.1Gi available, swap at 0B. New pods land on a host already swapping | **High** | D4 and D5 cap usage; measure before/after and record real figures; ARK can be stopped if needed |
| ~~HTTPS certs left disabled~~ | Closed | Enabled 2026-09-02. Verification still checks the certificate itself, not just connectivity |
| OAuth client over-scoped | Medium | §8 names exactly three scopes and forbids `all`; over-granting hands broad tailnet control to a cluster Secret |
| ProxyGroup hostname mechanics differ from expectation | Medium | §10 fallback, decided by observation |
| `operator-oauth` lost or revoked | Medium | §9; port-forward (D11) remains a working way in |
| Policy paste clobbers live ACLs | Medium | §6 mandates copying live policy first |
| Rebuild ordering: operator needs a Secret that no repo holds | Medium | `README.md` First install gains a step, as `repo-homelab` did |
| Tailnet rename changes every hostname | Low | Names derive from the MagicDNS suffix; documented |

## 14. Verification

The phase is complete when all of the following hold:

1. Operator pod `Running` in `tailscale`; a `tailscale-operator` device
   appears in `tailscale status`.
2. ProxyGroup reports ready with **exactly one** replica pod.
3. Argo CD's Ingress has a populated `status.loadBalancer.ingress`.
4. `argocd.taildf6cd4.ts.net` resolves via MagicDNS.
5. **From `msi`, not from this host**, `https://argocd.taildf6cd4.ts.net`
   loads the Argo CD UI with **no certificate warning**, and login works.
6. The certificate chains to Let's Encrypt and matches the hostname —
   verified explicitly, not inferred from the page loading.
7. `kubectl port-forward` still works (D11).
8. All Applications Synced/Healthy; `free -h` recorded before and after,
   replacing the 150–250Mi estimate with a measurement.
