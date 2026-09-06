# Vault Secrets Operator

The path by which a Kubernetes Secret gets its value from Vault, kept in
sync, with no human ever copying it by hand:

```
Vault KV v2            Vault Secrets Operator          Kubernetes Secret         Workload
homelab/data/canary  →  authenticates via k8s SA   →   vault-canary (ns vault)  →  mounts/env
                         polls every refreshAfter=60s
```

Chart `hashicorp/vault-secrets-operator` 1.5.1 (app 1.5.1), from
`https://helm.releases.hashicorp.com`.

| Path | Contents |
| --- | --- |
| `values.yaml` | Helm values for the controller |
| `config/vault-secrets.yaml` | `ServiceAccount`, `VaultConnection`, `VaultAuth`, `VaultStaticSecret` |

The chart's own `defaultVaultConnection` and `defaultAuthMethod` are
deliberately disabled. `VaultConnection` and `VaultAuth` are committed as
plain manifests in `config/` instead, so they land at sync-wave 22 via the
`vso-config` Application — after `ingress-config` (21) — because their
health depends on Vault configuration a human applies by hand, and nothing
that gates ingress may depend on that. The operator itself (`vso-operator`)
sits at wave 21, alongside `ingress-config` rather than right after `vault`
at 10: its pods start and become Ready whether or not Vault is configured,
but that made an early wave look safer than it was — an early `vso-operator`
would still gate `ingress-config`, putting a new component in front of every
tailnet URL for no reason, since nothing needs VSO running before wave 22.

## The ceremony

Vault's mount, auth method, policy and role are not Kubernetes objects, so
nothing reconciles them. **`configure-vault.sh` is not reconciled by Argo
CD**, the same as `infrastructure/networking/policy.hujson` for the tailnet
ACL — committing it records the intent, it does not apply it. Three
commands, run by hand, in this order, the middle one from the repository
root (it redirects a local file into the pod, so the relative path only
resolves there):

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
    sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'

The first prompts for the root token at a hidden prompt — never pass it as
an argument or environment variable, both are visible in `ps` to every user
on this host. The third removes the pod-local copy of the token the first
command created; without it, a login persists in the pod indefinitely.

`configure-vault.sh` is idempotent: every step either checks before acting
or overwrites with the same value, so re-running it is safe on a rebuild.
Not every step is the first kind — the auth config, the policy, and the
role are all written unconditionally on every run, which is safe only
because the script always writes the same values from git. **The
consequence a later phase should know:** a hand-edit made directly in
Vault to the `vso-canary-read` policy or the `vso-canary` role does not
survive the next run of this script — it is silently overwritten back to
whatever `configure-vault.sh` says, with no warning that anything changed.
This was confirmed live — a second run left `homelab/canary`'s value
untouched (a checked step) and exited cleanly.

**Known limitation: once the third command runs, Vault cannot be inspected
without logging in again.** There is no way to read back the mount, policy,
or role from outside without a fresh `vault login`. The better route is not
to log back in but to check the `VaultStaticSecret`'s own status instead
(see below) — it proves every element of the ceremony at once, because none
of it can sync if any part is wrong. Do not keep the token in the pod to
make inspection easier; that reintroduces the standing credential the
ceremony exists to avoid.

## The four objects

All four live in namespace `vault` — required, not a preference, because the
`VaultAuth` CRD states its ServiceAccount "must reside in the consuming
secret's namespace."

| Object | Name | Role |
| --- | --- | --- |
| `ServiceAccount` | `vault-canary` | The identity Vault actually trusts |
| `VaultConnection` | `vault` | Where Vault is: `http://vault.vault.svc:8200` |
| `VaultAuth` | `vault-canary` | How to authenticate: `kubernetes` mount, role `vso-canary`, this ServiceAccount, audience `vault` |
| `VaultStaticSecret` | `vault-canary` | What to fetch: `homelab` mount, `kv-v2`, path `canary`, `refreshAfter: 60s` |

The `ServiceAccount` is listed here rather than treated as scaffolding
because it is the thing authenticated, not incidental plumbing. VSO's
ClusterRole can mint a token for any ServiceAccount named this way, and
Vault's role binds `system:serviceaccount:vault:vault-canary` directly — the
`VaultAuth` CRD requires that ServiceAccount to live in this same namespace,
so it could not be the operator's own ServiceAccount (which lives in
`vault-secrets-operator-system`) even if that seemed simpler.

## The three strings that must agree

A shell script (`platform/vault/configure-vault.sh`) and a manifest
(`config/vault-secrets.yaml`) must agree on three literal strings:

| String | Value |
| --- | --- |
| Role | `vso-canary` |
| ServiceAccount | `vault-canary` |
| Audience | `vault` |

