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

echo "==> policy vso-ghcr-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-ghcr-read - <<'POLICY'
path "homelab/data/ghcr" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-ghcr"
# bound_service_account_names must match the ServiceAccount created in
# platform/registry/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-ghcr \
    bound_service_account_names=registry \
    bound_service_account_namespaces=apps \
    audience=vault \
    token_policies=vso-ghcr-read \
    ttl=1h

echo "==> seeding homelab/grafana"
if vault kv get homelab/grafana >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed, exactly as the postgres and redis
  # blocks above.
  #
  # Alphanumeric only -- this password is set as Grafana's admin password and
  # may be pasted into a browser login; a / or @ survives neither round trip
  # predictably.
  #
  # Written to a file so that only the FILENAME becomes an argument -- a
  # password on a command line is visible in `ps`.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/grafana username=admin password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-grafana-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
#
# Two paths since phase 25: the admin credential, and the OIDC client
# secret Grafana presents to Keycloak. Both are read by the same `grafana`
# ServiceAccount through the same VaultAuth, so extending this policy is
# the whole change -- no new role, no new ServiceAccount.
vault policy write vso-grafana-read - <<'POLICY'
path "homelab/data/grafana" {
  capabilities = ["read"]
}

path "homelab/data/keycloak-grafana" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-grafana"
# bound_service_account_names must match the ServiceAccount created in
# observability/monitoring/config/vault-secrets.yaml, and audience must match
# that file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-grafana \
    bound_service_account_names=grafana \
    bound_service_account_namespaces=monitoring \
    audience=vault \
    token_policies=vso-grafana-read \
    ttl=1h

echo "==> seeding homelab/postgres-exporter"
if vault kv get homelab/postgres-exporter >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed, exactly as the postgres and redis
  # blocks above.
  #
  # Alphanumeric only: a / or @ inside a password breaks connection URLs in
  # ways that surface far from the cause.
  #
  # Written to a file so that only the FILENAME becomes an argument -- a
  # password on a command line is visible in `ps`.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/postgres-exporter username=exporter password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-postgres-exporter-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-postgres-exporter-read - <<'POLICY'
path "homelab/data/postgres-exporter" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-postgres-exporter"
# bound_service_account_names must match the ServiceAccount created in
# observability/monitoring/targets/postgres-exporter.yaml, and audience must
# match that file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-postgres-exporter \
    bound_service_account_names=postgres-exporter \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-postgres-exporter-read \
    ttl=1h

echo "==> seeding homelab/keycloak"
if vault kv get homelab/keycloak >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # The BREAK-GLASS admin of the `master` realm, and nothing else. Humans
  # live in the `homelab` realm; this account exists to recover them.
  #
  # Generated here and never displayed, exactly as the postgres, redis and
  # grafana blocks above.
  #
  # Alphanumeric only -- this password is pasted into a browser login.
  #
  # IMPORTANT ASYMMETRY: KC_BOOTSTRAP_ADMIN_PASSWORD is honoured only when
  # no admin exists. Unlike homelab/postgres, whose Sync-hook Job runs
  # ALTER ROLE on every sync, rewriting this value does NOT change the live
  # password. Rotate in Keycloak first, then update Vault to match.
  # See platform/keycloak/README.md.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/keycloak username=admin password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> seeding homelab/keycloak-db"
if vault kv get homelab/keycloak-db >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # The PostgreSQL login Keycloak connects with. Read in TWO namespaces:
  # `keycloak` (the Deployment) and `databases` (the Sync-hook Job whose
  # ALTER ROLE pushes this value into the database on every sync).
  #
  # Alphanumeric only: this value lands in a JDBC URL, where a / or @
  # breaks parsing in ways that surface far from the cause.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/keycloak-db username=keycloak password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> seeding homelab/keycloak-grafana"
if vault kv get homelab/keycloak-grafana >/dev/null 2>&1; then
  echo "    already present, leaving the value alone"
else
  # A PLACEHOLDER, and deliberately so. Keycloak generates the real client
  # secret when it imports the realm; a human then overwrites this value.
  # See platform/keycloak/README.md.
  #
  # This block exists to break a REBUILD DEADLOCK, not to supply a working
  # credential. Grafana (wave 23) reads this Secret through envValueFrom.
  # A Secret that does not exist leaves the pod in
  # CreateContainerConfigError, so `monitoring` never goes Healthy, so
  # NOTHING AT WAVE 24 SYNCS -- including the Keycloak that is the only
  # thing able to produce the real value. Grafana would wait on Keycloak
  # and Keycloak on Grafana, with nothing anywhere naming identity as the
  # cause.
  #
  # Seeded, the failure degrades to `invalid_client` on the SSO button
  # while Grafana's local admin form keeps working.
  PWFILE=$(mktemp)
  trap 'rm -f "$PWFILE"' EXIT
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/keycloak-grafana clientSecret=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated placeholder -- Keycloak's real secret replaces it"
fi

