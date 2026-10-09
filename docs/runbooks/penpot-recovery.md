# Penpot recovery runbook

Penpot is the homelab's design platform, deployed from the upstream Helm
chart. Its MCP server lets an AI agent read and modify design files.

## Release record

| Item | Value |
| --- | --- |
| Chart | `penpot` from <http://helm.penpot.app> |
| Chart version | `1.11.3` |
| App version | `2.18.3` |
| Source | <https://github.com/penpot/penpot-helm/releases/tag/penpot-1.11.3> |
| Rollback target | chart `1.10.0` (appVersion `2.18.0`) |

### Image digests

Chart `1.11.3` renders `image: "{{ .Values.backend.image.repository }}:{{
.Values.backend.image.tag }}"` with no digest field anywhere in its values,
so an image digest cannot be pinned through chart values. This is a
**documented deviation** from the onboarding contract, consistent with every
other chart in this repository: no chart-managed component here pins image
digests. Penpot's images are pinned transitively, by exact chart version
selecting exact image tags.

These digests were resolved from Docker Hub for `2.18.3`. Check them on
every upgrade; a mismatch means an upstream tag moved.

| Image | Digest |
| --- | --- |
| `penpotapp/frontend:2.18.3` | `sha256:bb8abe27d53de84c95597f2c02c0e702b2779971fb0703e543f9ecf183e999f6` |
| `penpotapp/backend:2.18.3` | `sha256:2df1b3440d2a82cc3571db211b4ffdfa2b89ccc910759e8d5e9387fb62971b5c` |
| `penpotapp/exporter:2.18.3` | `sha256:418232d6ca3120b1c2bfde298a56a05a1f41f567cd8494deac3fe7fbc186cfbd` |
| `penpotapp/mcp:2.18.3` | `sha256:5e811e6eeb179d80d8781fb0ffd2991560785d150b3676f1ac5e28d63ba9f7c2` |

## State

| Path | Class | Restored from |
| --- | --- | --- |
| `penpot` database | durable | `pg_dump` / `pg_restore` |
| `penpot-data-assets` PVC | durable | `tar` stream through `penpot-backend` |
| `api-secret-key` | secret-derived | Vault; rotation invalidates sessions |
| `oidc-client-secret` | secret-derived | Vault; the registration Job re-applies it |
| `mcp-key` | secret-derived | Penpot Integrations; regenerating revokes the old key |
| Redis database 3 | reconstructible | nothing; cache and coordination only |

## Ceremonies

Three steps happen outside git. None is reproduced by a cluster rebuild.

1. **Vault seed.** Run `platform/vault/configure-vault.sh` **before the
   branch is pushed or merged**: the `penpot` Application's projections and
   the `vso-penpot` role it relies on must exist when Argo CD first syncs it.

   ```sh
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
   sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
   sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
   ```

   It seeds three
   paths: `homelab/penpot` (`postgres-password`, `postgres-username`,
   `api-secret-key`, `oidc-client-secret`),
   `homelab/penpot/data` (`password`), and `homelab/penpot-client`
   (`clientSecret`). The password and the client secret each live at **two**
   paths on purpose — a Job and the chart read different copies. The script
   writes both copies from one value, and on every run it **compares** them.
   If they differ it stops and names both paths rather than choosing for you,
   because either choice would hide a split behind a login that still works.
   If one copy is missing or empty it repairs that one with `vault kv patch`,
   which cannot disturb the other keys. Only when neither copy holds a usable
   value does it stop and ask you to seed one.

   The Redis URI is **not** seeded here. VSO renders it from the shared
   Redis password at `homelab/redis` (the VaultStaticSecret in
   `apps/penpot/config/vault-secrets.yaml`, read through the existing
   `vso-redis-read` policy on the `vso-penpot` role), so rotating
   `homelab/redis` rolls Penpot through VSO with no re-run of this script. A
   `redis-uri` key at `homelab/penpot` left by an older run of the script is
   unused; the script neither reads nor deletes it.
