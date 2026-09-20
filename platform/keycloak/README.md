# Keycloak

## What this is

The homelab's OIDC identity provider, workstation-plan phase 25. Keycloak
26.7.4 runs as one Deployment with no PVC; all durable state lives in the
`keycloak` database on PostgreSQL's existing PVC. Its Application joins
wave 24 because PostgreSQL is at 23. Grafana is the proving OIDC client;
its local admin form remains enabled. Argo CD deliberately is not a client
yet, so recovering the deployment system does not depend on this service.

Design: `docs/superpowers/specs/2026-09-20-keycloak-design.md`.

## Measured

Measured live on **2026-09-20**, not estimated from the resource budget:

| Check | Result |
| --- | --- |
| Steady memory | 602Mi, 91 minutes after the last restart; pod age 116 minutes |
| Peak across real OIDC logins | 603Mi (632242176 bytes) |
| Memory limit | Raised from 768Mi to 896Mi; login peak is 67.3% of the new limit |
| Node committed memory limits after adjustment | 13098Mi / 82% |
| Cold boot to Ready | 21 seconds, within the unchanged 200-second startupProbe budget |
| Persistence after pod deletion | Realm count stayed at 2; state survived without a Keycloak PVC |
| Database backup | `keycloak-db-20260920.sql.gz`, 65175 bytes, gzip valid |
| Realm export | `homelab-realm-20260920.json`, 66050 bytes, valid JSON with `realm: homelab` |

Database-policy acceptance also passed: `pg_up=1`, `redis_up=1`, an
unauthorized pod was denied, and both `keycloak-database` and
`postgres-exporter-role` Sync hooks succeeded.

## How to reach it

<https://keycloak.taildf6cd4.ts.net>, with the admin console at `/admin`.
TLS terminates at the Tailscale proxy; management port 9000 is used only
inside the cluster for probes and Prometheus integration 8.

Port-forward is a **degraded** route: `KC_HOSTNAME` redirects browsers to
the tailnet URL. Unlike Prometheus and Loki, this does not give a working
browser fallback when Tailscale is unavailable.

### The break-glass account

`master` holds one admin and nothing else; day-to-day humans belong in
`homelab`. Read the recovery password only in a private terminal:

```bash
sg k3s-admin -c 'kubectl -n keycloak get secret keycloak-admin -o jsonpath="{.data.password}"' | base64 -d
```

Sign in as `admin` in `master`. This is the only account-recovery path:
there is no SMTP and `resetPasswordAllowed` is false. A reset-password
form without mail delivery would offer recovery that cannot finish.

This account deliberately carries **no TOTP**, while every `homelab`
account must enrol it. A recovery account requiring a second device fails
when that device is what is missing. Its protection is a generated
32-character password held only in Vault as its source of truth, delivered
to `keycloak-admin` by VSO, and a tailnet-only account nobody uses day to
day. Keep Vault's value matched to the live account; see the rotation trap
below.

### Creating a human

In `homelab` → Users → Add user, assign `homelab-admins` or `homelab-users`
and set a temporary password. **Every user needs an email address**, unique
in the realm, or Grafana refuses the login with `user email is not found`.
The default required action `CONFIGURE_TOTP` makes every account enrol
TOTP on its first login, stricter than the roadmap's admins-only request.
Users and their credentials never belong in git.

Grafana maps `homelab-admins` to organization `Admin`, everyone else to
`Viewer`. The Grafana server-admin role remains local-account only.

### The client-secret paste ceremony

On a rebuild, the realm import generates the Grafana client secret;
`configure-vault.sh` only seeds a placeholder. SSO returns `invalid_client`
until this ceremony replaces it. The issuer is Keycloak, not Vault, just
as GitHub issues the GHCR token in the root README's rebuild instructions.
Task 7's paste instructions follow verbatim:

