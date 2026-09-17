# GHCR pull credential

The cluster pulls private images from `ghcr.io/slqzeer/*`. This directory
holds the path that credential takes: Vault -> Vault Secrets Operator ->
a `kubernetes.io/dockerconfigjson` Secret named `ghcr-pull` in namespace
`apps`.

## This credential is different from every other one here

Every other secret in this cluster is generated inside Vault by
`platform/vault/configure-vault.sh` and never seen by a human. A GHCR
personal access token cannot work that way: GitHub issues it, and it must be
pasted in.

It is therefore **not** in `configure-vault.sh`. Putting it there would either
place a credential in git, or require an interactive prompt in a script that
is fed to the pod on stdin — where a `read` would consume the script's own
remaining lines instead of waiting for input. The script carries only the
policy and the role; the value is seeded by the ceremony below.

## Issuing the token

GHCR pulls need a **classic** personal access token. Fine-grained tokens do
not carry package scopes.

1. <https://github.com/settings/tokens> -> Generate new token (classic)
2. Scope: **`read:packages` only**. Nothing else. This token can only pull.
3. Set an expiry and **write the date down** — see "When it expires" below.

## Seeding it into Vault

Run at a real terminal, not through a piped script:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh'

Then, inside the pod:

    vault login
    umask 077
    TMPF=$(mktemp)
    trap 'stty echo' INT TERM EXIT; stty -echo; printf 'Paste the PAT, then press Enter: '; read -r PAT; stty echo; printf '\n'
    printf '%s' "$PAT" > "$TMPF"
    unset PAT
    wc -c < "$TMPF"
    vault kv put homelab/ghcr username=Slqzeer password=@"$TMPF"
    rm -f "$TMPF"
    rm -f /home/vault/.vault-token
    exit

The `trap` restores echo even if the paste is interrupted — a Ctrl-C between
`stty -echo` and `stty echo` would otherwise leave the terminal silently not
echoing. `stty -echo` keeps the token off the screen. `printf '%s'` writes it with **no
trailing newline** — a newline inside the token produces a 401 whose message
says nothing about whitespace. `password=@"$TMPF"` passes only the *filename*
as an argument, so the token never appears in `ps`; this is the same form the
unsealer and `configure-vault.sh` use.

`wc -c` prints the byte count. Check it against the token's real length before
continuing — it is the only confirmation available that the paste was complete,
and it reveals nothing.

## When it expires

**Running pods are not affected.** Only *new* pulls fail, so an expired token
surfaces at the next rollout, node restart or eviction — arbitrarily far from
the cause, as `ImagePullBackOff` with a 401.

To rotate: issue a new classic token with `read:packages`, repeat the seeding
ceremony above (`vault kv put` overwrites), and VSO propagates it within its
`refreshAfter` window. Nothing needs restarting, and no manifest changes.

| Field | Value |
| --- | --- |
| Issued | _fill in_ |
| Expires | _fill in_ |
| Scope | `read:packages` |
| Vault path | `homelab/ghcr` |

## Related

- `platform/registry/config/vault-secrets.yaml` — the VSO wiring
- `platform/vault-secrets-operator/README.md` — how VSO authenticates
- The `beacon` repository — the first consumer