echo "==> seeding homelab/keycloak-portal and homelab/portal"
# The same OIDC secret is presented by Keycloak and by the portal. Files
# created by mktemp are private, and Vault receives values via key=@file,
# never via argv or terminal output. An existing portal path is patched so
# an optional session-previous-key survives key rotation.
umask 077
KC_PORTAL_SECRET_FILE=$(mktemp)
PORTAL_SESSION_FILE=$(mktemp)
PORTAL_EXISTING_FILE=$(mktemp)
PORTAL_PATH_FILE=$(mktemp)
PORTAL_READ_ERROR_FILE=$(mktemp)
trap 'rm -f "$KC_PORTAL_SECRET_FILE" "$PORTAL_SESSION_FILE" "$PORTAL_EXISTING_FILE" "$PORTAL_PATH_FILE" "$PORTAL_READ_ERROR_FILE"' EXIT

# A failed Vault CLI read is not proof that a path or field is absent. Only
# Vault's explicit not-found diagnostics permit seeding; all other errors
# abort before any write. Capture diagnostics privately and never print data.
vault_optional_get() {
  read_path=$1
  read_field=$2
  read_output=$3
  if [ "$read_field" = "-" ]; then
    if vault kv get "$read_path" >"$read_output" 2>"$PORTAL_READ_ERROR_FILE"; then
      VAULT_READ_STATUS=present
      return 0
    fi
  elif vault kv get "-field=$read_field" "$read_path" >"$read_output" 2>"$PORTAL_READ_ERROR_FILE"; then
    VAULT_READ_STATUS=present
    return 0
  fi
  data_path="homelab/data/${read_path#homelab/}"
  if grep -Fqx "No value found at $read_path" "$read_output" ||
     grep -Fqx "No value found at $data_path" "$read_output" ||
     grep -Fqx "No value found at $read_path" "$PORTAL_READ_ERROR_FILE" ||
     grep -Fqx "No value found at $data_path" "$PORTAL_READ_ERROR_FILE"; then
    VAULT_READ_STATUS=absent
    return 0
  fi
  if [ "$read_field" != "-" ] &&
     { grep -Fqx "Field \"$read_field\" not present in secret" "$PORTAL_READ_ERROR_FILE" ||
       grep -Fqx "Field \"$read_field\" not present in secret" "$read_output"; }; then
    VAULT_READ_STATUS=absent
    return 0
  fi
  echo "Vault read failed for $read_path" >&2
  return 1
}

# Read all existing state before any mutation so a transient error cannot
# replace a client secret or discard an optional previous rotation key.
vault_optional_get homelab/keycloak-portal clientSecret "$KC_PORTAL_SECRET_FILE" || exit 1
kc_secret_status=$VAULT_READ_STATUS
vault_optional_get homelab/portal - "$PORTAL_PATH_FILE" || exit 1
portal_path_status=$VAULT_READ_STATUS
portal_secret_status=absent
portal_session_status=absent
if [ "$portal_path_status" = present ]; then
  vault_optional_get homelab/portal oidc-client-secret "$PORTAL_EXISTING_FILE" || exit 1
  portal_secret_status=$VAULT_READ_STATUS
  vault_optional_get homelab/portal session-current-key "$PORTAL_SESSION_FILE" || exit 1
  portal_session_status=$VAULT_READ_STATUS
fi

if [ "$kc_secret_status" = present ] && [ -s "$KC_PORTAL_SECRET_FILE" ]; then
  echo "    Keycloak portal client secret already present"
elif [ "$portal_secret_status" = present ] && [ -s "$PORTAL_EXISTING_FILE" ]; then
  cp "$PORTAL_EXISTING_FILE" "$KC_PORTAL_SECRET_FILE"
  vault kv put homelab/keycloak-portal clientSecret=@"$KC_PORTAL_SECRET_FILE" >/dev/null
  echo "    recovered Keycloak portal client secret from portal path"
else
  head -c 512 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 48 >"$KC_PORTAL_SECRET_FILE"
  vault kv put homelab/keycloak-portal clientSecret=@"$KC_PORTAL_SECRET_FILE" >/dev/null
  echo "    generated Keycloak portal client secret"
fi