1. Open `https://keycloak.taildf6cd4.ts.net/admin`, sign in as `admin`, switch to the **homelab** realm.
2. Clients → `grafana` → Credentials tab → copy the **Client Secret**.
3. Run these two commands. The second opens a shell inside `vault-0`:

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh'
```

4. Inside that shell, run these lines. The `read` prompt takes the pasted secret with echo off, so it never appears on screen, in argv or in history:

```sh
stty -echo; printf 'client secret: '; read CS; stty echo; echo
printf '%s' "$CS" > /tmp/cs
vault kv put homelab/keycloak-grafana clientSecret=@/tmp/cs
rm -f /tmp/cs; unset CS
exit
```

5. Remove the token:

```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

VSO refreshes within 60 seconds, but Grafana reads the environment variable
only at startup. Wait for `keycloak-grafana`'s SYNCED/HEALTHY/READY columns
to be true, then restart Grafana:

```bash
sg k3s-admin -c 'kubectl -n monitoring get vaultstaticsecret keycloak-grafana'
sg k3s-admin -c 'kubectl -n monitoring rollout restart deploy/monitoring-grafana'
sg k3s-admin -c 'kubectl -n monitoring rollout status deploy/monitoring-grafana'
```

## Things that will surprise you

**The two rotation traps are silent.** `KC_BOOTSTRAP_ADMIN_PASSWORD` is
honoured only when no admin exists. Changing Vault updates the Secret but
does not rotate the live account: rotate in Keycloak first, then update
`homelab/keycloak` in Vault. Separately, regenerating the Grafana client
secret in Keycloak breaks SSO until `homelab/keycloak-grafana` is updated
through the paste ceremony and Grafana restarted. No reconciler performs
either operation; local Grafana login remains available throughout.

**The realm is reproducible, not reconciled.** `--import-realm` uses
`IGNORE_EXISTING`: the seed is applied once, then ignored on every later
boot while that realm exists. Editing `config/realm.yaml` does not change
a running Keycloak, and console edits do not come back to git. The realm
export below is the only portable realm-configuration record of those
edits. `keycloak-config-cli` was rejected because its newest build targeted
26.5.5 against this server's 26.7.4; adding that version dependency to the
identity service was not worth automatic reconciliation.

**Declaring `clientScopes` suppresses Keycloak's built-in scope population.**
The seed therefore explicitly declares only `groups`, `profile` and
`email`, and Grafana references those three as default client scopes.
Referencing a built-in name without declaring it here does not create its
mappers. The `groups` mapper emits bare group names; without it the role
mapping quietly falls through to Viewer.

**Application health is not credential health.** Argo CD assesses the
Deployment, but not `VaultAuth`, `VaultConnection` or `VaultStaticSecret`.
Inspect the VaultStaticSecrets' own SYNCED/HEALTHY/READY columns in both
`keycloak` and `databases`; `Synced`/`Healthy` alone does not prove Vault
authentication or Secret delivery.

### Adding a new OIDC client

Create a confidential OpenID Connect client in the `homelab` console with
standard authorization-code flow. Add the `groups` default scope, plus
`profile` and `email` when required, and restrict `redirectUris` to the
application's exact domain and callback path. Map the group claim to that
application's local permissions explicitly. Add its Vault/VSO credential
path and repeat the same paste ceremony for its own client secret and
Vault path. Console creation alone does not update the git seed: record
the non-secret client structure there for rebuilds and take a fresh export.

### Backups

Manual, under `/backups/services/keycloak`. On 2026-09-20 the directory was
mode **700**, and both artifacts were mode **600**. The parent `/backups`
being mode 0777 does not make these files readable through that private
directory. Keep the narrower permissions: the database holds password
hashes and TOTP seeds, and realm exports can contain client secrets and
user credentials. Never commit either artifact or print their full contents.

The database dump uses PostgreSQL's trusted local Unix socket, so no
password is needed. In Bash:

