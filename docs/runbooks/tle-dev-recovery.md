# The Last Exam dev environment — recovery runbook

Source: `The-Last-Exam/server` (product, releases, images); this repository
owns the Kubernetes deployment (TLE ADR-0002). Deployment info pinned at
`apps/tle-dev/deploy-info/` (copies of the release's generated files;
re-vendor on each TLE release with `gh api repos/The-Last-Exam/server/...`).

## Release pinning (promotion gate)

| Item | Value |
| --- | --- |
| TLE release | `v0.0.2` (images); deploy-info still vendored from `v0.0.1` (`704c53a`, config-keys.json unchanged through `v0.0.2` -- confirmed no diff in `deploy-info/`) |
| deploy-info vendored | `config-keys.json`: 2026-10-23 from `v0.0.1`; other artifacts unchanged |
| Edge image `tag@digest` | _fill in_ (api-gateway not published; placeholder stays at 0 replicas, see edge-config.yaml) |
| Service images `tag@digest` | auth-service `v0.0.2@sha256:189309e7eba8f3683c20324f77f5920ceab8ac330b527eaea2d261fea2d6c463`; lobby-service `v0.0.2@sha256:cb5654799f03aea85a60cde4539632a5b6f69dc6e01dec84784701412fa78bed`; matchmaking-service `v0.0.2@sha256:a22b7967000ba061e9fd0a2b1e74bf96926c38bda0f6f7cd11b78698cd8be155`; metadata-service `v0.0.2@sha256:b62fb8e977e4c7a491583456f11d857ad4f9a6d62ec1e85eb3ad38fbfcab788f`; notification-service `v0.0.2@sha256:bda618ae2533dfe3c54853a2a23a7c9ada5a1e50da622d1ec64914773a6fe78e`; player-service `v0.0.2@sha256:e8085c1ac3da888b97d6b34e7dcf9b04eb99014cf90450b3d5cb1e35d4fa0bf8`; social-service `v0.0.2@sha256:9342d0e713b708938282a76fd9886bac249ddb309c2628a23e9fe3ab51a01e7f` (source: The-Last-Exam/server publish-images run 40263029044) |
| Role manifest version | `1` (registry schema `1.3`) |
| Identity headers | 26 headers + 5 prefixes (see `edge-config.yaml`) |
| Public routes | 62 routes across 9 services (see `public-routes.json`) |
| Object storage (S3 endpoint) | _open_: deploy MinIO in-cluster (`tle-dev` + PVC + Service + bucket job) or point `MINIO_ENDPOINT` at an external MinIO; then create the access pair in its console and seed `MINIO_*`. Blocks metadata-service's object-storage path (issue #52 pins `MINIO_ENDPOINT`/`MINIO_BUCKET` to the not-yet-deployed `tle-dev-minio` name so no further wiring change is needed once it exists). |
| Non-secret service config | Per-service ConfigMaps in `service-config.yaml` carry only datastore addresses (test-enforced, `tests/test_tle_datastore_config.py`); every other required non-secret key (ports, `*_ENV`, tunables) is a literal container env value in `workloads.yaml` (issue #52). |
| Datastore credential split | Complete in desired state: per-service ConfigMap addresses, credential-only VSO templates, and the `v0.0.1` configuration contract. |
| Core service workloads | `auth-service`, `lobby-service`, `matchmaking-service`, `metadata-service`, `notification-service`, `player-service`, `social-service` declared in `workloads.yaml` (issue #52), each listening on 8080 for HTTP (see that file's header) regardless of its `.env.example` default port. `matchmaking-service`'s `GAME_MASTER_GRPC_ADDR` points at a not-yet-onboarded `game-master` Service; see Risks below. |

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
      date, and result here. As of 2026-10-23 there were no service
      Deployments to exercise. Issue #52 (this change) declares
      `auth-service`, `lobby-service`, `matchmaking-service`,
      `metadata-service`, `notification-service`, `player-service`, and
      `social-service`; live rollout evidence is still outstanding --
      `metadata-service` needs MinIO (see Object storage above) and
      `matchmaking-service` needs `game-master` (not yet onboarded) before
      both report ready.

## Retirement

Disable portal publication (already off) → final `pg_dump` → revoke Vault
policies/roles + OIDC clients → remove Application + Ingresses → inspect
retained PVCs → remove namespace only after retention decision.
