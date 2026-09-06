# Vault Secrets Operator — Design

Covers workstation-plan phase 17. Phase 16 delivered the secret *store*; this
phase delivers the *path* by which a Kubernetes workload obtains a secret from
it without anyone copying a value by hand.

## 1. Goal

A working, proven Vault → VSO → Kubernetes Secret chain, authenticated by
Kubernetes ServiceAccount rather than a stored credential, with Vault's own
configuration committed to git as a re-runnable ceremony.

The phase is not finished when the operator is installed. It is finished when a
value written into Vault appears in a Kubernetes Secret without human action,
and that has been observed.

## 2. Measured starting state

Measured 2026-09-05 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| Vault | 2.0.4, initialized, unsealed, Raft, HA active |
| Vault auth methods | **`token/` only** |
| Vault secrets engines | `agent-registry/`, `cubbyhole/`, `identity/`, `sys/` — **no KV mount exists** |
| Vault footprint | 98Mi measured (62 + 8 + 28) |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `vault` 10, `ingress-config` 21 |
| Applications | 6, all Synced/Healthy |
| Cluster-only secrets | 3: `repo-homelab`, `operator-oauth`, `vault-unseal-keys` |
| k3s SA token issuer | `https://kubernetes.default.svc.cluster.local` |
| Vault SA TokenReview | **already permitted** — the chart binds `system:auth-delegator` |
| Memory available | ~8.2Gi of 15Gi |

**Vault is empty.** This phase is not "install an operator"; it is the phase
that first gives Vault a secrets engine, an auth method, a policy and a role.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| S1 | VSO chart `1.5.1`, app `1.5.1`, from `https://helm.releases.hashicorp.com` | Current release; pinned like every other chart here |
| S2 | **Kubernetes auth**, not a static token or AppRole | No credential is stored, nothing needs rotating. Both prerequisites verified in §2 |
| S3 | KV **v2** mounted at `homelab/` | Explicit, and avoids implying Vault's dev-mode `secret/` default |
| S4 | Vault's own config is a committed script a human runs | §7. Same shape as `infrastructure/networking/policy.hujson`, which Argo CD also does not reconcile |
| S5 | The root token never enters the cluster | The script prompts via `vault login`; a Job would require a standing superuser credential in-cluster, which is strictly worse than today |
| S6 | One permanent canary secret proves the path | §8. VSO fails silently — one known-good object to check first is worth its cost |
| S7 | `vso-config` sits at wave **22**, after `ingress-config` (21) | §6. Its health depends on imperative state no wave can guarantee |
| S8 | `vso-operator` sits at wave 11 | Its health depends only on its pods starting, not on Vault being configured or unsealed |
| S9 | **`operator-oauth` is NOT migrated into Vault** | §10. It cannot be, and the phase-16 spec was wrong to say it could |
| S10 | A **dedicated ServiceAccount `vault-canary`** in namespace `vault` is what authenticates, not the operator's own | §9. VSO requires the ServiceAccount to reside in the consuming secret's namespace, and this removes any dependency on Helm-generated names |
| S11 | Helm `releaseName` pinned to `vso` | For predictable object names only. Auth no longer depends on it (S10) |

## 4. Architecture

```
        Vault KV v2:  homelab/canary
                 ▲
                 │ read, authorised by policy vso-canary-read
                 │
        Vault Kubernetes auth  ──TokenReview──►  k3s API server
                 ▲
                 │ presents its ServiceAccount JWT
                 │
   Vault Secrets Operator  (chart 1.5.1)
                 │
                 ▼ writes and keeps in sync
        Secret  vault-canary   (namespace vault)
```

No credential is stored anywhere in this chain. VSO presents the JWT k3s already
mounts into its pod; Vault validates it by calling TokenReview, which it is
already entitled to do.

Git carries the *instruction* — which mount, which path, which destination —
and never the value. That is the property the roadmap asks for.

## 5. Repository layout

**New:**

