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
| `penpot-assets` PVC | durable | filesystem copy |
| `api-secret-key` | secret-derived | Vault; rotation invalidates sessions |
| `oidc-client-secret` | secret-derived | Vault; the registration Job re-applies it |
| `mcp-key` | secret-derived | Penpot Integrations; regenerating revokes the old key |
| Redis database 3 | reconstructible | nothing; cache and coordination only |

## Ceremonies

Three steps happen outside git. None is reproduced by a cluster rebuild.

1. **Vault seed.** Run `platform/vault/configure-vault.sh`. It seeds
   `homelab/penpot` (`postgres-password`, `redis-uri`, `api-secret-key`,
   `oidc-client-secret`) and `homelab/penpot/data` (`password`).
2. **MCP key.** In Penpot: *Your account → Integrations → MCP Server* →
   enable, then generate a key. It is shown **once** and is not
   recoverable. Store it in a password manager and seed it into Vault at
   `homelab/penpot` as `mcp-key`.

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
   not by hand. It reads its secret from Vault, so re-running the hook is
   how a rotated secret reaches Keycloak.

## Backup and restore

Local-path storage is single-node and is **not** a backup. Nothing here is
backed up automatically.

```bash
# Database. Consistent because pg_dump takes a snapshot.
kubectl -n databases exec statefulset/postgres -- \
  pg_dump -U postgres -d penpot --format=custom --file=/tmp/penpot.dump
kubectl -n databases cp statefulset/postgres:/tmp/penpot.dump ./penpot.dump

# Assets.
kubectl -n penpot cp statefulset/penpot-assets:/opt/data/assets ./penpot-assets
```

Restore order is assets first, then the database: the database stores asset
references, so it is meaningless without the files behind it.

```bash
kubectl -n databases cp ./penpot.dump statefulset/postgres:/tmp/penpot.dump
kubectl -n databases exec statefulset/postgres -- \
  pg_restore -U postgres -d penpot --clean --if-exists /tmp/penpot.dump
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
`penpot-assets` PVC before removing the namespace. Deleting the namespace
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