if [ "$portal_path_status" = present ]; then
  if [ "$portal_secret_status" = absent ] ||
     ! cmp -s "$PORTAL_EXISTING_FILE" "$KC_PORTAL_SECRET_FILE"; then
    vault kv patch homelab/portal oidc-client-secret=@"$KC_PORTAL_SECRET_FILE" >/dev/null
    echo "    synchronized portal OIDC client secret"
  fi
  if [ "$portal_session_status" = absent ] || [ ! -s "$PORTAL_SESSION_FILE" ]; then
    head -c 512 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 64 >"$PORTAL_SESSION_FILE"
    vault kv patch homelab/portal session-current-key=@"$PORTAL_SESSION_FILE" >/dev/null
    echo "    generated portal session key"
  fi
else
  head -c 512 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 64 >"$PORTAL_SESSION_FILE"
  vault kv put homelab/portal \
    oidc-client-secret=@"$KC_PORTAL_SECRET_FILE" \
    session-current-key=@"$PORTAL_SESSION_FILE" >/dev/null
  echo "    generated portal OIDC and session keys"
fi
rm -f "$KC_PORTAL_SECRET_FILE" "$PORTAL_SESSION_FILE" "$PORTAL_EXISTING_FILE" "$PORTAL_PATH_FILE" "$PORTAL_READ_ERROR_FILE"

echo "==> policy vso-keycloak-read"
# Grants all three paths the Keycloak pod needs, so its one ServiceAccount needs
# one role. The data/ segment is REQUIRED and is not a typo -- see the note
# on vso-canary-read above.
vault policy write vso-keycloak-read - <<'POLICY'
path "homelab/data/keycloak" {
  capabilities = ["read"]
}

path "homelab/data/keycloak-db" {
  capabilities = ["read"]
}

path "homelab/data/keycloak-portal" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-keycloak"
# bound_service_account_names must match the ServiceAccount created in
# platform/keycloak/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-keycloak \
    bound_service_account_names=keycloak \
    bound_service_account_namespaces=keycloak \
    audience=vault \
    token_policies=vso-keycloak-read \
    ttl=1h

echo "==> policy vso-portal-read"
vault policy write vso-portal-read - <<'POLICY'
path "homelab/data/portal" {
  capabilities = ["read"]
}
# The portal image is private on GHCR. The kubelet pulls it with the
# ghcr-pull Secret projected by apps/portal/config/vault-secrets.yaml,
# so this role must also read the shared GHCR credential.
path "homelab/data/ghcr" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-portal"
# The portal alone receives its OIDC secret and session key. Do not add
# keycloak or another namespace to this role's bindings.
vault write auth/kubernetes/role/vso-portal \
    bound_service_account_names=homelab-portal \
    bound_service_account_namespaces=portal \
    audience=vault \
    token_policies=vso-portal-read \
    ttl=1h

echo "==> policy vso-keycloak-db-read"
# Narrower than vso-keycloak-read on purpose: the Job in `databases` needs
# the database login and must not see the Keycloak admin credential.
vault policy write vso-keycloak-db-read - <<'POLICY'
path "homelab/data/keycloak-db" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-keycloak-db"
# A SECOND role rather than adding `databases` to vso-keycloak's namespaces.
# Vault's kubernetes auth matches the CROSS-PRODUCT of
# bound_service_account_names and bound_service_account_namespaces, so one
# role naming both SAs and both namespaces would additionally authorize
# `keycloak` in `databases` and `keycloak-db` in `keycloak` -- two
# identities nobody intended, and a widening no manifest comment reveals.
#
# bound_service_account_names must match the ServiceAccount created in
# platform/keycloak/config/database-job.yaml.
vault write auth/kubernetes/role/vso-keycloak-db \
    bound_service_account_names=keycloak-db \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-keycloak-db-read \
    ttl=1h

echo "==> policy vso-tle-dev-auth-read"
# Vault holds only tle-dev passwords and keys, never URLs: the full
# DATABASE_URLs are composed in apps/tle-dev/config/vault-secrets.yaml
# from public parts plus these passwords. Seed values come from
# apps/tle-dev/vault-seed/ (templates) via seed-tle-dev-vault.sh.
vault policy write vso-tle-dev-auth-read - <<'POLICY'
path "homelab/data/tle-dev/auth" {
  capabilities = ["read"]
}
POLICY

echo "==> policy vso-tle-dev-data-read"
vault policy write vso-tle-dev-data-read - <<'POLICY'
path "homelab/data/tle-dev/data" {
  capabilities = ["read"]
}
POLICY