| Path | Contents |
| --- | --- |
| `platform/vault/configure-vault.sh` | Idempotent Vault configuration. **Human-run, not reconciled** |
| `platform/vault-secrets-operator/values.yaml` | VSO Helm values |
| `platform/vault-secrets-operator/config/vault-secrets.yaml` | `ServiceAccount`, `VaultConnection`, `VaultAuth`, `VaultStaticSecret` |
| `platform/vault-secrets-operator/README.md` | What the path is, how to verify it, how it fails |
| `environments/homelab/apps/vso-operator.yaml` | Application, wave `11` |
| `environments/homelab/apps/vso-config.yaml` | Application, wave `22` |

**Modified:** `bootstrap/namespaces/namespaces.yaml` (add
`vault-secrets-operator-system`); `platform/vault/README.md` (the ceremony
grows a step); `README.md`; `docs/troubleshooting.md`; and
`docs/superpowers/specs/2026-09-03-vault-design.md`, whose §11 claim that
`operator-oauth` can migrate is corrected by §10 below — with a dated note
rather than a silent edit.

The script lives under `platform/vault/` rather than beside VSO because what it
configures is **Vault** — the KV mount and the auth method outlive any single
consumer, and a second consumer in phase 18 will extend the same script rather
than write its own.

The `config/` subdirectory mirrors `platform/vault/config/` and
`infrastructure/ingress/config/`: a directory source applies every YAML beneath
it as a manifest, and `values.yaml` has no `kind`.

## 6. Sync waves

| Wave | Application | Creates |
| --- | --- | --- |
| 10 | `vault` | Vault, unsealer |
| 11 | `vso-operator` | VSO chart, 10 CRDs |
| 21 | `ingress-config` | ProxyClass, every Ingress |
| 22 | `vso-config` | `VaultConnection`, `VaultAuth`, `VaultStaticSecret` |

**Why `vso-config` is after ingress, not beside its operator.** A
`VaultStaticSecret` only becomes Healthy once Vault holds the KV mount, the
policy and the role — and those come from a script a human runs (S4). At wave
12 this Application would gate wave 21, so an operator who unsealed Vault but
forgot the script would silently lose **every tailnet URL**, with nothing naming
the script as the cause.

That is precisely the failure phase 16 removed by moving `ingress-config` to 21,
and re-introducing it one phase later would be an unforced error. Nothing
consumes the canary, so the late placement costs nothing.

`vso-operator` stays at 11 because its health is independent of Vault entirely:
its pods start, become Ready, and report Healthy whether or not Vault is
configured, unsealed, or even running.

> **Correction, 2026-09-06 (whole-phase review, post-implementation).** The
> paragraph above is the wrong conclusion from a true premise. Wave 11 gates
> wave 21, so a wave-11 `vso-operator` sits in front of `ingress-config` —
> the ProxyClass, every Ingress, and Argo CD's own — regardless of whether
> its own health depends on Vault. That is exactly the coupling phase 16
> removed by moving `ingress-config` to 21, reintroduced one phase later by
> a component that did not need to be early at all: nothing needs VSO
> running before wave 22, where its CRs actually get created. `vso-operator`
> was moved to wave 21, alongside `ingress-config`, after the VSO manager
> was observed restarting once under host memory pressure (see
> `docs/troubleshooting.md` entry 10) turned the risk from hypothetical to
> demonstrated. See `environments/homelab/apps/vso-operator.yaml` for the
> corrected placement.

## 7. Vault configuration — the unreconciled part

`platform/vault/configure-vault.sh`, run by the operator with their root token.
Every step either checks before acting or overwrites with the same value, so
a rebuild re-runs it safely. Steps 4-6 (the auth config, the policy, and the
role) are the overwriting kind, not the checking kind — safe because the
script always writes the same values from git, but meaning a hand-edit made
directly in Vault to the policy or role is silently reverted the next time
this script runs.

1. Mount KV v2 at `homelab/`
2. Seed `homelab/canary`
3. Enable the `kubernetes` auth method
4. Configure it with the in-cluster API server address
5. Write policy `vso-canary-read`, granting `read` on **`homelab/data/canary`**
6. Create role `vso-canary`, binding ServiceAccount **`vault-canary` in
   namespace `vault`** to that policy, with the audience set to `vault`

