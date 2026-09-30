# The Last Exam dev environment — Vault ceremony

Canonical, ordered checklist for getting every `tle-dev` secret into
Vault. This page is the single source of truth for the ceremony; the
recovery runbook (`tle-dev-recovery.md`) links here instead of
duplicating it.

Background: Argo CD reconciles manifests from git, but Vault's mounts,
policies, roles, and secret *values* are not Kubernetes objects, so
nothing reconciles them. This ceremony applies the versioned intent by
hand, in two steps that must run **in order**. Both steps are idempotent
and safe to re-run: `configure-vault.sh` never overwrites an existing
field, and `seed-tle-dev-vault.sh` patches only the keys you filled in
(empty values are skipped, never clobbering Vault).

## Prerequisites

- Access to the cluster host (the commands below assume a shell on it).
- The TLE release's product-issued values at hand for the providers you
  enable: Ed25519 JWT private key, MinIO pair, OAuth client secrets, push
  credentials. Per-key sources are documented in the
  `apps/tle-dev/vault-seed/.env.tle-dev-*.example` templates.
- Know which step you are on: **step 1 first, always**. Running step 2
  first is harmless (step 1 never overwrites present fields) but the
  `disabled` placeholders remain until both have run.

## Step 1 — Configure Vault (policies, roles, generated values)

Run **inside** the `vault-0` pod (see also
`platform/vault-secrets-operator/README.md` and the header of
`platform/vault/configure-vault.sh`):

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

The token is typed at a hidden prompt in the first command and removed
in the last. It is never an argument, never an environment variable,
and never in history.

What this creates for `tle-dev` (extension at the end of the script):

- 4 policies + 4 Kubernetes auth roles: `vso-tle-dev-auth/data/misc`
  (bound to ServiceAccounts `tle-dev-auth/data/misc` in namespace
  `tle-dev`) and `vso-tle-dev-db` (bound to `tle-dev-db` in namespace
  `databases`, used by the postgres Job).
- Generated values (random, server-side, alphanumeric so they compose
  into `postgres://` URLs): `password`, `metadata_password`,
  `quest_password` in `homelab/tle-dev/data`; `CSRF_SECRET` (48 chars),
  `METADATA_GRPC_INTERNAL_TOKEN`, `NOTIFICATION_WEBHOOK_SIGNING_SECRET`
  in `homelab/tle-dev/misc`.
- `disabled` placeholders for every product-issued key: `MINIO_*`,
  `NOTIFICATION_APNS_*`, `NOTIFICATION_FCM_*`,
  `AUTH_JWT_ED25519_*_PRIVATE_KEY_PEM`, `APPLE_*`, `DISCORD_*`,
  `GOOGLE_*`. Placeholders let every `VaultStaticSecret` sync; real
  values arrive in step 2.

## Step 2 — Seed product-issued values (patch placeholders)

On your workstation (or anywhere with `kubectl` + the kubeconfig), from
the repository root:

```bash
cp apps/tle-dev/vault-seed/.env.tle-dev-auth.example apps/tle-dev/vault-seed/.env.tle-dev-auth
cp apps/tle-dev/vault-seed/.env.tle-dev-data.example apps/tle-dev/vault-seed/.env.tle-dev-data    # only if overriding a generated password
cp apps/tle-dev/vault-seed/.env.tle-dev-misc.example apps/tle-dev/vault-seed/.env.tle-dev-misc
# fill in the values (KEY=literal, KEY=@relative/file for PEM/JSON payloads;
# empty or absent keys are skipped)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
./apps/tle-dev/vault-seed/seed-tle-dev-vault.sh
```

The `.env.tle-dev-*` files are gitignored and must never be committed
(the seed script refuses to run if a real values file was ever
tracked). Values travel to Vault over the `kubectl exec` stdin pipe
into a temp file read as `key=@file`: they never appear in `argv`
(`ps`) on either side.

**Gate — REQUIRED before workloads can run; sync alone is not enough:**

- `AUTH_JWT_ED25519_PRIVATE_KEY_PEM` (JWT signing key; pin its public
  half as `auth-jwt-ed25519-public-key-pem` in
  `apps/tle-dev/config/edge-config.yaml`)
- `MINIO_ACCESS_KEY` + `MINIO_SECRET_KEY` (object storage)

Everything else stays `disabled` until you enable its provider (OAuth,
APNs, FCM) — that is expected, not an error.

## Verify (key names only, never values)

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl -n vault exec vault-0 -- sh -c '
  for p in tle-dev/auth tle-dev/data tle-dev/misc; do
    echo "-- homelab/$p"
    vault kv get -format=json "homelab/$p" 2>/dev/null | grep -o "\"[a-zA-Z_]*\":" || echo "  (unreadable)"
  done
'
kubectl -n tle-dev get vaultstaticsecret tle-dev-auth tle-dev-data tle-dev-misc
kubectl -n tle-dev get secret tle-dev-auth-secrets tle-dev-data-secrets tle-dev-misc-secrets
```

Expected: all three paths list their keys; all three `VaultStaticSecret`
objects report synced; all three K8s Secrets exist. If a path is
missing, step 1 has not run. If REQUIRED keys are still `disabled`,
step 2 has not run (or their `.env` entries were left empty).

## Rotation / rebuild notes

- Re-running step 1 after a rebuild is safe (never overwrites).
- Re-running step 2 patches only filled keys; to rotate a generated
  password, fill it in `.env.tle-dev-data` and re-run — the postgres
  Job re-applies role passwords on its next Argo sync, and VSO
  `rolloutRestartTargets` restart the consumers.
- Auth signing-key rotation follows the #4 flow (staged key), not this page.
