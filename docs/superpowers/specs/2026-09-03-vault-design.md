# Vault — Design

Covers workstation-plan phase 16. Phase 17 (Vault Secrets Operator) is a
separate subsystem and gets its own spec; §13 says what is deliberately
left to it.

## 1. Goal

A running, persistent, automatically-unsealing Vault at
`https://vault.taildf6cd4.ts.net`, reconciled by Argo CD, with a verified
snapshot procedure and a documented recovery path.

Phase 16 delivers the secret *store*. Nothing consumes it until phase 17.

## 2. Measured starting state

Measured 2026-09-03 on `slqzeer-ms7c56`.

| Fact | Value |
| --- | --- |
| k3s | v1.36.4+k3s1, single node |
| Argo CD | 5 Applications, all Synced/Healthy |
| Ingress | Tailscale operator; `argocd.taildf6cd4.ts.net` live with Let's Encrypt TLS |
| Existing waves | `argocd` -1, `namespaces` 0, `ingress-operator` 2, `ingress-config` 3 |
| `vault` namespace | Exists, empty (created at wave 0) |
| StorageClass | `local-path (default)`, `WaitForFirstConsumer`, `ALLOWVOLUMEEXPANSION false` |
| **PVs / PVCs on this cluster** | **NONE — zero, ever. Local-path is entirely unproven here** |
| `/srv/kubernetes/storage` | `root:k3s-admin`, ACL with `mask::---`; no `default:` ACL |
| `/srv` free space | 853G of 938G |
| **Memory** | **8.1Gi available — ARK is stopped; the constraint of phases 13-15 is gone** |
| Swap | 504Mi of 512Mi still consumed, stale from earlier pressure |
| Cluster-only secrets | 2: `repo-homelab`, `operator-oauth` |

The memory situation has inverted since 2026-09-02, when 652Mi was
available. Decisions below are still bounded, but memory is no longer the
binding constraint. **Storage is**, because it has never once been used.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| V1 | HashiCorp Vault, official chart `0.34.1`, app `2.0.4` | User's explicit choice. Chart from `https://helm.releases.hashicorp.com`; image `hashicorp/vault:2.0.4` |
| V2 | Raft integrated storage, **1 replica** | The plan requires "snapshots Vault". Only Raft has a native snapshot API producing a consistent backup from a running Vault. `file` storage would mean copying a live directory |
| V3 | Auto-unseal via an in-cluster helper reading keys from a Secret | User's explicit choice. Vault OSS has no local-file seal type, so this is a helper calling the unseal API, not a native seal |
| V4 | The helper reuses the `hashicorp/vault` image | No third-party unsealer image. This phase adds exactly one new image to the cluster |
| V5 | Unseal keys piped on **stdin**, never as arguments | Command arguments are visible in `ps` to every user on the host. `vault operator unseal $KEY` would leak the keys |
| V6 | `vault operator init` is run by hand | Automating it puts unseal keys and the root token through Job logs and the k3s datastore |
| V7 | Injector and CSI disabled | Phase 17 uses the Vault Secrets Operator instead. The chart enables the injector by default via its `"-"` sentinel |
| V8 | `ingress-config` moves from wave 3 to **21** | Removes the wave coupling that would block Vault. See §6 |
| V9 | TLS terminates at the tailnet proxy; Vault serves HTTP in-cluster | Same posture as Argo CD. Single node — pod traffic never leaves the host. cert-manager remains the upgrade path |
| V10 | Backup automation deferred to phase 27 | The plan schedules it there. This phase delivers a verified manual procedure |
| V11 | Storage proven before Vault is installed | §9. No PVC has ever existed on this cluster |

## 4. Architecture

