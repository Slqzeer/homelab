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
| Game control images `tag@digest` | game-master `v0.0.2@sha256:966baf4811fcc71a60069dffa8bef03d1ecc2a10e16f1c48047c479b54662936`; game-service `v0.0.2@sha256:b96d88334ddfb682c32845f251a26a7e6c017b62d9b95076fedc2e809bb7e7ef` (source: the same publish-images run) |
| Admin images `tag@digest` | admin-audit-writer `v0.0.2@sha256:7c0da00a399b089cf4387cc8474395a6c27729397a292792eb09b479b3f48078`; internal-admin-gateway `v0.0.2@sha256:1048cd10d5c7859169231876e20e76329f56a23b6e05ef0cec61db1a41ac4736`; internal-ops-gateway `v0.0.2@sha256:903ce62cef35744b98878678cd0b0807cc6bf684d73e22a028135fa13a40f46e`; projection-writer `v0.0.2@sha256:8287bdcb383c388b7bba1010c5cdfa20082c87e154a3c25460e016231a2a65b1` (source: the same publish-images run) |
| Quest image `tag@digest` | quest-service `v0.0.2@sha256:3dc4bf8f9226efb06695c292c67031073ad7b4f13872bc2748547145b7b1ac26` (source: the same publish-images run) |
| Role manifest version | `1` (registry schema `1.3`) |
| Identity headers | 26 headers + 5 prefixes (see `edge-config.yaml`) |
| Public routes | 62 routes across 9 services (see `public-routes.json`) |
| Object storage (S3 endpoint) | In-cluster `tle-dev-minio` (`datastores.yaml`, PVC `data-tle-dev-minio-0`), reachable only from metadata-service; metadata-service creates its own bucket. Upstream MinIO no longer publishes an image, so this pins the community fork `pgsty/minio` by digest. Generate the `MINIO_*` pair and seed it **before** the first boot; see the [ceremony](tle-dev-vault-ceremony.md). |
| Non-secret service config | Per-service ConfigMaps in `service-config.yaml` carry only datastore addresses (test-enforced, `tests/test_tle_datastore_config.py`); every other required non-secret key (ports, `*_ENV`, tunables) is a literal container env value in `workloads.yaml` (issue #52). |
| Datastore credential split | Complete in desired state: per-service ConfigMap addresses, credential-only VSO templates, and the `v0.0.1` configuration contract. |
| Core service workloads | `auth-service`, `lobby-service`, `matchmaking-service`, `metadata-service`, `notification-service`, `player-service`, `social-service` declared in `workloads.yaml` (issue #52), each listening on 8080 for HTTP (see that file's header) regardless of its `.env.example` default port. `matchmaking-service` reaches the Game Master Service on gRPC `9100` and presents the dedicated matchmaking credential from VSO. |
| Metadata baseline bootstrap | `metadata-bootstrap.yaml` runs `/usr/local/bin/metadata-seed-baseline` from the exact metadata-service release image as a Sync hook. The wave-0 database hook creates `tle_dev_metadata`; the bootstrap runs at wave 1; metadata-service, matchmaking-service, and Game Master wait at wave 2. A command failure blocks the sync and therefore blocks match startup. The packaged `/migrations` and `/baseline/dataset.json` defaults are used without overrides. |
| Game Master | `game-master.yaml` declares one replica with no HPA, a `minAvailable: 1` PDB, namespace-scoped pod RBAC, HTTP `8082`, gRPC `9100`, and only the three issue #44 ingress caller classes. `GAME_MASTER_NAMESPACE=tle-dev`, so the Role and registry target the same restricted namespace. `game-pod-environment.yaml` supplies the shared environment and default resources; `networkpolicy.yaml` limits game pods to the edge and their four runtime dependencies. |
| Admin workloads | `admin-workloads.yaml` declares admin-audit-writer, internal-admin-gateway, internal-ops-gateway and projection-writer in the separate restricted `tle-admin-dev` namespace (issue #53), with the release probes/resources and HPAs. Namespace-local VSO projections in `admin-secrets.yaml` supply database, CSRF, admin-client and GHCR credentials. `admin-networkpolicy.yaml` denies ingress and egress by default; issue #45 adds only the explicit cross-namespace flows. Issue #51 replaces the ops gateway's temporary in-memory session adapter with Keycloak. |
| Quest workloads | `quest-workloads.yaml` declares five role-specific Deployments from one pinned image. `quest-api` scales `1-6`, `quest-evaluator` scales `1-8`, and ingress/operations/outbox stay at one replica without HPAs. Wave 0 creates configuration, VSO projections, and policies; Sync-hook waves 1 and 2 run `quest-migrate` and then `quest-seed-baseline`; the Deployments roll out at wave 3 only after both Jobs succeed. `quest-operations` mounts the checked-in `history-hydration.json` ConfigMap item read-only. |

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

- Namespaces `tle-dev` (player-facing) and `tle-admin-dev` (administrative),
  both restricted PSS `v1.36` and both owned solely by
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

JetStream streams are reconciled by the `tle-dev-jetstream-bootstrap` Argo Sync
hook after NATS becomes healthy; manual `nats stream add` commands are recovery
diagnostics only.

## Vault ceremony extension

Canonical ordered checklist: `docs/runbooks/tle-dev-vault-ceremony.md`
(this section is only a summary — follow that page step by step).

`platform/vault/configure-vault.sh` carries the TLE extension: the original
`vso-tle-dev-auth/data/misc` roles in `tle-dev` and `vso-tle-dev-db` in
`databases`, plus namespace-bound `tle-admin-dev` data, misc, OIDC and GHCR
roles. They reuse only the required Vault paths without sharing Kubernetes
identities across namespaces. The script also creates generated database
passwords + CSRF/internal/webhook secrets,
distinct generated Game Master pod-registry and matchmaking credentials,
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

## Player migration-table inspection ceremony

Player Service uses `golang-migrate`, which requires
`public.schema_migrations(version bigint, dirty boolean)`. A table with the
filename-based contract belongs to another migrator and must not be altered,
renamed, or dropped until its owner and rows are identified.

Run the following read-only inspection with the approved operator identity:

```bash
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- \
  psql -U postgres -d tle_dev -v ON_ERROR_STOP=1 \
  -c "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = '\''schema_migrations'\'';" \
  -c "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = '\''public'\'' AND table_name = '\''schema_migrations'\'' ORDER BY ordinal_position;" \
  -c "SELECT * FROM public.schema_migrations ORDER BY 1;"'
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- \
  pg_dump -U postgres -d tle_dev --schema-only --table=public.schema_migrations'
```

Capture the output in the owning issue without credentials or unrelated row
data. Compare every filename row with the migration directories of the services
sharing `tle_dev`; determine which service created the table and whether its
migrations are complete. Before any repair, take and verify a full `tle_dev`
backup using the PostgreSQL runbook.

The repair decision must explicitly choose a non-conflicting migration-table
contract for every service sharing the database. Do not add `version`/`dirty`
columns to the filename table: that creates a hybrid table understood by
neither migration engine. The approved repair must name the exact DDL, owner,
backup, rollback SQL, and post-repair checks before it is executed. Expected
post-repair evidence is Player Service applying its migrations without a dirty
version, the original filename-based migrator retaining all of its markers,
and both workloads becoming ready. This repository patch deliberately performs
no database mutation.

## Restore drill (before production acceptance)

- `pg_dump tle_dev... | gzip > /backups/databases/tle-dev/...`
- Drop + recreate one dev database from dump; record date/result here:
  _fill in_.

## Live acceptance evidence (promotion gate)

- [ ] Operator in admin-composite group signs in via Keycloak, sees admin tools
- [ ] `tle-admin-dev` and `tle-admin-prod` registration Job succeeded; their
      callbacks use `/auth/callback`, default scopes include `basic` and
      `roles`, and roles match the pinned manifest
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
      `social-service`; live rollout evidence is still outstanding. Game Master
      is now declared by issue #44; its live registry and mutation acceptance
      remains pending the issue #48 game-pod environment described below.
- [ ] On the first fresh-environment reconciliation, the metadata baseline hook
      completes with exactly one active version before metadata-service,
      matchmaking-service, or Game Master starts. A second Application sync
      completes successfully, reports that it kept the same active version,
      and leaves exactly one active row. Follow the operator acceptance below
      and record evidence in `Slqzeer/homelab#77`.
- [ ] On the first admin-workload promotion, all four Deployments in
      `tle-admin-dev` complete rollout, their HTTP probes succeed, and their
      HPAs report the declared 2-4/2-6/2-5/2-5 ranges. Record rollout,
      image-digest, probe and HPA evidence in issue #45 after its namespace
      policies land. Record admin sign-in in issue #3 and ops-session evidence
      in issue #51. Until those issues complete, repository validation is not
      live acceptance.
- [ ] On the first quest rollout, record the two Sync-hook Job completions in
      order, all five role Deployments ready, HPA bounds `1-6` and `1-8`, the
      operations hydration file mounted read-only, and allowed/denied network
      probes. The pinned auth-service access-token format currently omits the
      `iss` and `aud` claims required by quest-api; player quest requests are
      expected to remain denied until the product release contract is aligned.

### Metadata baseline operator acceptance

This is live rollout acceptance for issue #57, tracked by follow-up issue #77;
it is not repository validation. The Homelab operator must run it in a fresh
`tle-dev` environment with the approved write-capable identity; Hermes'
read-only identity must not trigger either sync.

Prerequisites: PostgreSQL is healthy; the Vault configure ceremony has created
the `tle-dev/data` metadata password; the `tle-dev-data` and `ghcr-pull`
projections report ready; and the pinned metadata-service image is pullable.
Before merge, take and inspect a `tle_dev_metadata` backup using the PostgreSQL
runbook. Do not print or copy database credentials into the evidence.

1. Merge the repository change and let Argo CD reconcile `tle-dev`. In the
   Argo CD UI, confirm the Sync operation runs
   `tle-dev-metadata-seed-baseline` before the three wave-2 Deployments. Then
   collect read-only evidence:

   ```bash
   sg k3s-admin -c 'kubectl -n argocd get application tle-dev'
   sg k3s-admin -c \
     'kubectl -n tle-dev get job tle-dev-metadata-seed-baseline'
   sg k3s-admin -c \
     'kubectl -n tle-dev logs job/tle-dev-metadata-seed-baseline'
   sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- \
     psql -U postgres -d tle_dev_metadata -v ON_ERROR_STOP=1 \
     -c "SELECT id, state FROM metadata_versions WHERE state = '\''active'\'';" \
     -c "SELECT count(*) AS active_versions FROM metadata_versions WHERE state = '\''active'\'';"'
   ```

   Expected: the Job is `Complete`; its log says it activated one baseline
   version; the first query returns that version; and the count is exactly `1`.
   Record the active version ID, Job completion timestamp, image digest, Argo CD
   revision, operator, and command output in `Slqzeer/homelab#77`.

2. In the Argo CD UI, start a second Sync operation for `tle-dev` without
   changing the release. `BeforeHookCreation` replaces the prior hook Job. Run
   the same three Job/log/database commands again.

   Expected: the replacement Job is `Complete`; its log says it kept the
   existing active metadata version; the version ID matches step 1; and the
   active row count remains exactly `1`. No API activation step is permitted.

If the first hook fails, do not bypass it or start matches: revert the Git change
and let Argo CD restore the previous desired state, then diagnose the retained
Job logs. If the second hook fails or changes the active version, stop rollout,
retain the database and Job evidence, and revert the Git change. Do not delete,
retire, or replace an active version as part of rollback; restore the pre-rollout
database backup only with explicit operator approval when the database itself
must be recovered.

### Quest rollout operator acceptance

Merging the quest workload change is itself the rollout trigger: Argo CD runs
the database migration and baseline seed hooks automatically. Before merge, the
Homelab operator must take and inspect a `tle_dev_quest` backup using the
approved write-capable identity; Hermes' read-only identity must not run this
ceremony:

```bash
mkdir -p /backups/databases/tle-dev
sg k3s-admin -c \
  'kubectl -n databases exec postgres-0 -- pg_dump -U postgres tle_dev_quest' \
  | gzip > /backups/databases/tle-dev/tle-dev-quest-pre-rollout.sql.gz
zcat /backups/databases/tle-dev/tle-dev-quest-pre-rollout.sql.gz \
  | grep -m1 '^-- PostgreSQL database dump'
sg k3s-admin -c \
  'kubectl -n tle-dev get vaultstaticsecret tle-dev-data'
```

Expected before merge: the dump header is present and `tle-dev-data` reports a
ready projection. Also confirm PostgreSQL, NATS, and the GHCR pull projection
are healthy and the pinned `v0.0.2` quest image is pullable.

After merge and reconciliation, collect the rollout evidence without changing
desired state:

```bash
sg k3s-admin -c 'kubectl -n argocd get application tle-dev'
sg k3s-admin -c \
  'kubectl -n tle-dev get jobs tle-dev-quest-migrate tle-dev-quest-seed-baseline'
sg k3s-admin -c 'kubectl -n tle-dev logs job/tle-dev-quest-migrate'
sg k3s-admin -c 'kubectl -n tle-dev logs job/tle-dev-quest-seed-baseline'
for role in api ingress evaluator operations outbox; do
  sg k3s-admin -c \
    "kubectl -n tle-dev rollout status deployment/tle-dev-quest-$role"
done
sg k3s-admin -c \
  'kubectl -n tle-dev get hpa tle-dev-quest-api tle-dev-quest-evaluator'
sg k3s-admin -c \
  'kubectl -n tle-dev exec deployment/tle-dev-quest-operations -- test -r /config/history-hydration.json'
```

Expected: migration completes before seed, both Jobs are complete, all five
Deployments are available, the HPA bounds are `1-6` and `1-8`, and the
operations hydration file is readable. Record the command output, timestamps,
operator, release digest, and Argo CD revision in `Slqzeer/homelab#54`.

If either hook or any Deployment fails, revert the Git change and let Argo CD
prune the five Deployments. Do not run `quest-migrate -direction down`: that
command rolls back every quest migration. If the previous release cannot use
the migrated schema, restore the pre-rollout dump into an empty
`tle_dev_quest` database using the PostgreSQL restore procedure's plain-SQL
`psql` path in `platform/databases/postgres/README.md`; that restore path is
documented but not yet exercised, so database restoration requires explicit
operator approval and evidence.

### Game Master operator acceptance

This is a live, mutating acceptance procedure. It is **not** a repository
validation step and must be run by the Homelab operator with the approved
write-capable identity. Hermes' read-only identity must not run it.

Prerequisites: issue #48 is merged and reconciled; the Vault configure ceremony
has generated the two distinct Game Master credentials; VSO reports both the
`tle-dev-misc` and `tle-dev-game-pod-credentials` projections ready; the pinned
game-service image is pullable; and the operator has selected an approved
restricted-compatible diagnostic image for the temporary pod below.

Before exercising Game Master mutations, verify the game-pod environment itself:

```bash
kubectl -n tle-dev get configmap tle-dev-game-pod-env -o yaml
kubectl -n tle-dev get limitrange tle-dev-game-pod-defaults -o yaml
kubectl -n tle-dev auth can-i get configmap/tle-dev-game-pod-env \
  --as=system:serviceaccount:tle-dev:tle-dev-game-master
kubectl -n tle-dev auth can-i get secret/tle-dev-game-pod-credentials \
  --as=system:serviceaccount:tle-dev:tle-dev-game-master
kubectl -n tle-dev get secret tle-dev-game-pod-credentials \
  -o go-template='{{range $key, $_ := .data}}{{printf "%s\n" $key}}{{end}}'
pod_registry=$(kubectl -n tle-dev get secret tle-dev-misc-secrets \
  -o jsonpath='{.data.GAME_MASTER_POD_REGISTRY_CREDENTIAL}')
game_pod=$(kubectl -n tle-dev get secret tle-dev-game-pod-credentials \
  -o jsonpath='{.data.GAME_MASTER_POD_REGISTRY_CREDENTIAL}')
matchmaking=$(kubectl -n tle-dev get secret tle-dev-misc-secrets \
  -o jsonpath='{.data.GAME_MASTER_MATCHMAKING_CREDENTIAL}')
test -n "$pod_registry" && test "$pod_registry" = "$game_pod" && \
  test "$pod_registry" != "$matchmaking"
unset pod_registry game_pod matchmaking
```

Expected: the ConfigMap contains the Game Master, Metadata Service, NATS, and
Valkey endpoints without credentials; the LimitRange reports `100m`/`128Mi`
requests and `500m`/`512Mi` limits; both named-object checks return `yes`; the
game-pod Secret lists only `GAME_MASTER_POD_REGISTRY_CREDENTIAL`; and the
silent comparisons exit successfully, proving the game-pod projection matches
Game Master's pod-registry value while differing from the matchmaking value.
Run these comparisons with shell tracing disabled so encoded Secret data is
not written to logs.

After the warm pool is ready, inspect one `role=game-pod` pod and confirm its
effective resources and environment, then run allowed probes to NATS `4222`,
Valkey `6379`, Metadata Service `8080`, and Game Master `9100`. Run denied probes
to another `tle-dev` workload and to an external address. From an edge-labelled
diagnostic pod, probe the game pod only on `8080`; from an unlabelled diagnostic
pod the same probe must time out. Record command output and cleanup evidence in
Slqzeer/homelab#62. Static repository validation does not satisfy these live
checks.

1. Confirm desired state and the single-writer boundary:

   ```bash
   kubectl -n tle-dev get deploy tle-dev-game-master \
     -o jsonpath='{.spec.replicas}{"\n"}'
   kubectl -n tle-dev get hpa tle-dev-game-master
   kubectl -n tle-dev get pdb tle-dev-game-master \
     -o jsonpath='{.spec.minAvailable}{"\n"}'
   kubectl -n tle-dev rollout status deploy/tle-dev-game-master
   ```

   Expected: replicas `1`, HPA `NotFound`, PDB minimum `1`, rollout complete.

2. Create an assigned test game pod through the Game Master ServiceAccount,
   then recreate Game Master and verify its registry rebuilds that pod. Replace
   `<approved-image@sha256:digest>` with the operator-approved diagnostic image:

   ```bash
   cat <<'EOF' | kubectl \
     --as=system:serviceaccount:tle-dev:tle-dev-game-master create -f -
   apiVersion: v1
   kind: Pod
   metadata:
     name: game-master-acceptance
     namespace: tle-dev
     labels:
       role: game-pod
       status: assigned
       match-id: acceptance
   spec:
     restartPolicy: Never
     securityContext:
       runAsNonRoot: true
       runAsUser: 10001
       runAsGroup: 10001
       seccompProfile:
         type: RuntimeDefault
     containers:
       - name: diagnostic
         image: <approved-image@sha256:digest>
         command: ["sh", "-c", "sleep 3600"]
         securityContext:
           allowPrivilegeEscalation: false
           readOnlyRootFilesystem: true
           capabilities:
             drop: ["ALL"]
   EOF
   kubectl -n tle-dev delete pod -l app=tle-dev-game-master
   kubectl -n tle-dev rollout status deploy/tle-dev-game-master
   kubectl -n tle-dev logs deploy/tle-dev-game-master | \
     grep 'pod registry rebuilt'
   ```

   Expected: the create succeeds, the replacement Game Master becomes ready,
   and its log records a rebuilt registry with at least one active pod.

3. Exercise the remaining pod verbs through the same ServiceAccount and clean
   up the test object:

   ```bash
   kubectl --as=system:serviceaccount:tle-dev:tle-dev-game-master \
     -n tle-dev get pod game-master-acceptance
   kubectl --as=system:serviceaccount:tle-dev:tle-dev-game-master \
     -n tle-dev patch pod game-master-acceptance --type merge \
     -p '{"metadata":{"labels":{"acceptance":"patched"}}}'
   kubectl --as=system:serviceaccount:tle-dev:tle-dev-game-master \
     -n tle-dev delete pod game-master-acceptance
   ```

   Expected: get, patch, and delete succeed. Also verify `list` and `watch`
   with `kubectl auth can-i --list --as=system:serviceaccount:tle-dev:tle-dev-game-master
   -n tle-dev`; it must show only `get/list/watch/create/delete/patch` on core
   pods and no wildcard resource or verb.

4. From temporary diagnostic pods carrying, in turn, the exact matchmaking,
   `role=game-pod`, ops-job, and player-edge labels, probe Game Master. Expected:
   matchmaking and game-pod callers connect only to TCP `9100`, ops-job callers
   reach HTTP `8082`, and the player-edge caller times out on both ports. Use the
   same approved image and delete every temporary pod after the probes.

5. Record the date, operator, command output, Game Master log excerpt, allowed
   and denied network results, and cleanup result in Slqzeer/homelab#44. If any
   check fails, delete the temporary pods, revert the Git change that introduced
   the failure, and let Argo CD reconcile the previous immutable revision.

## Retirement

Disable portal publication (already off) → final `pg_dump` → revoke Vault
policies/roles + OIDC clients → remove Application + Ingresses → inspect
retained PVCs → remove namespace only after retention decision.
