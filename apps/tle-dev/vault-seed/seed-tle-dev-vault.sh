#!/bin/sh
# Push human-provided tle-dev Vault values from gitignored .env files.
#
# configure-vault.sh (run first, inside vault-0) owns policies, roles, the
# generated passwords and the `disabled` placeholders, so after it every
# VaultStaticSecret syncs. This script only OVERWRITES placeholders with
# real product values: JWT keys, OAuth secrets, MinIO, push credentials.
# Empty or absent keys are skipped, so generated values are never clobbered.
#
# Usage:
#   1. cp apps/tle-dev/vault-seed/tle-dev-auth.env.example \
#        apps/tle-dev/vault-seed/tle-dev-auth.env   # and -misc.env
#   2. fill the values (KEY=@file for the PEM/JSON file payloads)
#   3. export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
#   4. ./seed-tle-dev-vault.sh
#
# Values travel to Vault over the exec stdin pipe into a temp file read as
# key=@file: they never appear in argv (`ps`) on either side, the same
# reason configure-vault.sh uses key=@<path>.
set -eu

SEED_DIR=$(dirname "$0")

die() {
  echo "seed-tle-dev-vault.sh: $*" >&2
  exit 1
}

command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
[ -n "${KUBECONFIG:-}" ] || die "KUBECONFIG is unset (export KUBECONFIG=/etc/rancher/k3s/k3s.yaml)"
kubectl -n vault get pod vault-0 >/dev/null 2>&1 || die "cannot reach vault-0 (wrong cluster?)"

# Refuse to run if a real values file was ever committed. Examples are the
# only files in this directory that may be tracked.
tracked=$(git -C "$SEED_DIR" ls-files '*.env' '*.pem' '*.json' 2>/dev/null || true)
[ -z "$tracked" ] || die "refusing: real values appear tracked by git: $tracked"

remote_patch() {
  # $1 = vault path, $2 = key, value arrives on stdin.
  kubectl -n vault exec -i vault-0 -- sh -c '
    p="$1"; k="$2"
    f=$(mktemp) || exit 1
    trap "rm -f \"$f\"" EXIT
    cat >"$f" || exit 1
    [ -s "$f" ] || exit 0
    vault kv patch "$p" "$k"@"$f" >/dev/null || exit 1
  ' sh "$1" "$2"
}

push_env_file() {
  # $1 = vault path, $2 = env filename in SEED_DIR.
  file="$SEED_DIR/$2"
  [ -f "$file" ] || {
    echo "  (no $2, skipping)"
    return 0
  }
  pushed=0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) echo "  ignoring malformed line: $line" >&2; continue ;;
    esac
    key=${line%%=*}
    key=$(printf '%s' "$key" | tr -d '[:space:]')
    value=${line#*=}
    case "$value" in
      '"'*'"')
        value=${value#'"'}
        value=${value%'"'}
        ;;
    esac
    [ -n "$value" ] || continue
    case "$value" in
      @*)
        src="$SEED_DIR/${value#@}"
        [ -f "$src" ] || {
          echo "  $key: file $src not found, skipping" >&2
          continue
        }
        remote_patch "$1" "$key" <"$src" || die "patch $1/$key failed"
        ;;
      *)
        printf '%s' "$value" | remote_patch "$1" "$key" || die "patch $1/$key failed"
        ;;
    esac
    echo "  patched $key"
    pushed=$((pushed + 1))
  done <"$file"
  echo "  ($pushed keys patched)"
}

echo "==> homelab/tle-dev/auth <= .env.tle-dev-auth"
push_env_file homelab/tle-dev/auth .env.tle-dev-auth
echo "==> homelab/tle-dev/data <= .env.tle-dev-data"
push_env_file homelab/tle-dev/data .env.tle-dev-data
echo "==> homelab/tle-dev/misc <= .env.tle-dev-misc"
push_env_file homelab/tle-dev/misc .env.tle-dev-misc

echo "==> verifying (key names only, never values)"
kubectl -n vault exec vault-0 -- sh -c '
  for p in tle-dev/auth tle-dev/data tle-dev/misc; do
    echo "-- homelab/$p"
    vault kv get -format=json "homelab/$p" 2>/dev/null | grep -o "\"[a-zA-Z_]*\":" || echo "  (unreadable)"
  done
'
echo "done. Restarted workloads pick up new values via VSO rollout targets."
