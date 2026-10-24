# The Last Exam dev environment — recovery runbook

Source: `The-Last-Exam/server` (product, releases, images); this repository
owns the Kubernetes deployment (TLE ADR-0002). Deployment info pinned at
`apps/tle-dev/deploy-info/` (copies of the release's generated files;
re-vendor on each TLE release with `gh api repos/The-Last-Exam/server/...`).

## Release pinning (promotion gate)

| Item | Value |
| --- | --- |
| TLE release | `v0.0.1` (`704c53a`, includes The-Last-Exam/server#114 via PR #117) |
| deploy-info vendored | `config-keys.json`: 2026-10-23 from `v0.0.1`; other artifacts unchanged |
| Edge image `tag@digest` | _fill in_ |
| Service images `tag@digest` | _fill in per service_ |
| Role manifest version | `1` (registry schema `1.3`) |
| Identity headers | 26 headers + 5 prefixes (see `edge-config.yaml`) |
| Public routes | 62 routes across 9 services (see `public-routes.json`) |
| Object storage (S3 endpoint) | _open_: deploy MinIO in-cluster (`tle-dev` + PVC + Service + bucket job) or point `MINIO_ENDPOINT` at an external MinIO; then create the access pair in its console and seed `MINIO_*`. Blocks metadata-service only — nothing deployed today consumes it. |
| Non-secret service config | Per-service ConfigMaps in `service-config.yaml`; datastore URLs contain no userinfo. Other `config-keys.json` entries with `"secret": false` follow the same pattern when Deployments are authored. |
| Datastore credential split | Complete in desired state: per-service ConfigMap addresses, credential-only VSO templates, and the `v0.0.1` configuration contract. |

Every Deployment uses `tag@digest`, never `latest`. `images.json` is not in
git (a digest exists only after a push); record the values here at promotion.

### Datastore credential split rollout

The order below is a compatibility boundary; do not combine or reorder it.

1. `service-config.yaml` provides every datastore address without userinfo, and
   `tle-dev-data-secrets` projects the database user/password pairs.
2. The pinned `v0.0.1` release contains The-Last-Exam/server#114: its tag commit
   `704c53a` is the merge commit of PR #117. The release's
   `deploy-info/config-keys.json` is vendored here and marks address keys
   non-secret and credential keys secret.
3. The legacy `DATABASE_URL`, `METADATA_DATABASE_URL`, and
   `QUEST_DATABASE_URL` Vault templates are removed. A read-only cluster check
   on 2026-10-23 found no TLE service Deployments consuming either contract;
   only the zero-replica edge placeholder and the ready Valkey/NATS
   StatefulSets exist. The first service-workload promotion must perform the
   connection check below.

Rollback the credential split by restoring the prior Git revision so VSO
recreates the legacy URL keys before pinning any pre-`v0.0.1` server release.

## Ownership and state

- Namespace `tle-dev` (restricted PSS `v1.36`), sole owner
  `bootstrap/namespaces/namespaces.yaml`.
- Child Application `environments/homelab/apps/tle-dev.yaml` (wave 25,
  prune/self-heal, `ServerSideApply=true`, `homelab.io/state: durable`).
- Durable state: `tle_dev`, `tle_dev_metadata`, `tle_dev_quest` databases
  in shared Postgres (`databases`); Valkey PVC `tle-dev-valkey-data` (AOF,
  reconstructible cache); NATS JetStream PVC (reconstructible bus).
- Secret-derived: database users/passwords, signing keys, MinIO/push/CSRF
  credentials via Vault (`tle-dev/{auth,data,misc}`) + VSO. Datastore
  addresses come from per-service ConfigMaps.
- Reconstructible: NATS streams, Valkey cache contents.

## Vault ceremony extension

Canonical ordered checklist: `docs/runbooks/tle-dev-vault-ceremony.md`
(this section is only a summary — follow that page step by step).

`platform/vault/configure-vault.sh` carries the tle-dev extension: 4
policies/roles (`vso-tle-dev-auth/data/misc` in `tle-dev`, `vso-tle-dev-db`
in `databases`), generated db passwords + CSRF/internal/webhook secrets,
and `disabled` placeholders for product-issued keys. Run it first (inside
vault-0); it never overwrites. It also creates the independent Keycloak
client secrets for `tle-admin-dev` and `tle-admin-prod`; Keycloak's VSO role
can read only those two paths for TLE administration.

Product-issued values (JWT private key, MinIO pair, enabled OAuth/push
providers) are then patched over the placeholders, never typed on a
command line — see the ceremony page for the exact commands.

Per-key sources are documented in the `.env.example` templates. The
`*- REQUIRED` placeholders (JWT key, MinIO pair) must be replaced before
workloads can run; sync alone is not enough.

## Upgrade / rollback

1. Vendor new deploy-info; diff strip set + routes + config keys. When its
   `role-manifest.json` changes, regenerate the automatic Keycloak role
   reconciliation ConfigMap and commit it with the release pin:

   ```bash
   python3 platform/keycloak/tle-operator/generate.py \
     apps/tle-dev/deploy-info/role-manifest.json \
     platform/keycloak/config/tle-admin-role-reconciliation.yaml
   ```

2. Pin new `tag@digest`s in this table; update workload images.
3. Sync Keycloak as well as `tle-dev`, then verify the Keycloak PostSync Job
   completed: `kubectl -n keycloak get job keycloak-client-registration` and
   `kubectl -n keycloak logs job/keycloak-client-registration`. The Job
   registers `tle-admin-dev` and `tle-admin-prod` and reconciles their roles
   from the pinned manifest. Group-to-composite assignment remains a manual
   Keycloak-console operation.
4. `kubectl -n argocd get app tle-dev`; validate + live-check below.
5. Rollback target: previous row of this table (immutable revision).
   Stateful rollback: Postgres restores may be required on schema change;
   Valkey/NATS roll back by recreation (reconstructible).

## Restore drill (before production acceptance)

- `pg_dump tle_dev... | gzip > /backups/databases/tle-dev/...`
- Drop + recreate one dev database from dump; record date/result here:
  _fill in_.

## Live acceptance evidence (promotion gate)

- [ ] Operator in admin-composite group signs in via Keycloak, sees admin tools
- [ ] `tle-admin-dev` and `tle-admin-prod` registration Job succeeded; their
      callbacks use `/auth/callback` and roles match the pinned manifest
- [ ] Operator without TLE roles refused
- [ ] Player token refused by rotation endpoint (`AUTH_FORBIDDEN`); no token → 401
- [ ] `/auth/admin/rotate-key/promote` unreachable via tailnet host
- [ ] Allowed/denied NetworkPolicy flows probed (edge→svc ok, svc→Keycloak
      8080 ok from auth-service only, denied pod refused)
- [ ] Metrics present (`up` for tle-dev-edge/valkey/nats); idle/peak
      resources recorded: _fill in_
- [ ] OIDC + tailnet-only reachability verified; secret rotation restarts
      consumers
- [ ] On the first service-workload promotion, every service reports ready
      while taking datastore addresses from its `tle-dev-*-config` ConfigMap
      and database credentials from `tle-dev-data-secrets`; record release,
      date, and result here. As of 2026-10-23 there are no service Deployments
      to exercise; the read-only check found Argo CD `tle-dev` Synced/Healthy at
      `ef51252`, edge at 0 replicas, and Valkey/NATS ready.

## Retirement

Disable portal publication (already off) → final `pg_dump` → revoke Vault
policies/roles + OIDC clients → remove Application + Ingresses → inspect
retained PVCs → remove namespace only after retention decision.