```
you ──https──► vault.taildf6cd4.ts.net     (tailnet proxy terminates TLS,
                     │                       Let's Encrypt cert)
                     │  http, in-cluster
                     ▼
              vault-0  (StatefulSet, 1 replica)
                     │
                     ├── Raft integrated storage
                     │      └── PVC (local-path) ──► /srv/kubernetes/storage
                     ▲
                     │  unseal API
              vault-unsealer (Deployment, 1 replica)
                     └── reads 3 keys from Secret `vault-unseal-keys`
                         mounted optional at /unseal
```

## 5. Repository layout

**New:**

| Path | Contents |
| --- | --- |
| `platform/vault/values.yaml` | Vault Helm values |
| `platform/vault/unsealer.yaml` | The auto-unseal Deployment and its ConfigMap script |
| `platform/vault/README.md` | Init ceremony, unseal keys, snapshots, recovery |
| `infrastructure/ingress/config/vault-ingress.yaml` | Ingress for the Vault UI |
| `environments/homelab/apps/vault.yaml` | Application, sync-wave `10` |

**Modified:** `environments/homelab/apps/ingress-config.yaml` (wave 3 → 21);
`README.md`; `docs/troubleshooting.md`.

`platform/vault/.gitkeep` is removed once the directory has content.

The unsealer lives in `platform/vault/` beside Vault rather than in its own
component directory: it has no independent purpose, its lifecycle is Vault's,
and separating them would invite someone to delete one without the other.

## 6. Sync waves, and closing the coupling this repo already has

| Wave | Application | Creates |
| --- | --- | --- |
| -1 | `argocd` | Argo CD's own release |
| 0 | `namespaces` | `cert-manager`, `vault`, `tailscale` |
| 2 | `ingress-operator` | Tailscale operator, CRDs, `IngressClass` |
| 10 | `vault` | Vault StatefulSet, unsealer |
| 21 | `ingress-config` | ProxyClass, Argo CD Ingress, Vault Ingress |

**Why `ingress-config` moves to 21.** It currently sits at wave 3, and a
wave gates every wave after it. An Ingress is `Progressing` until its
controller populates `status.loadBalancer.ingress`, and that depends on the
Tailscale control plane — the OAuth credential, the tailnet policy, Let's
Encrypt. If any of those breaks, `ingress-config` never goes Healthy and
**Vault at wave 10 would never sync at all**, with no error naming ingress
as the cause. This was recorded as a known risk on 2026-09-02 and deferred
to this phase.

The fix is one annotation, not a restructuring. `ingress-config` holds only
the ProxyClass and Ingresses, and nothing at an earlier wave consumes
either: the ProxyClass is read only when a proxy is created, which happens
only because an Ingress exists. Moving the whole Application to 21 changes
no file paths, prunes nothing, and recreates no proxy, device, or
certificate — an important property, because pruning and recreating the
Argo CD Ingress is precisely the operation that produced an orphaned
finalizer and a hostname collision on 2026-09-02.

Consequence, accepted: on a rebuild from scratch no tailnet URL resolves
until wave 21. `kubectl port-forward` covers that window, which is why
it stays documented.

## 7. The unseal helper

A Deployment, one replica, running `hashicorp/vault:2.0.4` with a shell
loop. `vault status` exits `0` unsealed, `2` sealed, `1` on error — so no
JSON parsing and no `jq` dependency.

```sh
export VAULT_ADDR=http://vault-0.vault-internal:8200
while true; do
  vault status >/dev/null 2>&1
  if [ $? -eq 2 ]; then
    for k in /unseal/key1 /unseal/key2 /unseal/key3; do
      [ -s "$k" ] || continue
      vault operator unseal - < "$k" >/dev/null 2>&1 || true
    done
  fi
  sleep 10
done
```

Three properties are deliberate:

- **Keys arrive on stdin from mounted files.** Never as arguments (V5),
  never echoed, never logged.
- **The Secret mount is `optional: true`.** The helper deploys before the
  Secret exists, waits harmlessly, and begins working the moment the Secret
  is created. There is no ordering requirement between deploying Vault and
  performing the init ceremony, on a first install or a rebuild.