echo "==> policy vso-tle-dev-misc-read"
vault policy write vso-tle-dev-misc-read - <<'POLICY'
path "homelab/data/tle-dev/misc" {
  capabilities = ["read"]
}
POLICY

echo "==> policy vso-tle-dev-db-read"
# A separate policy object from vso-tle-dev-data-read on purpose, even
# though both read homelab/data/tle-dev/data: the Job in `databases`
# must not gain anything else the data policy may grow, and vice versa.
vault policy write vso-tle-dev-db-read - <<'POLICY'
path "homelab/data/tle-dev/data" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-tle-dev-auth"
# bound_service_account_names must match the ServiceAccount created in
# apps/tle-dev/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-tle-dev-auth \
    bound_service_account_names=tle-dev-auth \
    bound_service_account_namespaces=tle-dev \
    audience=vault \
    token_policies=vso-tle-dev-auth-read \
    ttl=1h

echo "==> role vso-tle-dev-data"
vault write auth/kubernetes/role/vso-tle-dev-data \
    bound_service_account_names=tle-dev-data \
    bound_service_account_namespaces=tle-dev \
    audience=vault \
    token_policies=vso-tle-dev-data-read \
    ttl=1h

echo "==> role vso-tle-dev-misc"
vault write auth/kubernetes/role/vso-tle-dev-misc \
    bound_service_account_names=tle-dev-misc \
    bound_service_account_namespaces=tle-dev \
    audience=vault \
    token_policies=vso-tle-dev-misc-read \
    ttl=1h

echo "==> role vso-tle-dev-db"
# A SECOND role rather than adding `databases` to vso-tle-dev-data's
# namespaces. Vault's kubernetes auth matches the CROSS-PRODUCT of
# bound_service_account_names and bound_service_account_namespaces, so one
# role naming both SAs and both namespaces would additionally authorize
# `tle-dev-data` in `databases` and `tle-dev-db` in `tle-dev` -- two
# identities nobody intended. Same reasoning as vso-keycloak-db above.
#
# bound_service_account_names must match the ServiceAccount created in
# apps/tle-dev/config/postgres-job.yaml.
vault write auth/kubernetes/role/vso-tle-dev-db \
    bound_service_account_names=tle-dev-db \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-tle-dev-db-read \
    ttl=1h

echo "==> seeding homelab/tle-dev/{auth,data,misc}"
# Every field is seeded exactly once and never overwritten: generated
# passwords for the databases, `disabled` placeholders for product-issued
# keys the human has not provided yet. Placeholders let every
# VaultStaticSecret sync; the human replaces them with
# apps/tle-dev/vault-seed/seed-tle-dev-vault.sh (which patches, so it
# cannot clobber generated values). An existing path is patched so a
# previous seed survives; a missing path is created. Values travel via
# key=@file, never via argv or terminal output.
umask 077
TLE_READ=$(mktemp)
TLE_VAL=$(mktemp)
trap 'rm -f "$KC_PORTAL_SECRET_FILE" "$PORTAL_SESSION_FILE" "$PORTAL_EXISTING_FILE" "$PORTAL_PATH_FILE" "$PORTAL_READ_ERROR_FILE" "$TLE_READ" "$TLE_VAL"' EXIT
tle_ensure_field() {
  # $1 = vault path, $2 = field, $3 = "generated"|"placeholder".
  # $TLE_VAL already holds the value to seed when absent.
  vault_optional_get "$1" "$2" "$TLE_READ" || exit 1
  if [ "$VAULT_READ_STATUS" = present ] && [ -s "$TLE_READ" ]; then
    echo "    $1/$2 already present"
    return 0
  fi
  vault_optional_get "$1" "-" "$TLE_READ" || exit 1
  if [ "$VAULT_READ_STATUS" = present ]; then
    vault kv patch "$1" "$2=@$TLE_VAL" >/dev/null
  else
    vault kv put "$1" "$2=@$TLE_VAL" >/dev/null
  fi
  echo "    seeded $1/$2 ($3)"
}
tle_generate() {
  # $1 = length. Alphanumeric only: these passwords are composed into
  # postgres:// URLs, where a / or @ breaks parsing far from the cause.
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c "$1" >"$TLE_VAL"
}
tle_placeholder() {
  printf 'disabled' >"$TLE_VAL"
}
for f in password metadata_password quest_password; do
  tle_generate 32
  tle_ensure_field homelab/tle-dev/data "$f" "generated"