A mismatch in any one of them is a permission denial that names neither
side — Vault reports only that the login failed, not which of the three
disagreed. **Argo CD will report everything Synced while this happens.**
Both `vso-config` and `vault` can be green while the canary Secret quietly
stops updating. See `docs/troubleshooting.md` entry 10 for the two `grep`
commands that check agreement mechanically, and the general point that a
Synced Application describes what Argo CD applied, never what is working.

## Why the canary exists

It is tempting to call `vault-canary` a health check. It is not, quite —
nothing scrapes it and nothing alerts on it until phase 22, so a stale
canary is exactly as silent as a stale real secret would be. What it
actually is: the only thing in the cluster that exercises the Kubernetes
auth path end to end, and the only detector for drift across the seam
between `configure-vault.sh` (which Argo CD cannot see) and the manifests
in `config/` (which Argo CD self-heals). Without it, a wrong role, a wrong
audience, or a wrong bound ServiceAccount sits undetected until phase 18's
first real consumer fails — at which point it looks like a bug in that
consumer, not in this seam. Treat it as a drift detector that must be
looked at, not an alarm that pages anyone.

## What proof exists that this works

`VaultStaticSecret` reports its own status, independent of Argo CD:

    sg k3s-admin -c 'kubectl -n vault get vaultstaticsecret vault-canary -o jsonpath={.status.conditions[*].message}'

which prints something like:

    Secret synced, horizon=52.2s VaultStaticSecretHealthy VaultStaticSecretReady

The `kubernetes-` prefix on the `vaultClientMeta.cacheKey` in that status,
and an `entityID` in VSO's own logs, are the evidence that authentication
used the Kubernetes auth method rather than a stored token. **No audit
device is enabled** on this Vault (left off in phase 16), so VSO's logs and
this status are the only evidence available — this documents what was
checked, not proof from Vault's own audit trail, which does not exist here.

Proven live, all four:

1. Changing `homelab/canary` in Vault propagated to the Secret.
2. Deleting the Secret caused VSO to recreate it, observed in **~5 seconds**,
   with no human action.
3. Zero Vault tokens exist in any Kubernetes Secret cluster-wide.
4. Re-running `configure-vault.sh` left the canary value untouched — the
   ceremony is idempotent.

### Argo CD's health reporting for these CRDs is unproven

Argo CD has no custom health check configured for `secrets.hashicorp.com`
kinds and falls back to looking for a generic `Ready` condition. VSO does
set one, so a healthy path shows up as `Healthy` — but whether Argo CD
reports `Degraded` on a real Vault auth failure has never been observed on
this cluster. Treat that as unknown rather than assumed. The practical
guidance is the same regardless: **check the `VaultStaticSecret`'s own
status, not the Application's health.**

## Measured resource usage

| Pod | CPU | Memory (limit) |
| --- | --- | --- |
| VSO manager | 2m | **36Mi** (limit 96Mi) |
| VSO `kube-rbac-proxy` | 1m | **11Mi** (limit 64Mi) |
| **Total** | | **47Mi** |

Measured 2026-09-06, not estimated. `kube-rbac-proxy` is mandatory in chart 1.5.1 — no
flag disables it — and fronts a metrics endpoint nothing scrapes until
phase 22. Cluster pod count went from 18 to **19**. For context: Vault
itself measured 98Mi in phase 16, so this phase adds roughly half again on
top of the secret store it serves.

## A real fault, observed

The VSO manager restarted once, about 8 hours after install
(`restartCount=1`, `reason=Error`, `exitCode=1` — **not** `OOMKilled` /
`137`). Its previous logs showed:

    Error retrieving lease lock ... context deadline exceeded
    Failed to renew lease
    problem running manager: leader election lost

A slow API server call timed out, the manager lost leader election, and
restarted cleanly. The host was under memory pressure at the time (ARK
running at 7.95GB, host available memory down to ~776Mi, swap fully
consumed). Measured usage is 36Mi against a 96Mi limit, so the trimmed
limits in `values.yaml` were **not** the cause — see
`docs/troubleshooting.md` entry 10 for how to tell this fault apart from a
genuine OOM.

## `configure-vault.sh` is not reconciled by Argo CD

Said here as loudly as `infrastructure/networking/README.md` says it for
the tailnet ACL policy: this script is versioned intent, applied by hand.
Committing it does not apply it, pushing it does not apply it, and Argo CD
never runs it. A rebuild that unseals Vault but skips this ceremony leaves
`vso-config` unhealthy at wave 22 — see `platform/vault/README.md` for the
ceremony's place in the full unseal-and-configure sequence, and the root
`README.md`'s "First install / rebuild" section for why that specific
failure costs no tailnet URL.
