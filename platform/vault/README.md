# Vault

HashiCorp Vault, chart 0.34.1 (Vault 2.0.4), single replica with Raft
integrated storage on a local-path PVC.

Reachable at <https://vault.taildf6cd4.ts.net> from any tailnet device.
`/` returns **307**, redirecting to `/ui/`, which returns **200** — that is
the expected pair of status codes, not a broken redirect. Certificate
observed: `subject=CN=vault.taildf6cd4.ts.net`,
`issuer=C=US, O=Let's Encrypt, CN=YE1`, valid 2026-09-03 to 2026-12-02.

| Path | Contents |
| --- | --- |
| `values.yaml` | Helm values. **Not** applied as a manifest |
| `config/unsealer.yaml` | Auto-unseal Deployment, script inline in the pod template |

The `vault` Application has three sources: the chart, this directory's
`values.yaml` via the `values` ref, and `platform/vault/config` as a plain
manifest path. All three are required — the unsealer is not part of the
chart, and it once lived in git with no Application source pointing at it,
so Argo CD reported the `vault` Application Synced/Healthy while the
Deployment simply did not exist. It stays a source of this Application
rather than an Application of its own so that Vault and the helper that
unseals it cannot be deleted independently of each other. See
`docs/troubleshooting.md` entry 9 for the full story.

## Security posture — read this before trusting it

The unseal keys live in a Kubernetes Secret in the same cluster as the data
they protect. Vault here defends against **disk theft, backup exposure, and
accidental commits to git**. It does **not** defend against compromise of
this host: anyone with root gets both the ciphertext and the keys.

This trade was chosen deliberately so that Vault recovers by itself after a
reboot. It is the common homelab posture, and it is not Vault's usual
security story. Do not describe this Vault as protecting secrets from an
attacker who is already on the machine.

## The init ceremony

Vault comes up sealed and uninitialized. This runs once, by hand.

1. Initialise:

       sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault operator init -key-shares=5 -key-threshold=3'

2. **Put all five unseal keys and the root token in a password manager
   immediately.** This is the only copy not on this machine. Lose it and the
   data is permanently unreadable.

3. Create the Secret the helper reads, from three of the keys. Run this
   interactively and paste each value in place of the placeholder — do not
   put real keys in shell history or a script file:

       sg k3s-admin -c 'kubectl create secret generic vault-unseal-keys -n vault --from-literal=key1=PASTE_KEY1_HERE --from-literal=key2=PASTE_KEY2_HERE --from-literal=key3=PASTE_KEY3_HERE'

   The names `key1`, `key2`, `key3` are fixed by the helper script. Other
   names give you a helper that runs and silently never unseals.

4. The helper unseals within about ten seconds.

5. **Configure Vault.** Unsealing alone leaves Vault empty — no secrets
   engine, no auth method, no policy, no role. Run
   `platform/vault/configure-vault.sh` inside the pod, as described in
   `platform/vault-secrets-operator/README.md`. A rebuild that unseals but
   does not configure leaves `vso-config` unhealthy at wave 22.

Five shares with a threshold of three buys nothing today, since the cluster
holds a full quorum. It costs nothing either, and it keeps the option of
later withdrawing the keys and splitting custody among people without
re-initialising Vault. Initialising 1-of-1 would close that door.

**The root token is being kept, deliberately, not revoked.** Phase 17 added
the Vault Secrets Operator, but that does not make the root token disposable:
`configure-vault.sh` logs in with it and is a permanent, re-runnable step of
every rebuild (see `platform/vault-secrets-operator/README.md`), and the only
other auth path Vault has, role `vso-canary`, grants read on exactly one KV
path — nowhere near enough to run the ceremony or diagnose a failure. This is
the same reasoning `docs/superpowers/specs/2026-09-05-vault-secrets-operator-design.md`
§11 gives for deferring revocation: doing it in the phase that first depends
on Vault working would remove the credential needed to diagnose a failure. If
it is ever revoked anyway, the way back is `vault operator generate-root`
with the unseal keys — a command not otherwise documented in this
repository.

## How the helper actually unseals Vault

The helper (`config/unsealer.yaml`) polls `vault status` on
`vault-0.vault-internal:8200` and, while sealed, runs:

    vault write -format=json sys/unseal key=@"$k"

`key=@<path>` makes Vault read the key from the file named by `$k`, so only
the filename reaches the argument list — the key itself never appears in
`ps` or in a log line.

This is not what was originally designed. The original design called for
`vault operator unseal - < "$k"`, piping the key on stdin. **That command
does not work**: the Vault CLI has no `-` stdin convention, so a literal `-`
is sent as the key and rejected with `'key' must be a valid hex or base64
string`. Piping into the no-argument form fails a different way — Vault
refuses it outright with `file descriptor 0 is not a terminal`.
`vault operator unseal -help` documents exactly two routes, a TTY prompt or
the key as a command argument, and neither is available to a Deployment.
`vault write sys/unseal key=@<path>` is the third route: the same
unauthenticated API, with the key loaded from a file instead.

