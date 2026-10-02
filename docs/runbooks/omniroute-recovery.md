# OmniRoute — recovery runbook

OmniRoute is the single LLM gateway for the agent platform (Hermes and its
workers; design in `/srv/projects/agent-platform/docs/architecture.md`). It
replaces the former host install (`npm i -g omniroute`, data in
`~/.omniroute`).

## Release pinning (promotion gate)

| Field | Value |
| --- | --- |
| Source | upstream `diegosouzapw/OmniRoute`, image `docker.io/diegosouzapw/omniroute` (no product repo of ours) |
| Release | `3.8.50` |
| Image | `docker.io/diegosouzapw/omniroute:3.8.50@sha256:085c57adf499a8aaa9f35ccde95c0df9c11bd9ecd18d6c9edbf3b68b8079ba9d` (multi-arch index; linux/amd64 `sha256:dbf98ed1efcfeb22786abb504d657e88ccec47e864563d3e7745e7fec56038cb`) |
| Image user | `node` (UID/GID 1000), `DATA_DIR=/app/data` |
| Rollback target | none yet (first deployment); record the previous tag@digest here on every upgrade |

## Ownership and state

| Path | Class | Notes |
| --- | --- | --- |
| PVC `omniroute-data` → `/app/data` | **durable** | `storage.sqlite` (+ WAL): provider connections, client API keys, combos, settings, call logs; `db_backups/` (automatic pre-migration backups) |
| `/tmp`, `/app/.next/cache` | reconstructible | emptyDir |
| `homelab/omniroute` in Vault | secret | `admin-password`, `jwt-secret`, `api-key-secret`, `storage-encryption-key` |

`storage-encryption-key` and `api-key-secret` are **required to read the
database**. A database restored without the matching Vault values loses its
provider credentials and client keys. Back up Vault and the PVC together.

## Network

- In: tailnet only (`omniroute.taildf6cd4.ts.net`, Ingress in
  `infrastructure/ingress/config/omniroute-ingress.yaml`), port 20128.
- Out: DNS, and TCP 3128 to `egress-proxy.egress` only. OmniRoute has **no**
  direct internet route; `HTTPS_PROXY` sends provider calls through the
  allowlisting proxy (`platform/egress-proxy`, see its README). With
  `PROXY_FAIL_OPEN=false` a proxy outage fails calls instead of bypassing it.
- New provider connected in the dashboard and its calls fail with a proxy
  403? Add its API/OAuth hosts to `allowed-domains.txt` in
  `platform/egress-proxy/config/configmap.yaml`, bump the proxy's
  `config-revision` annotation, and push.

## Credentials: Vault only, applied at every start

There is no `CHANGEME` and no initial value anywhere in git or the image
config. `platform/vault/configure-vault.sh` generates all four fields once,
never displays them, and never overwrites them. VSO projects them into
`omniroute-secrets`.

OmniRoute itself reads `INITIAL_PASSWORD` only when its database has no
password yet. To keep Vault authoritative after the first boot, the
`apply-admin-password` init container runs the bundled
`bin/reset-password.mjs --password-stdin` with the Vault value on **every**
start. Consequences:

- Rotate the admin password **in Vault**:
  `vault kv patch homelab/omniroute admin-password=@<file>`. VSO's
  `rolloutRestartTargets` restarts the Deployment and the init container
  applies it.
- A password changed in the dashboard lasts only until the next restart.
- Read the current password (operator only):
  `sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault kv get -field=admin-password homelab/omniroute'`
  after `vault login` (and remove the token afterwards, as in the ceremony).

## First deployment

1. Vault ceremony (adds policy `vso-omniroute-read`, role `vso-omniroute`
   and the four generated fields; idempotent for everything else):
   ```sh
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
   sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
   sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
   ```
2. Push to `main`. Argo creates the `omniroute` namespace (`namespaces`),
   the Ingress (`ingress-config`, wave 21) and the app (wave 23).
3. Check: `kubectl -n omniroute get vaultstaticsecret,secret,pod`, then
   `kubectl -n omniroute logs deploy/omniroute -c apply-admin-password`
   (expect "first boot" on the first run, "applied from Vault" afterwards).
4. Sign in at `https://omniroute.taildf6cd4.ts.net` with the Vault password.
5. Point Hermes at it: `providers.omniroute.api: https://omniroute.taildf6cd4.ts.net/v1`
   in `~/.hermes/config.yaml`, and create the `hermes-orchestrator` client key
   in the dashboard (API Keys), stored as `OMNIROUTE_API_KEY` in `~/.hermes/.env`.

## Migrating the former host install (optional)

The host install encrypted its credentials with its own keys
(`~/.omniroute/.env`), which differ from the Vault-generated ones. Two options:

- **Re-add providers** in the dashboard (simplest; OAuth providers need a
  browser login anyway).
- **Config bundle** (VERIFY that bundles carry provider credentials before
  relying on it): start the host install once more, then
  `omniroute sync bundle /tmp/omniroute.bundle`, then
  `omniroute --base-url https://omniroute.taildf6cd4.ts.net sync import /tmp/omniroute.bundle`
  (authenticate as prompted), then `shred -u /tmp/omniroute.bundle`. Never
  commit or paste the bundle; it may contain decrypted credentials.

Client API keys created on the host (`hermes-orchestrator`) are not carried
over; create new ones in the cluster instance.

## Upgrade / rollback

1. Resolve the new tag's index digest (`docker buildx imagetools inspect
   docker.io/diegosouzapw/omniroute:<tag>` or the registry API) and update
   both image references in `apps/omniroute/config/omniroute.yaml`, the
   Application annotations, and the table above (keep the old pair as the
   rollback target).
2. OmniRoute runs SQLite migrations on start and writes a pre-migration
   backup to `/app/data/db_backups/` (`DISABLE_SQLITE_AUTO_BACKUP` unset).
3. Rollback = revert the image commit. If the new release migrated the
   schema, also restore the pre-migration backup (restore drill below),
   because older releases may not read a newer schema.

## Restore drill (before production acceptance)

1. Snapshot: scale to 0 is not possible while Argo self-heals, so copy a
   consistent file from the running pod with SQLite's backup API, e.g.
   `kubectl -n omniroute exec deploy/omniroute -- node -e "require('better-sqlite3')('/app/data/storage.sqlite').backup('/app/data/db_backups/manual.sqlite').then(()=>console.log('ok'))"`
   (VERIFY module path in the image), then `kubectl cp` it off the node.
2. Restore: disable auto-sync on the `omniroute` Application, scale to 0,
   mount the PVC in a restricted-compliant helper pod, replace
   `storage.sqlite` (delete `-wal`/`-shm`), remove the helper, re-enable
   auto-sync.
3. Pass criteria: dashboard login with the Vault password, provider list
   intact, a `/v1/chat/completions` call succeeds with an existing client key.

Record the date and result here. **Not yet performed.**

## Live acceptance evidence (promotion gate)

To record after rollout: tailnet-only reachability, default-deny verified
(a pod in another namespace cannot reach `omniroute:20128`; the OmniRoute pod
cannot open a direct internet connection), provider calls succeed through the
proxy and a non-allowlisted host is denied in the proxy log, password rotation via Vault, measured idle and peak
memory against the 2Gi limit, restore drill result.

## Retirement

Remove the Application and Ingress, take a final PVC + Vault backup, revoke
role `vso-omniroute` and policy `vso-omniroute-read`, delete
`homelab/omniroute`, inspect the retained PVC, then remove the namespace.