**The `data/` segment in step 5 is not a typo.** KV v2 stores values one level
below the mount, so a policy written against `homelab/canary` matches nothing
and produces a permission denial that reads exactly like a wrong path. It is
the most common mistake made with KV v2 and it is worth a comment in the script
itself.

The script prompts for the token with `vault login` rather than accepting it as
an argument or an environment variable (S5), for the same reason the unsealer
reads its keys from files: arguments are visible in `ps` to every user on this
host.

**This file is not reconciled by Argo CD**, and its README says so as loudly as
`infrastructure/networking/README.md` does for the tailnet policy. Committing
configuration that a human must apply is a deliberate trade, not an oversight —
the alternative puts a standing Vault superuser credential in the cluster.

## 8. The custom resources

Three objects, all in `platform/vault-secrets-operator/config/`:

- **`VaultConnection`** — where Vault is: `http://vault.vault.svc:8200`. Plain
  HTTP in-cluster, matching the posture used for Argo CD and Vault's own UI
  (phase 16, V9). Single node; pod traffic never leaves the host.
- **`VaultAuth`** — how to authenticate: the `kubernetes` mount, role
  `vso-canary`, ServiceAccount `vault-canary`, audience `vault`.
- **`VaultStaticSecret`** — what to fetch. Required fields are `mount`, `path`,
  `type` and `destination`; `refreshAfter` sets the poll interval.
- **`ServiceAccount vault-canary`** — the identity that authenticates (§9).
  Four objects, not three; it is listed here because it is easy to mistake for
  incidental scaffolding when it is the thing Vault actually trusts.

All live in namespace `vault`, which `VaultAuth` requires (§9). `VaultConnection`
requires both `address` and `skipTLSVerify`.

The canary materialises as Secret `vault-canary` in namespace `vault`. It is
permanent and deliberately boring: its only job is to be the first thing checked
when the path appears broken. VSO's failure mode is a Secret that quietly stops
updating, so a known-good object with a known value is worth its negligible cost.

## 9. Which ServiceAccount authenticates

**Corrected 2026-09-05, while writing the implementation plan.** An earlier
draft of this spec bound the Vault role to the operator's own ServiceAccount,
`vso-vault-secrets-operator-controller-manager`, and treated the resulting
dependency on Helm's release name as a headline risk.

That was wrong, and the `VaultAuth` CRD states the rule itself:

> `serviceAccount`: ServiceAccount to use when authenticating to Vault's
> authentication backend. **This must reside in the consuming secret's
> (VDS/VSS/PKI) namespace.**

The authenticating identity must therefore live in namespace `vault`, beside the
`VaultStaticSecret` — not in the operator's namespace. VSO's ClusterRole grants
`serviceaccounts/token: create` cluster-wide, so it can mint a token for any
ServiceAccount named this way.

This phase creates its own ServiceAccount, `vault-canary` in namespace `vault`,
and Vault's role binds `system:serviceaccount:vault:vault-canary`.

Two consequences, both good:

- **The release-name coupling disappears entirely.** No string in the script
  refers to anything Helm generates. `releaseName: vso` stays pinned (S11), but
  only for predictable object names; authentication no longer depends on it.
- It follows VSO's own guidance of a distinct ServiceAccount per Vault role, so
  a second consumer in phase 18 cannot silently inherit the canary's access.

**The coupling that remains** is narrower and unavoidable: the script writes
`system:serviceaccount:vault:vault-canary` into a Vault role, and a manifest
creates a ServiceAccount of that name. Two files must agree on one string. Both
carry a comment naming the other, and §14 verifies the binding by
authenticating rather than by reading either file.

The **audience must match on both sides**: `VaultAuth` requests a token with
audience `vault`, and the Vault role is configured to expect `vault`. A mismatch
produces a permission denial that names neither side as the cause.

## 10. Why `operator-oauth` cannot move into Vault

The phase-16 spec (§11) said `operator-oauth` "can migrate, but only once phase
17 provides the Vault Secrets Operator." **That is wrong, and this spec
supersedes it.**

The Tailscale operator mounts `operator-oauth` at wave 2. Vault does not exist
until wave 10, and wave 2 gates wave 10 — so on a rebuild the operator would
wait for a secret that requires Vault, which requires wave 2 to have completed.
The cluster would never converge.

