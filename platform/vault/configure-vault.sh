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

echo "==> done"