done
for spec in "CSRF_SECRET 48" "METADATA_GRPC_INTERNAL_TOKEN 32" \
  "NOTIFICATION_WEBHOOK_SIGNING_SECRET 32"; do
  set -- $spec
  tle_generate "$2"
  tle_ensure_field homelab/tle-dev/misc "$1" "generated"
done
for f in MINIO_ACCESS_KEY MINIO_SECRET_KEY NOTIFICATION_APNS_PRIVATE_KEY \
  NOTIFICATION_FCM_CREDENTIALS_JSON AUTH_JWT_ED25519_PRIVATE_KEY_PEM \
  AUTH_JWT_ED25519_STAGED_PRIVATE_KEY_PEM APPLE_PRIVATE_KEY \
  APPLE_CLIENT_SECRET_OVERRIDE DISCORD_CLIENT_SECRET GOOGLE_CLIENT_SECRET; do
  case "$f" in
    MINIO_*|AUTH_JWT_ED25519_PRIVATE_KEY_PEM) kind="placeholder REQUIRED" ;;
    *) kind="placeholder optional" ;;
  esac
  tle_placeholder
  case "$f" in
    MINIO_*|NOTIFICATION_APNS*|NOTIFICATION_FCM*) tle_ensure_field homelab/tle-dev/misc "$f" "$kind" ;;
    *) tle_ensure_field homelab/tle-dev/auth "$f" "$kind" ;;
  esac
done
rm -f "$TLE_READ" "$TLE_VAL"
echo "    NOTE: placeholders marked REQUIRED (JWT private key, MinIO pair)"
echo "    must be replaced via apps/tle-dev/vault-seed/seed-tle-dev-vault.sh"
echo "    before the workloads can actually run; sync alone is not enough"

echo "==> policy vso-omniroute-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-omniroute-read - <<'POLICY'
path "homelab/data/omniroute" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-omniroute"
# bound_service_account_names must match the ServiceAccount created in
# apps/omniroute/config/vault-secrets.yaml, and audience must match that
# file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-omniroute \
    bound_service_account_names=omniroute \
    bound_service_account_namespaces=omniroute \
    audience=vault \
    token_policies=vso-omniroute-read \
    ttl=1h

echo "==> seeding homelab/omniroute"
# Each field is generated once and never overwritten, never displayed.
#   admin-password          dashboard login; applied to OmniRoute's DB on
#                           EVERY start by the init container, so rotating it
#                           here (+ VSO rollout restart) is the whole rotation
#   jwt-secret              signs dashboard session cookies
#   api-key-secret          encrypts client API keys at rest
#   storage-encryption-key  encrypts provider credentials in SQLite. Rotating
#                           it makes existing stored credentials unreadable;
#                           apps/omniroute/vault-seed/migrate-local-omniroute.sh
#                           overwrites it with the key of an existing install.
# Alphanumeric/hex only so values survive env files and browser paste.
umask 077
OMNI_READ=$(mktemp)
OMNI_VAL=$(mktemp)
trap 'rm -f "$KC_PORTAL_SECRET_FILE" "$PORTAL_SESSION_FILE" "$PORTAL_EXISTING_FILE" "$PORTAL_PATH_FILE" "$PORTAL_READ_ERROR_FILE" "$OMNI_READ" "$OMNI_VAL"' EXIT
omni_ensure_field() {
  # $1 = field. $OMNI_VAL already holds the generated value.
  vault_optional_get homelab/omniroute "$1" "$OMNI_READ" || exit 1
  if [ "$VAULT_READ_STATUS" = present ] && [ -s "$OMNI_READ" ]; then
    echo "    homelab/omniroute/$1 already present"
    return 0
  fi
  vault_optional_get homelab/omniroute - "$OMNI_READ" || exit 1
  if [ "$VAULT_READ_STATUS" = present ]; then
    vault kv patch homelab/omniroute "$1=@$OMNI_VAL" >/dev/null
  else
    vault kv put homelab/omniroute "$1=@$OMNI_VAL" >/dev/null
  fi
  echo "    seeded homelab/omniroute/$1 (generated)"
}
head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 >"$OMNI_VAL"
omni_ensure_field admin-password
head -c 512 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 64 >"$OMNI_VAL"
omni_ensure_field jwt-secret
head -c 4096 /dev/urandom | tr -dc 'a-f0-9' | head -c 64 >"$OMNI_VAL"
omni_ensure_field api-key-secret
head -c 4096 /dev/urandom | tr -dc 'a-f0-9' | head -c 64 >"$OMNI_VAL"
omni_ensure_field storage-encryption-key
rm -f "$OMNI_READ" "$OMNI_VAL"

echo "==> done"