The script is inline in the pod's `args`, not in a ConfigMap. It lived in a
ConfigMap first; editing a ConfigMap does not roll the pods that mount it,
so a corrected script sat in the cluster while the running pod kept
executing the old one from memory, indefinitely. Changing the inline script
now changes the pod template, which rolls the Deployment the way every
other change does.

**Interactive manual unseal always works**, using the TTY prompt a human has
and a Deployment does not:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault operator unseal'

Run it three times, once per key.

## Backups and recovery

Snapshotting needs an authenticated session. `vault login` prompts for the
root token with input hidden — do not pass it as `VAULT_TOKEN=` on the
command line, since that puts it in `ps` and shell history:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh -c "vault login && vault operator raft snapshot save /tmp/vault-snapshot.snap"'
    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- cat /tmp/vault-snapshot.snap' > /backups/vault/vault-$(date +%Y%m%d).snap
    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /tmp/vault-snapshot.snap'

The middle line uses `cat` redirected to a file rather than `kubectl cp` —
fewer moving parts, and it does not depend on `tar` existing inside the
container. `/backups` is its own disk, mode 0777; no `sudo` is needed for
any of this.

A snapshot is a gzip tar containing `meta.json`, `state.bin`, `SHA256SUMS`,
and `SHA256SUMS.sealed`. The `.sealed` file is concrete evidence of the
invariant below: it is a checksum manifest that only makes sense if the
snapshot's payload is itself sealed with Vault's master key.

Scheduled automation is roadmap §33 (`docs/workstation-plan.md`). This is
the verified manual procedure it will schedule.

**A snapshot alone cannot restore anything.** It is encrypted with Vault's
master key, so restoring into a fresh Vault needs the same unseal keys. The
snapshot and the keys are deliberately kept in different places — snapshots
on disk at `/backups/vault`, keys in a password manager. If they ever end up
in the same place, that place becomes a single point of both total
compromise and total loss.

### Restoring

**Status: this path is documented but not exercised.** The snapshot
procedure above is verified — a real snapshot has been taken from this
cluster and its integrity checked. The restore procedure below has not:
exercising it would mean destroying the live Vault, so it has not been run
against this cluster. It is transcribed from `vault operator raft snapshot
restore -help`, run read-only against `vault-0` to confirm the flags below
are real rather than guessed, and from HashiCorp's own documentation. Treat
it as a starting point, not a rehearsed procedure.

Copy the snapshot file onto the target Vault pod, then, authenticated:

    vault operator raft snapshot restore /path/to/vault-snapshot.snap

Add `-force` when restoring into a Vault whose Shamir keys are not already
known to match the snapshot — `-force` "bypasses checks ensuring the
Autounseal or shamir keys are consistent with the snapshot data" (verbatim
from `-help`). Restoring into a *fresh* Vault, which is the recovery
scenario this procedure exists for, is exactly that case.

**The non-obvious part, and the reason this section exists:** after a
successful restore, the live Vault's unseal keys become **the keys that
were current when the snapshot was taken** — not whatever keys the fresh
Vault was initialised with before the restore, if it was initialised at
all. `vault-unseal-keys` in the cluster must be recreated from *those*
keys (from the password manager, matched to the snapshot's date) before the
unsealer can do anything. Restore with the wrong keys still in the Secret
and the symptom is silent: the helper logs "still sealed (uninitialized, or
keys absent/wrong)" forever, indistinguishable from the Secret simply being
wrong — see `docs/troubleshooting.md` entry 9, fault 1. Confirm which keys
are current by testing one manually
(`sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault operator unseal'`)
before trusting the helper to do it.

## Resource usage (measured)

| Pod | CPU | Memory |
| --- | --- | --- |
| `vault-0` | 24m | 62Mi |
| `vault-unsealer` | 9m | 8Mi |
| `ts-vault-s8ckn-0` (tailnet proxy) | 3m | 28Mi |
| **Total** | | **98Mi** |

A verified snapshot of this Vault (empty of application data — nothing
consumes it until phase 17) is 20,648 bytes (~20 KB).

Host-level, measured before and after this phase: 8.3Gi available memory
and 15 pods cluster-wide before; 8.2Gi available and 16 pods after.

## When Vault will not unseal

    sg k3s-admin -c 'kubectl -n vault logs deploy/vault-unsealer --tail=20'

- "still sealed (uninitialized, or keys absent/wrong)" repeating means
  either the init ceremony has not run, the Secret is missing or has the
  wrong key names, or the unseal command itself is failing — the helper's
  own logging hides the API's actual error behind `>/dev/null 2>&1`. See
  `docs/troubleshooting.md` entry 9 for how to get the real error.
- The helper mounts the Secret as optional, so it runs happily with no
  Secret at all. A running helper is not evidence that unsealing works.
- Manual unseal always remains available with the keys from your password
  manager:

      sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault operator unseal'
