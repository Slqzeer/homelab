# The Last Exam dev environment — recovery runbook

Source: `The-Last-Exam/server` (product, releases, images); this repository
owns the Kubernetes deployment (TLE ADR-0002). Deployment info pinned at
`apps/tle-dev/deploy-info/` (copies of the release's generated files;
re-vendor on each TLE release with `gh api repos/The-Last-Exam/server/...`).

## Release pinning (promotion gate)

| Item | Value |
| --- | --- |
| TLE release | _fill in_ (e.g. `v1.4.0`) |
| deploy-info vendored | _fill in date + release_ |
| Edge image `tag@digest` | _fill in_ |
| Service images `tag@digest` | _fill in per service_ |
| Role manifest version | `1` (registry schema `1.3`) |
| Identity headers | 26 headers + 5 prefixes (see `edge-config.yaml`) |
| Public routes | 62 routes across 9 services (see `public-routes.json`) |

Every Deployment uses `tag@digest`, never `latest`. `images.json` is not in
git (a digest exists only after a push); record the values here at promotion.

## Ownership and state

- Namespace `tle-dev` (restricted PSS `v1.36`), sole owner
  `bootstrap/namespaces/namespaces.yaml`.
- Child Application `environments/homelab/apps/tle-dev.yaml` (wave 25,
  prune/self-heal, `ServerSideApply=true`, `homelab.io/state: durable`).
- Durable state: `tle_dev`, `tle_dev_metadata`, `tle_dev_quest` databases
  in shared Postgres (`databases`); Valkey PVC `tle-dev-valkey-data` (AOF,
  reconstructible cache); NATS JetStream PVC (reconstructible bus).
- Secret-derived: all `DATABASE_URL`s, signing keys, MinIO/push/CSRF
  credentials via Vault (`tle-dev/{auth,data,misc}`) + VSO.
- Reconstructible: NATS streams, Valkey cache contents.

## Vault ceremony extension

`platform/vault/configure-vault.sh` needs, before first sync:

```bash
# policies vso-tle-dev-auth, vso-tle-dev-data, vso-tle-dev-misc, vso-tle-dev-db
# roles bound to SA tle-dev-auth/data/misc (ns tle-dev) and tle-dev-db (ns databases)
# kv put homelab/tle-dev/auth  AUTH_JWT_ED25519_PRIVATE_KEY_PEM=... ...
# kv put homelab/tle-dev/data  password=... metadata_password=... quest_password=...
# kv put homelab/tle-dev/misc  MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... ...
# (Vault holds only per-database passwords, never URLs: the full
# DATABASE_URLs are composed in apps/tle-dev/config/vault-secrets.yaml
# from public parts + these passwords. Same for tle-dev/auth, which
# holds signing keys only; auth-service reads tle_dev via tle-dev-data.)
```

## Upgrade / rollback

1. Vendor new deploy-info; diff strip set + routes + config keys.
2. Pin new `tag@digest`s in this table; update workload images.
3. `kubectl -n argocd get app tle-dev`; validate + live-check below.
4. Rollback target: previous row of this table (immutable revision).
   Stateful rollback: Postgres restores may be required on schema change;
   Valkey/NATS roll back by recreation (reconstructible).

## Restore drill (before production acceptance)

- `pg_dump tle_dev... | gzip > /backups/databases/tle-dev/...`
- Drop + recreate one dev database from dump; record date/result here:
  _fill in_.

## Live acceptance evidence (promotion gate)

- [ ] Operator in admin-composite group signs in via Keycloak, sees admin tools
- [ ] Operator without TLE roles refused
- [ ] Player token refused by rotation endpoint (`AUTH_FORBIDDEN`); no token → 401
- [ ] `/auth/admin/rotate-key/promote` unreachable via tailnet host
- [ ] Allowed/denied NetworkPolicy flows probed (edge→svc ok, svc→Keycloak
      8080 ok from auth-service only, denied pod refused)
- [ ] Metrics present (`up` for tle-dev-edge/valkey/nats); idle/peak
      resources recorded: _fill in_
- [ ] OIDC + tailnet-only reachability verified; secret rotation restarts
      consumers

## Retirement

Disable portal publication (already off) → final `pg_dump` → revoke Vault
policies/roles + OIDC clients → remove Application + Ingresses → inspect
retained PVCs → remove namespace only after retention decision.