Reordering does not rescue it. Vault cannot serve any secret until a human has
performed the unseal ceremony, so placing Vault before the Tailscale operator
would make every tailnet URL — including Argo CD's — depend on a human being
present during a rebuild.

So `operator-oauth` joins `repo-homelab` in the category of credentials that can
never live in Vault, for the same underlying reason stated differently: **a
credential required to bootstrap the cluster cannot live in something the
cluster bootstraps.** `repo-homelab` because Argo CD needs it to clone this
repository; `operator-oauth` because of wave ordering.

The count of cluster-only secrets therefore stays at **three** after this phase.
Phase 16 predicted it would fall to two. It will not, and the honest accounting
belongs here rather than in a later phase's surprise.

## 11. What this phase does not deliver

- Migrating any existing secret (§10)
- Dynamic secrets, PKI, or database credential generation — phase 18 at the
  earliest, and only if a real consumer wants them
- Any Vault policy beyond a single read grant on a single path
- Metrics scraping of VSO — phase 22
- Rotation, leases, or TTL tuning — nothing yet has a lifetime worth managing
- Revoking the phase-16 root token. It should be revoked, but doing it in the
  same phase that first depends on Vault working would remove the credential
  needed to diagnose a failure

## 12. Cost

Two containers, because the chart's `kube-rbac-proxy` sidecar has no disable
flag in 1.5.1. Chart defaults request 128Mi and limit 256Mi across both — more
than Vault's entire measured footprint. Both will be trimmed, since the manager
reconciles one object and the proxy fronts a metrics endpoint nothing scrapes.

`quay.io/brancz/kube-rbac-proxy:v0.18.1` is the **first non-HashiCorp image and
the second registry** in this cluster. Worth stating plainly rather than
discovering later during an outage.

## 13. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| KV v2 policy written without the `data/` segment | **High — near-certain if unexamined** | §7 states it; the script carries a comment; the troubleshooting entry leads with it |
| ~~Release name changed, breaking the Vault role binding~~ | **Eliminated** | §9 — the design no longer references any Helm-generated name |
| ServiceAccount name or token audience disagree between the script and the manifests | Medium | §9; reciprocal comments; verification authenticates rather than reads. Argo CD reports Synced while auth fails, so the check must be a login |
| Rebuild unseals Vault but skips the config script | Medium | Ceremony documented as one procedure in two places; wave 22 placement means the damage is confined to the canary, not ingress |
| Vault's config is imperative and unversioned in-cluster | Accepted | S4; the script is committed and idempotent, so the *intent* is versioned even though the state is not |
| VSO fails silently — Secret stops updating, nothing alerts | Medium | S6's canary is the deliberate answer; real alerting waits for phase 22 |
| Two new images, one from a new registry | Low | §12 states it; both pinned |
| Kubernetes auth misconfigured against k3s's issuer | Low | Issuer and TokenReview entitlement both verified in §2 before design |

## 14. Verification

The phase is complete when all hold:

1. `vso-operator` and `vso-config` are Synced/Healthy, and `vso-config`'s
   `status.resources` actually lists the three custom resources — not merely a
   green Application.
2. Secret `vault-canary` exists in namespace `vault` and its value matches what
   was written to `homelab/canary`, **verified by reading both**.
3. The value was placed there by VSO, not by hand — proven by changing the value
   in Vault and observing the Secret follow within `refreshAfter`.
4. VSO authenticated by ServiceAccount, not by a stored credential. No audit
   device is enabled (phase 16 left `auditStorage` off), so the evidence is
   VSO's own logs showing a `kubernetes` auth login, plus the absence of any
   Vault token in any Kubernetes Secret — checked, not assumed.
5. `configure-vault.sh` is idempotent — running it a second time changes
   nothing and exits cleanly.
6. All Applications Synced/Healthy, both tailnet URLs still 200, and the Argo CD
   Ingress not recreated.
7. Memory measured before and after and recorded, not estimated.
8. Deleting Secret `vault-canary` results in VSO recreating it — the property
   that makes this a managed path rather than a one-time copy.