2. **MCP key.** In Penpot: *Your account → Integrations → MCP Server* →
   enable, then generate a key. It is shown **once** and is not
   recoverable. Store it in a password manager, then seed it into Vault.
   This runs **inside the Vault pod**, the same way
   `platform/vault/configure-vault.sh` does. Log in first — the `vault` CLI
   in the pod holds no token until you do — then paste the key at the
   `MCP key:` prompt, and remove the token afterwards:

   ```sh
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh -c '"'"'
     umask 077
     MCP_KEY_FILE=$(mktemp)
     trap "rm -f \"$MCP_KEY_FILE\"" EXIT
     printf "MCP key: "
     read -r MCP_KEY < /dev/tty && printf "\n"
     printf %s "$MCP_KEY" >"$MCP_KEY_FILE"
     unset MCP_KEY
     vault kv patch homelab/penpot mcp-key=@"$MCP_KEY_FILE"
     vault kv get -field=mcp-key homelab/penpot >/dev/null && echo "mcp-key stored"
   '"'"''
   sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
   ```

   The key is read from the pod's terminal and written straight to a private
   `mktemp` file, so it never reaches this machine's disk, `argv`, or `ps` —
   the same `key=@<path>` rule the seed script follows.

   **`vault kv patch`, not `vault kv put`.** `homelab/penpot` already carries
   `postgres-password`, `postgres-username`, `api-secret-key` and
   `oidc-client-secret`, and a KV v2 `put` replaces every key at the path. A
   `put` here would delete all four, leaving Penpot unable to reach
   PostgreSQL or to complete an OIDC login, and the loss is invisible until
   the workload fails. `patch` merges the one key in.

   | Field | Value |
   | --- | --- |
   | Issued | _recorded in Task 10, step 6_ |
   | Expires | _recorded in Task 10, step 6_ |

   Do not leave these as an unlabelled blank. `platform/registry/README.md`
   has exactly that shape for its GHCR token and the root README lists it as a
   known gap; a blank nobody can attribute is how the GHCR expiry became
   unknown to everyone but its issuer. Task 10 owns both fields.

   The expiry date must be filled in. An expired key surfaces as an MCP
   client that cannot connect, with nothing in the cluster naming expiry —
   the same delayed, misleading failure mode as the GHCR token.
3. **Keycloak client.** The `penpot` OIDC client is reconciled by the
   `PostSync` hook in `platform/keycloak/config/client-registration.yaml`,
   not by hand. It reads its secret from the Secret `keycloak/keycloak-penpot-client`,
   which the `penpot` Application (sync wave 25) projects from Vault — so on
   first rollout the hook usually runs **before** that Secret exists. It then
   reconciles the portal client, skips `penpot` with the log line
   `WARNING: penpot client skipped`, and succeeds. Nothing re-runs it on its
   own: **after the `penpot` Application's first sync, re-sync the `keycloak`
   Application** and check the hook log says `created penpot client` (or
   `penpot client already current`):

   ```sh
   sg k3s-admin -c 'kubectl -n keycloak logs job/keycloak-client-registration'
   ```

   Re-syncing `keycloak` is also how a rotated client secret reaches
   Keycloak.

## Backup and restore

Local-path storage is single-node and is **not** a backup. Nothing here is
backed up automatically.

The database lives in the shared PostgreSQL pod `postgres-0` (StatefulSet
`postgres`, container `postgres`). `kubectl cp` takes a **pod** name, not
`statefulset/...`, which it would parse as `namespace/pod`. The assets live
on PVC `penpot-data-assets`, mounted at `/opt/data/assets` in Deployment
`penpot-backend`, and are streamed through that pod with `tar`. The backend
runs as UID 1001; the local-path volume directory is created mode `0777`, so
that user can read and write it.

```bash
# Database. Consistent because pg_dump takes a snapshot.
kubectl -n databases exec postgres-0 -c postgres -- \
  pg_dump -U postgres -d penpot --format=custom --file=/tmp/penpot.dump
kubectl -n databases cp -c postgres postgres-0:/tmp/penpot.dump ./penpot.dump
kubectl -n databases exec postgres-0 -c postgres -- rm -f /tmp/penpot.dump

# Assets.
kubectl -n penpot exec deploy/penpot-backend -- \
  tar -C /opt/data/assets -cf - . > penpot-assets.tar
```

Restore order is assets first, then the database: the database stores asset
references, so it is meaningless without the files behind it. The backend
must be **stopped** while `pg_restore --clean` drops and recreates its
tables, or it fails mid-restore and may write into a half-restored schema.

