#!/bin/sh
# Vault configuration for the homelab. Run INSIDE the vault-0 pod.
#
# ARGO CD DOES NOT RECONCILE THIS FILE. Vault's mounts, auth methods,
# policies and roles are not Kubernetes objects; nothing reconciles them.
# This script is the versioned *intent*, applied by hand. It is the same
# arrangement as infrastructure/networking/policy.hujson.
#
# It is idempotent: every step either checks before acting or overwrites with
# the same value, so a rebuild can re-run it safely.
#
# How to run it -- see platform/vault-secrets-operator/README.md:
#   1. sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
#   2. sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
#   3. sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
#
# The token is entered at a hidden prompt in step 1 and removed in step 3. It
# is never an argument, never an environment variable, and never in history.
set -eu

echo "==> checking Vault is reachable and unsealed"
vault status >/dev/null

echo "==> KV v2 at homelab/"
if vault secrets list | grep -q '^homelab/'; then
  echo "    already mounted"
else
  vault secrets enable -path=homelab -version=2 kv
fi

echo "==> seeding homelab/canary"
if vault kv get homelab/canary >/dev/null 2>&1; then
  echo "    already present, leaving its value alone"
else
  vault kv put homelab/canary value=canary-ok
fi

echo "==> kubernetes auth method"
if vault auth list | grep -q '^kubernetes/'; then
  echo "    already enabled"
else
  vault auth enable kubernetes
fi

echo "==> kubernetes auth config"
# kubernetes_host is the only setting needed when Vault runs in-cluster: it
# uses its own ServiceAccount token as the reviewer JWT and the pod's CA
# bundle. Vault's ServiceAccount already holds system:auth-delegator, granted
# by the Vault chart, so it may call TokenReview.
vault write auth/kubernetes/config \
    kubernetes_host="https://kubernetes.default.svc.cluster.local"

echo "==> policy vso-canary-read"
# The data/ segment is REQUIRED and is not a typo. KV v2 stores values one
# level below the mount, so a policy on homelab/canary matches nothing and
# produces a permission denial that reads exactly like a wrong path.
vault policy write vso-canary-read - <<'POLICY'
path "homelab/data/canary" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-canary"
# bound_service_account_names must match the ServiceAccount created in
# platform/vault-secrets-operator/config/vault-secrets.yaml, and audience must
# match that file's VaultAuth spec.kubernetes.audiences. A mismatch in either
# is a permission denial naming neither side.
vault write auth/kubernetes/role/vso-canary \
    bound_service_account_names=vault-canary \
    bound_service_account_namespaces=vault \
    audience=vault \
    token_policies=vso-canary-read \
    ttl=1h

echo "==> seeding homelab/postgres"
if vault kv get homelab/postgres >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed. It exists only in Vault and in the
  # Secret VSO derives from it -- no human sees or types it.
  #
  # Alphanumeric only: a / or @ inside a password breaks connection URLs in
  # ways that surface far from the cause.
  #
  # Written to a file so that only the FILENAME becomes an argument. A
  # password on a command line is visible in `ps`, which is the same reason
  # the unsealer reads its keys with key=@<path>.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/postgres username=postgres password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-postgres-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-postgres-read - <<'POLICY'
path "homelab/data/postgres" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-postgres"
# bound_service_account_names must match the ServiceAccount created in
# platform/databases/postgres/config/vault-secrets.yaml, and audience must
# match that file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-postgres \
    bound_service_account_names=postgres \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-postgres-read \
    ttl=1h

echo "==> seeding homelab/redis"
if vault kv get homelab/redis >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed, exactly as the postgres block above.
  #
  # Alphanumeric only. Redis stores requirepass verbatim and would accept
  # anything, but clients build redis://:password@host URLs, where a / or @
  # breaks parsing in ways that surface far from the cause.
  #
  # Written to a file so that only the FILENAME becomes an argument -- a
  # password on a command line is visible in `ps`.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/redis username=default password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-redis-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-redis-read - <<'POLICY'
path "homelab/data/redis" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-redis"
# bound_service_account_names must match the ServiceAccount created in
# platform/databases/redis/config/vault-secrets.yaml, and audience must match
# that file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-redis \
    bound_service_account_names=redis \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-redis-read \
    ttl=1h

echo "==> done"