- **It addresses `vault-0.vault-internal` directly**, the headless service,
  not the `vault` service. A sealed Vault is not Ready, so a normal Service
  may have no endpoints exactly when the helper needs to reach it.
  Implementation must confirm the headless service name the chart creates.

Failures are tolerated and retried: unsealing an uninitialized Vault fails
harmlessly, which is the state between deployment and the init ceremony.

## 8. The init ceremony

Run once, by hand, by the operator. Not automated (V6).

1. Argo CD deploys Vault. It comes up **sealed and uninitialized**. This is
   the expected state, not a failure.
2. Operator runs `vault operator init -key-shares=5 -key-threshold=3`.
3. **All five unseal keys and the root token go to a password manager.**
   This is the only copy not on this machine.
4. Operator creates the Secret from three of the keys:

   ```bash
   kubectl create secret generic vault-unseal-keys -n vault \
     --from-literal=key1=<key1> \
     --from-literal=key2=<key2> \
     --from-literal=key3=<key3>
   ```

5. The helper unseals within its poll interval.

**Why five shares when the cluster holds a quorum of three anyway?** It
buys nothing today and costs nothing. It preserves the option of later
withdrawing the keys from the cluster and splitting custody among people
without re-initializing Vault. Initialising 1-of-1 would close that door
permanently.

**The root token is not a permanent credential.** It should be revoked once
phase 17 establishes real auth methods. Documented as such rather than left
implying a standing superuser token is the intended steady state.

## 9. Storage — the unproven path

No PersistentVolume or PersistentVolumeClaim has ever existed on this
cluster (§2). `docs/troubleshooting.md` entry 1 explicitly predicts that
PVC directories under `/srv/kubernetes/storage` will bite non-root pods,
naming phase 16 as where it resurfaces. The Vault chart ships
`securityContext: {}`, so the container runs as whatever UID its image
declares — a non-root process mounting a PVC.

Therefore Vault is **not installed until storage is proven**. The first
implementation task creates a PVC and a non-root pod that writes a file,
then confirms the data survives a pod restart. Only then does Vault follow.

If the probe fails, the ACL on `/srv/kubernetes/storage` is repaired using
the method that worked in phase 12 — strip inheritable ACLs, correct the
mode, preserve the security intent — and the probe is repeated. Vault does
not proceed on unproven storage.

`local-path` does not enforce capacity: a PV is a directory on `/srv`,
which has 853G free. The PVC size is therefore nominal, not a quota.

## 10. Backups and recovery

`vault operator raft snapshot save` produces a consistent snapshot from a
running Vault. Destination `/backups/vault`, per the plan.

**Scheduled automation is out of scope** (V10) — the plan places backup
automation at phase 27. This phase delivers a verified manual command so
that phase 27 schedules something already known to work.

**The recovery invariant, which must be stated wherever backups are:**

> A Raft snapshot is encrypted with Vault's master key. Restoring it into a
> fresh Vault requires the same unseal keys. The snapshot alone is
> worthless; the keys alone are worthless. Recovery needs both, and they
> are deliberately stored in different places — snapshots on disk at
> `/backups/vault`, keys in a password manager. If they ever end up in the
> same place, that place becomes a single point of total compromise and
> total loss simultaneously.

This belongs in `README.md`'s recovery section, not only in a component doc.

## 11. Credentials, and honest accounting

Phase 16 **increases** the number of secrets that exist only in this
cluster, from two to three:

| Secret | Namespace | Loss impact |
| --- | --- | --- |
| `repo-homelab` | `argocd` | All Argo CD reconciliation stops |
| `operator-oauth` | `tailscale` | All tailnet ingress stops |
| `vault-unseal-keys` | `vault` | Vault cannot auto-unseal; manual unseal from the password manager still works |