```bash
# Assets, while the backend is still running (the stream goes through it).
# --no-overwrite-dir: the volume root belongs to root; UID 1001 can write
# into it but not change its mode or times.
kubectl -n penpot exec -i deploy/penpot-backend -- \
  tar -C /opt/data/assets --no-overwrite-dir -xf - < penpot-assets.tar

# Database, with the backend scaled to zero. Stop Argo CD first, or
# selfHeal scales the backend back up mid-restore. `root` goes first: it
# self-heals the penpot Application's own syncPolicy back.
kubectl -n argocd patch application root --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'
kubectl -n argocd patch application penpot --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'
kubectl -n penpot scale deploy/penpot-backend --replicas=0
kubectl -n penpot rollout status deploy/penpot-backend
kubectl -n databases cp -c postgres ./penpot.dump postgres-0:/tmp/penpot.dump
kubectl -n databases exec postgres-0 -c postgres -- \
  pg_restore -U postgres -d penpot --clean --if-exists /tmp/penpot.dump
kubectl -n databases exec postgres-0 -c postgres -- rm -f /tmp/penpot.dump
kubectl -n penpot scale deploy/penpot-backend --replicas=1
kubectl -n penpot rollout status deploy/penpot-backend
# Hand control back: root is the one manifest applied by hand, and its
# next sync restores the penpot Application's automated policy.
kubectl apply -f environments/homelab/root.yaml
```

### Restore drill

Run before promotion and record the result here. The drill restores into a
scratch database, never over live state.

- [ ] Drill date:
- [ ] Restored objects:
- [ ] Assets file count before / after:
- [ ] Result:

## Upgrade

1. Read the chart's changelog and confirm the new `appVersion`.
2. Resolve the new image digests and add them to the table above.
3. Take a database dump and an assets copy first.
4. Bump `targetRevision` in `environments/homelab/apps/penpot.yaml` and both
   version annotations.
5. Watch the backend run its migrations before declaring success. The
   backend owns the schema and migrates on start.
6. Roll back by reverting the chart version **and** restoring the dump if
   migrations were not backward compatible.

## Rollback

Stateful rollback has a database-compatibility caveat the onboarding
contract requires be accounted for: a Penpot downgrade may need the
pre-upgrade dump restored, because the backend's migrations are not
guaranteed reversible. Chart-only rollback (values, flags, resources) needs
no data restore.

## Retirement

Disable portal publication first, then take a final dump and assets copy.
Revoke the Keycloak client, remove the child Application, then inspect the
`penpot-data-assets` PVC before removing the namespace. Deleting the namespace
cascades to the PVC and is not reversible.

## Known gaps

- **The shared cache's eviction policy is not Penpot's recommendation.** The
  shared cache is the `redis:8.2-alpine` release in `databases`, configured
  with `maxmemory 128mb` and `maxmemory-policy allkeys-lru`; Penpot
  documents `volatile-lfu`. Under `allkeys-lru` Penpot's websocket
  coordination keys are evictable, and evicting one degrades live
  collaboration rather than merely slowing a cache. `maxmemory` and the
  policy are cluster-wide and cannot be varied per client. Watch
  `evicted_keys` on that instance; if Penpot evicts under real use the fix is
  a **dedicated** cache for Penpot -- its own `valkey/valkey` StatefulSet in
  the `penpot` namespace, the same shape tle-dev already runs in
  `apps/tle-dev/config/datastores.yaml` -- and not a change to the settings
  of the shared one.
- **No Penpot series reach Grafana Cloud.** No component of Penpot 2.18.3
  exposes a Prometheus endpoint, so there is no ServiceMonitor for it. The
  exporter is the trap: its name suggests it exports metrics, but port 6061
  serves `POST /api/export` and `/readyz` for a headless-browser renderer.
  Health is visible only through the cluster-level `kube_pod_*` and
  `kube_deployment_*` series already in the allowlist.
- **The admin console is disabled.** `enable-admin-console` is absent from
  `config.flags`; it is a fifth deployment on port 3000 that a personal
  installation does not need. Re-enabling it requires a NetworkPolicy rule.
- **Keycloak is the only way in.** `config.flags` disables registration and
  password login explicitly (Penpot 2.18.3 enables both by default), and
  enables `oidc-registration` so a first Keycloak login can create its
  account. There is no password fallback: while Keycloak is down nobody can
  sign in to Penpot.
- **The chart's helm-test pod.** Chart `1.11.3` renders Pod
  `penpot-test-connection` (`curlimages/curl:8.11.1`, annotated
  `helm.sh/hook: test`) and no value disables it. Argo CD is expected not to
  create `helm.sh/hook: test` resources; go-live checks that no such pod
  exists. Should one ever be created, it borrows the frontend's hardened
  securityContext and so passes the namespace's `restricted` admission.