```bash
set -o pipefail
umask 077
mkdir -p /backups/services/keycloak
chmod 700 /backups/services/keycloak
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- pg_dump -U postgres -d keycloak' \
  | gzip > /backups/services/keycloak/keycloak-db-$(date +%Y%m%d).sql.gz
chmod 600 /backups/services/keycloak/keycloak-db-$(date +%Y%m%d).sql.gz
gzip -t /backups/services/keycloak/keycloak-db-$(date +%Y%m%d).sql.gz
ls -l /backups/services/keycloak/
```

Check the dump command's status as well as `gzip -t`; gzip validity alone
does not prove `pg_dump` succeeded. The measured dump was 65175 bytes.

Live `kc.sh export --file` OOMKilled the pod at 768Mi. At the current 896Mi
limit, this constrained second JVM wrote the realm successfully:

```bash
sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- env JAVA_OPTS_KC_HEAP="-Xms64m -Xmx128m" /opt/keycloak/bin/kc.sh export --realm homelab --dir /tmp/homelab-export'
```

It logs `Realm 'homelab' - data exported` and `Export finished successfully`,
then **exits 1** because the second process cannot bind management port
9000, already occupied by the live server. Do not mistake this for a
zero-exit command, or accept every export error as this known case. Confirm
both success messages and validate the actual JSON before copying it:

```bash
set -o pipefail
umask 077
if sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- cat /tmp/homelab-export/homelab-realm.json' \
  | jq -e '.realm == "homelab"' >/dev/null; then
  sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- cat /tmp/homelab-export/homelab-realm.json' \
    > /backups/services/keycloak/homelab-realm-$(date +%Y%m%d).json
  chmod 600 /backups/services/keycloak/homelab-realm-$(date +%Y%m%d).json
  jq -e '.realm == "homelab"' /backups/services/keycloak/homelab-realm-$(date +%Y%m%d).json
else
  printf '%s\n' 'Realm validation failed; no backup copied.' >&2
fi
sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- rm -f /tmp/homelab-export/homelab-realm.json'
sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- rmdir /tmp/homelab-export'
```

The measured JSON was 66050 bytes. This proves usable backup artifacts
were written and parsed; **restoration has not been tested**. The full
database dump is the durable-state backup; a realm configuration export is
not a substitute for it.

## Files

| File | Purpose |
| --- | --- |
| `config/keycloak.yaml` | Deployment, application and management Service ports, probes, measured limit |
| `config/realm.yaml` | First-import realm structure, groups, scopes, MFA and Grafana client; no users |
| `config/vault-secrets.yaml` | Keycloak ServiceAccount, VaultConnection, VaultAuth and two Secrets |
| `config/database-job.yaml` | Database credential wiring and Sync hook in `databases` |
| `../../environments/homelab/apps/keycloak.yaml` | Wave-24 Application |
| `../../infrastructure/ingress/config/keycloak-ingress.yaml` | Tailnet HTTPS entry point |
| `../../observability/monitoring/targets/keycloak.yaml` | Integration 8 on management port 9000 |
| `../../observability/monitoring/config/vault-secrets.yaml` | Grafana's client-secret delivery |
| `../databases/postgres/config/networkpolicy.yaml` | Namespace deny, PostgreSQL clients and exporter access |
| `../databases/redis/config/networkpolicy.yaml` | Redis clients and exporter access |

## Rollback

Revert the Grafana OIDC integration first if SSO needs abandoning; the
local admin form already works. Reverting the Deployment prunes Keycloak
but preserves PostgreSQL's database. Reverting `realm.yaml` cannot remove
or overwrite an imported realm. To retire the whole Application, follow
the root README's removal procedure: add its cascade finalizer **before**
removing its file, or resources are orphaned. The database and Vault paths
survive and need deliberate, separate removal if truly unwanted.

Database policies belong to the databases, so retiring Keycloak leaves
the fence in place. If a policy regression drops `pg_up`/`redis_up` or
blocks a Sync hook, revert the responsible database-policy change and
hand the push to the operator; these failures can occur while Applications
remain Healthy.