**`repo-homelab` can never move into Vault.** Argo CD needs that deploy key
to clone this repository, which is how Vault is deployed. A credential
required to deploy the secret store cannot live in the secret store.

`operator-oauth` can migrate, but only once phase 17 provides the Vault
Secrets Operator. So the trajectory is 2 → 3 after this phase → 2 after
phase 17, and the real payoff is that application secrets from phase 18
onward have somewhere proper to live.

`vault-unseal-keys` is recoverable from the password manager, unlike the
other two. That is the one genuinely new safety property this phase adds.

**Security posture, stated plainly:** the unseal keys live in the same
cluster as the data they protect. Vault therefore protects against disk
theft, backup exposure, and accidental commitment to git. It does **not**
protect against compromise of this host, since an attacker with root
obtains both the ciphertext and the keys. This is the trade the user chose
knowingly (V3); it is the common homelab posture and it is not the same as
Vault's usual security story.

## 12. Exposure

`https://vault.taildf6cd4.ts.net`, UI enabled, following the pattern
established for Argo CD: `ingressClassName: tailscale`, the **short** name
`vault` in `spec.tls[0].hosts[0]`, and the `tailscale.com/proxy-class:
homelab` annotation so the proxy is bounded at 128Mi.

Costs one additional proxy pod, roughly 30Mi measured.

## 13. Out of scope

- **Vault Secrets Operator** — phase 17, its own spec
- Migrating `operator-oauth` into Vault — needs phase 17
- Scheduled backups — phase 27
- Any auth method beyond the root token — phase 17
- Vault policies and roles — nothing consumes Vault yet
- cert-manager and in-cluster TLS — still deferred (V9)
- Vault HA with more than one replica — single node
- Vault Agent Injector, CSI provider (V7)
- Audit device storage

## 14. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **Local-path PVC has never worked here; entry 1 predicts non-root failure at this phase** | **High** | §9 — prove storage with a probe before Vault; repair ACLs and retry if it fails |
| Unseal keys and data on the same host | Accepted | User's explicit choice (V3); §11 states the posture rather than implying Vault's usual one |
| Snapshot and keys separated — recovery needs both | High if forgotten | §10 invariant documented in README, not only a component doc |
| Init ceremony performed twice, or keys lost before storage | High | §8 makes it a single explicit operator step with the password manager first |
| Vault 2.0.x is a major version; chart 0.34.1 is recent | Medium | Pin both; verify `vault status` and the unseal API behave as documented rather than assuming |
| Root token left as a standing superuser | Medium | §8 documents revocation once phase 17 lands |
| Wave 21 move delays all tailnet URLs on a rebuild | Low | Accepted (§6); port-forward covers the window |
| Vault 2.0.4 is BUSL-licensed | Low | Permitted for internal homelab use. OpenBao is the Apache-licensed fork if this ever matters |

## 15. Verification

The phase is complete when all hold:

1. A non-root pod writes to a local-path PVC and the data survives a pod
   restart — **before** Vault is installed (§9).
2. `vault-0` is Running; a PVC is Bound; its directory exists under
   `/srv/kubernetes/storage`.
3. Vault reports initialized and **unsealed**, having been unsealed by the
   helper rather than by hand — verified by deleting the Vault pod and
   confirming it returns to unsealed with no human action.
4. `https://vault.taildf6cd4.ts.net` serves the UI with a valid Let's
   Encrypt certificate, verified **without** `curl -k`, and confirmed from
   the `msi` machine.
5. `vault operator raft snapshot save` produces a non-empty snapshot file,
   and its size and format are recorded.
6. `kubectl port-forward` still reaches Argo CD (the ingress fallback
   survives the wave move).
7. All Applications Synced/Healthy, including after the `ingress-config`
   wave change, with the Argo CD Ingress **not** recreated — confirmed by
   its unchanged tailnet hostname and certificate.
8. Memory measured before and after; the figure recorded rather than
   estimated.
