# Keycloak

## What this is

The homelab's OIDC identity provider, workstation-plan phase 25. Keycloak
26.7.4 runs as one Deployment with no PVC; all durable state lives in the
`keycloak` database on PostgreSQL's existing PVC. Its Application joins
wave 24 because PostgreSQL is at 23. Grafana is the proving OIDC client;
its local admin form remains enabled. The portal's confidential client is
reconciled by a PostSync hook with only `homelab-portal` on its allowlist.
The hook does not change Grafana, users, groups, or realm settings.
Argo CD deliberately is not a client
yet, so recovering the deployment system does not depend on this service.

Design: `docs/superpowers/specs/2026-09-20-keycloak-design.md`.

## Measured

Measured live on **2026-09-20**, not estimated from the resource budget:

| Check | Result |
| --- | --- |
| Steady memory | 602Mi, 91 minutes after the last restart; pod age 116 minutes |
| Observed 15-minute maximum | 603Mi (632242176 bytes); login attribution unverified |
| Memory limit | Raised from 768Mi to 896Mi; observed maximum is 67.3% of the new limit |
| Node committed memory limits after adjustment | 13098Mi / 82% |
| Pod restart to Ready | 21 seconds against an initialized database; startupProbe budget 200 seconds |
| Persistence after pod deletion | Realm count stayed at 2; state survived without a Keycloak PVC |
| Database backup | `keycloak-db-20260920.sql.gz`, 65175 bytes, gzip valid |
| Realm export | `homelab-realm-20260920.json`, 66050 bytes, valid JSON with `realm: homelab` |

Database-policy acceptance also passed: `pg_up=1`, `redis_up=1`, an
unauthorized pod was denied, and both `keycloak-database` and
`postgres-exporter-role` Sync hooks succeeded.

**Browser acceptance is pending.** Earlier operator-reported OIDC logins
were not independently established: the database dump at
2026-09-20 15:34:32 +0200 contained zero `homelab` users and one `master`
user. Admin and Viewer mapping, temporary-password replacement, TOTP
enrolment and a subsequent TOTP challenge, and local Grafana login must
be rerun explicitly. The 603Mi observation is a 15-minute maximum, not a
proven login peak. The 21-second restart did not test first installation
against an empty database. The 896Mi limit remains supported by the steady
sample and the export memory evidence below.

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
`UPDATE_PASSWORD` must also be registered and enabled to replace temporary
passwords; it is non-default, so existing permanent passwords are not
changed on every login. Users and their credentials never belong in git.

Grafana maps `homelab-admins` to organization `Admin`, everyone else to
`Viewer`. The Grafana server-admin role remains local-account only.

### Repair the existing realm and complete browser acceptance

This is an operator procedure. The corrected seed applies to fresh
imports only: `IGNORE_EXISTING` means pushing it or restarting Keycloak
cannot repair the existing `homelab` realm. Do not delete/reimport that
realm. Take a database backup using the atomic recipe below before
changing its authentication settings.

1. Open `https://keycloak.taildf6cd4.ts.net/admin`, sign in with the
   break-glass `admin` in `master`, then select **homelab** in the realm
   selector. Confirm that realm before changing anything.
2. Open **Authentication → Required actions**. If **Update Password** is
   absent, choose **Register** and register **Update Password**
   (`UPDATE_PASSWORD`). Enable it and leave **Default action** off. Move
   it above **Configure OTP** so password replacement precedes enrolment.
3. Keep **Configure OTP** (`CONFIGURE_TOTP`) enabled with **Default action**
   on. Reload the page and verify both rows and their settings. Leave the
   `master` realm unchanged.
4. Create one human in `homelab-admins` and one in `homelab-users`, each
   with a unique email address. Set their passwords in **Credentials**
   with **Temporary** on. For a user created before the default action was
   enabled, also add **Configure OTP** to that user's **Required user
   actions**; changing the realm default does not retrofit existing users.
5. In a fresh private browser session, open
   `https://grafana.taildf6cd4.ts.net` and choose **Sign in with Keycloak**.
   For the admin user, verify that the temporary password must be replaced,
   then enrol TOTP. Confirm Grafana assigns organization **Admin**.
6. Fully sign out, close the private session, and open a new one. Verify
   login requires the new password and a TOTP code. Repeat steps 5–6 for
   the other user and confirm organization **Viewer**.
7. In a separate fresh session, verify Grafana's local admin form with its
   Vault-held credential. Record dated pass/fail results for both roles,
   password replacement, TOTP enrolment/challenge and local login; no
   passwords, tokens or TOTP seeds belong in that record.
8. Take fresh database and `--users realm_file` backups after onboarding,
   with the realm validation minimum set to the number of users just
   verified. The earlier zero-user backups do not cover these accounts.

### The client-secret paste ceremony

On a rebuild, the realm import generates the Grafana client secret;
`configure-vault.sh` only seeds a placeholder. SSO returns `invalid_client`
until this ceremony replaces it. The issuer is Keycloak, not Vault, just
as GitHub issues the GHCR token in the root README's rebuild instructions.

Before the Vault write, record a fingerprint of the current Kubernetes
Secret in the host's Bash shell. Keep this same shell open through the
paste and restart checks. Only the SHA-256 fingerprint enters a variable;
the Secret value travels through pipes, never argv or terminal output.
Stop if the baseline cannot be read. A TRUE VSO status may still describe
the old value and does not prove the replacement has arrived.

```bash
set -o pipefail
keycloak_grafana_fingerprint() { sg k3s-admin -c 'kubectl -n monitoring get secret keycloak-grafana -o json' | jq -er '.data.clientSecret | select(type == "string" and length > 0)' | sha256sum; }
KC_GRAFANA_SECRET_BEFORE=$(keycloak_grafana_fingerprint) || unset KC_GRAFANA_SECRET_BEFORE
test -n "${KC_GRAFANA_SECRET_BEFORE:-}" && printf '%s\n' 'Baseline recorded; continue with the paste ceremony.'
```

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

Back in the same host shell, VSO normally refreshes within 60 seconds.
Poll for up to two minutes for a successful read whose fingerprint differs
from the baseline. Missing/empty Secrets and read errors do not pass. The
restart is gated on that change, so a still-TRUE status for a placeholder
or old secret cannot trigger it. Grafana reads the value only at startup.

```bash
sg k3s-admin -c 'kubectl -n monitoring get vaultstaticsecret keycloak-grafana'
KC_GRAFANA_SECRET_CHANGED=false
for attempt in $(seq 1 60); do KC_GRAFANA_SECRET_AFTER=$(keycloak_grafana_fingerprint) && [ -n "${KC_GRAFANA_SECRET_BEFORE:-}" ] && [ "$KC_GRAFANA_SECRET_AFTER" != "$KC_GRAFANA_SECRET_BEFORE" ] && { KC_GRAFANA_SECRET_CHANGED=true; break; }; sleep 2; done
if [ "$KC_GRAFANA_SECRET_CHANGED" = true ]; then sg k3s-admin -c 'kubectl -n monitoring rollout restart deploy/monitoring-grafana' && sg k3s-admin -c 'kubectl -n monitoring rollout status deploy/monitoring-grafana'; else printf '%s\n' 'No verified secret change; Grafana was not restarted. Check the Vault write and VSO, then retry.' >&2; fi
unset KC_GRAFANA_SECRET_BEFORE KC_GRAFANA_SECRET_AFTER KC_GRAFANA_SECRET_CHANGED
```

If the baseline was already the intended value, this intentionally does
not prove a new delivery or restart Grafana. Do not use a timeout as proof
that the paste succeeded; investigate before repeating the ceremony.

## Things that will surprise you

**OIDC uses split routing.** A browser reaches the authorization endpoint
at `https://keycloak.taildf6cd4.ts.net`, but Grafana exchanges the code and
loads userinfo through `http://keycloak.keycloak.svc.cluster.local:8080`.
Pods use cluster DNS, which does not resolve the Tailscale MagicDNS name.
Pointing Grafana's back-channels at the public URL fails before client-secret
validation with `lookup keycloak.taildf6cd4.ts.net ... no such host`.

**The two rotation traps are silent.** `KC_BOOTSTRAP_ADMIN_PASSWORD` is
honoured only when no admin exists. Changing Vault updates the Secret but
does not rotate the live account: rotate in Keycloak first, then update
`homelab/keycloak` in Vault. Separately, regenerating the Grafana client
secret in Keycloak breaks SSO until `homelab/keycloak-grafana` is updated
through the paste ceremony and Grafana restarted. No reconciler performs
either operation; the local Grafana form remains enabled throughout, with
actual login acceptance pending the procedure above.

**The realm is reproducible, not reconciled.** `--import-realm` uses
`IGNORE_EXISTING`: the seed is applied once, then ignored on every later
boot while that realm exists. Editing `config/realm.yaml` does not change
a running Keycloak, and console edits do not come back to git. The portal
client is the sole exception: `config/client-registration.yaml` reconciles
its allowlisted fields after each Argo sync. The realm
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

**Declaring `requiredActions` also suppresses built-in registration.**
The seed explicitly enables `UPDATE_PASSWORD` (non-default) and
`CONFIGURE_TOTP` (default). Omitting the former prevents replacement of
temporary passwords. Repair existing realms with the operator procedure
above; a new git seed has no effect on already imported realms.

**Application health is not credential health.** Argo CD assesses the
Deployment, but not `VaultAuth`, `VaultConnection` or `VaultStaticSecret`.
Inspect the VaultStaticSecrets' own SYNCED/HEALTHY/READY columns in both
`keycloak` and `databases`; `Synced`/`Healthy` alone does not prove Vault
authentication or Secret delivery.

### Adding a new OIDC client

The portal client has a separate, repeatable path. Run
`platform/vault/configure-vault.sh` inside Vault using the procedure in
`platform/vault-secrets-operator/README.md` before syncing Keycloak. It
generates one shared OIDC client secret under `homelab/keycloak-portal`
(`clientSecret`) and `homelab/portal` (`oidc-client-secret`), plus
`session-current-key` for the portal. It leaves existing values in place;
if the two OIDC copies differ, the Keycloak copy wins. An existing
`session-previous-key` survives because the script patches individual
fields. The `vso-keycloak` role reads only Keycloak's three paths, and
`vso-portal` is bound only to `homelab-portal` in namespace `portal`.

The `keycloak-client-registration` PostSync Job reads
`keycloak-admin` and `keycloak-portal-client` from VSO. It creates or
updates only `homelab-portal`, with callback
`https://portal.taildf6cd4.ts.net/auth/callback`, logout
`https://portal.taildf6cd4.ts.net/auth/logout`, exact portal origin,
authorization-code flow, and the `groups` scope. It fails if duplicate
client IDs exist. Repeated syncs with the same Vault value make no write.
Inspect Job success and the VaultStaticSecret status; do not print
Kubernetes Secret contents. A Keycloak ingress policy permits only its
selected proxy, Prometheus, registration Job, Grafana, portal, and future
Nextcloud pods on the ports they use.

For other clients, create a confidential OpenID Connect client in the `homelab` console with
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
password is needed. Run this single-line command in Bash. It captures one
UTC timestamp, creates a private temporary file on the backup filesystem,
checks producer/pipeline success, gzip integrity and the SQL completion
marker plus realm table, then atomically publishes the artifact. Failure
removes only the new temporary file; existing backups are never overwritten.

```bash
( set -eu; set -o pipefail; umask 077; mkdir -p /backups/services/keycloak; chmod 700 /backups/services/keycloak; KC_BACKUP_STAMP=$(date -u +%Y%m%dT%H%M%S.%NZ); KC_DB_TMP=$(mktemp /backups/services/keycloak/.keycloak-db.XXXXXX); trap 'rm -f -- "$KC_DB_TMP"' EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; KC_DB_FINAL=/backups/services/keycloak/keycloak-db-${KC_BACKUP_STAMP}.sql.gz; sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- pg_dump -U postgres -d keycloak' | gzip > "$KC_DB_TMP"; test -s "$KC_DB_TMP"; gzip -t "$KC_DB_TMP"; gzip -cd "$KC_DB_TMP" | awk '/^CREATE TABLE public.realm / {realm=1} /^-- PostgreSQL database dump complete$/ {complete=1} END {exit !(realm && complete)}'; mv -nT -- "$KC_DB_TMP" "$KC_DB_FINAL"; test ! -e "$KC_DB_TMP"; printf 'Database backup published: %s\n' "$KC_DB_FINAL" )
```

`set -o pipefail` propagates a failed `kubectl`/`pg_dump` through gzip;
gzip validity alone cannot prove success. `mv -nT` also refuses a filename
collision, and the following check makes that a failure. Nanosecond UTC
timestamps distinguish same-day runs. The measured dump was 65175 bytes.

Live `kc.sh export --file` OOMKilled the pod at 768Mi. At the current 896Mi
limit, the constrained second JVM with `--dir /tmp/homelab-export` wrote
the measured realm configuration successfully. That original command used
the default `different_files` user strategy; the measured realm JSON alone
does not prove user credentials were included.

For subsequent backups, explicitly use `--users realm_file`: pinned
26.7.4's export help supports it, and the
[Keycloak export guide](https://www.keycloak.org/server/importExport)
defines it as including users in the realm JSON. This keeps users and
their credentials in `homelab-realm.json` instead of separate user files.
The user-inclusive variant is checked against the pinned help; it has not
been measured live. Its size need not match the original 66050 bytes.

The measured live command logged `Realm 'homelab' - data exported` and
`Export finished successfully`, then **exited 1** because the second
process could not bind management port 9000, already occupied by the live
server. The command below accepts exit 1 only with both success markers
and the specific management-port-9000/address-in-use diagnostics. All
other producer failures stop it. A successful transfer and artifact
validation are required even for that known case; no log is printed.
The diagnostics were reproduced with isolated Keycloak 26.7.4:
`Unable to start the management interface on 0.0.0.0:9000`, followed by
`Address already in use` after both export-success messages.

The database dump at **2026-09-20 15:34:32 +0200** establishes **zero
`homelab` users and one `master` user** then. It does not back up humans
created later. Keep `KC_MIN_USERS=1` after onboarding, or set it to the
known number of onboarded users for a stronger check. Only for an
explicitly verified pre-onboarding realm may it be set to `0`; zero users
then proves configuration only. Take fresh database and realm backups
after onboarding. Run exports serially because the pod scratch directory
is shared. The following command clears that exact directory first,
transfers once into a private local temporary file and checks that copy
before atomic publication:

```bash
( set -eu; set -o pipefail; umask 077; KC_MIN_USERS=1; mkdir -p /backups/services/keycloak; chmod 700 /backups/services/keycloak; KC_BACKUP_STAMP=$(date -u +%Y%m%dT%H%M%S.%NZ); KC_REALM_TMP=$(mktemp /backups/services/keycloak/.homelab-realm.XXXXXX); kc_export_cleanup() { rm -f -- "$KC_REALM_TMP"; sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- rm -rf -- /tmp/homelab-export' >/dev/null 2>&1 || true; }; trap kc_export_cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; KC_REALM_FINAL=/backups/services/keycloak/homelab-realm-${KC_BACKUP_STAMP}.json; sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- rm -rf -- /tmp/homelab-export'; KC_EXPORT_STATUS=0; KC_EXPORT_LOG=$(sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- env JAVA_OPTS_KC_HEAP="-Xms64m -Xmx128m" /opt/keycloak/bin/kc.sh export --realm homelab --dir /tmp/homelab-export --users realm_file' 2>&1) || KC_EXPORT_STATUS=$?; if [ "$KC_EXPORT_STATUS" -ne 0 ]; then test "$KC_EXPORT_STATUS" -eq 1; printf '%s\n' "$KC_EXPORT_LOG" | grep -F "Realm 'homelab' - data exported" >/dev/null; printf '%s\n' "$KC_EXPORT_LOG" | grep -F 'Export finished successfully' >/dev/null; printf '%s\n' "$KC_EXPORT_LOG" | grep -E 'Unable to start the management interface on .*:9000$' >/dev/null; printf '%s\n' "$KC_EXPORT_LOG" | grep -F 'Address already in use' >/dev/null; fi; sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- cat /tmp/homelab-export/homelab-realm.json' > "$KC_REALM_TMP"; jq -e --argjson minimum "$KC_MIN_USERS" '.realm == "homelab" and ((.users // []) | type == "array" and length >= $minimum)' "$KC_REALM_TMP" >/dev/null; sg k3s-admin -c 'kubectl -n keycloak exec deploy/keycloak -- rm -rf -- /tmp/homelab-export'; mv -nT -- "$KC_REALM_TMP" "$KC_REALM_FINAL"; test ! -e "$KC_REALM_TMP"; printf 'Realm backup published: %s\n' "$KC_REALM_FINAL" )
```

Failure removes only the new local temporary file and attempts remote
scratch cleanup, preserving every existing final backup. Cleanup removes
the entire exact `/tmp/homelab-export` directory,
including any credential-bearing user files left by an interrupted or
older export. It never targets a parent directory or a wildcard. If the
pod is unreachable, retry that exact cleanup once access returns.

The measured JSON was 66050 bytes. That proves a realm configuration
artifact was written and parsed; **restoration has not been tested**.
Keycloak's export guide does not guarantee consistency while a server is
running, and exports omit some server state. The full database dump is
the durable-state backup; even a user-inclusive realm export is not a
substitute for it.

## The Last Exam operator roles

The Last Exam's operators sign in through the confidential client
`tle-operator`. Each operator capability is a client role on it, named
exactly like the capability (for example `admin.auth.keys.rotate`), and
the composites `support`, `operator` and `admin` bundle them. The TLE
services read only `resource_access.tle-operator.roles`; realm roles grant
nothing there. Do not reuse the realm role `admin`, which is the homelab
administrator.

The TLE server repo publishes the list as its role manifest
(`deploy-info/role-manifest.json`). Until a hook reconciles it
automatically, this is a manual, rerunnable operator step:

1. Take a database backup using the atomic recipe above.
2. Regenerate the script from the release's manifest:
   `python3 platform/keycloak/tle-operator/generate.py <role-manifest.json>`.
3. Dry run, which is read-only and the default:
   `sg k3s-admin -c 'kubectl -n keycloak exec -i deploy/keycloak -- sh -s' < platform/keycloak/tle-operator/reconcile-roles.sh`
4. Apply with `sh -s apply`. The script re-reads Keycloak and exits 1 if any
   role or composite differs from the manifest.
5. Grant a composite to operators, preferably through a group, then give
   the TLE services `KEYCLOAK_OPERATOR_CLIENT_ID=tle-operator`.

The script runs inside the Keycloak pod with the bootstrap admin
credentials already in its environment, so the password never leaves the
pod or appears in arguments. It only adds client roles and composite
members; extras are reported and left in place. Its one deletion is the
unused realm roles `support` and `operator`, and only when they are not
composite, not assigned, and not in the default roles. Redirect URIs are
added when the TLE admin portal's sign-in lands.

## Files

| File | Purpose |
| --- | --- |
| `config/keycloak.yaml` | Deployment, application and management Service ports, probes, measured limit |
| `config/realm.yaml` | First-import realm structure, groups, scopes, MFA and Grafana client; no users |
| `config/vault-secrets.yaml` | Keycloak ServiceAccount, VaultConnection, VaultAuth and three VSO projections |
| `config/client-registration.yaml` | Portal client allowlist, reconciler and PostSync Job |
| `config/networkpolicy.yaml` | Namespace ingress deny and selected Keycloak peers/ports |
| `test_client_registration.py` | Client convergence, security and network-policy tests |
| `tle-operator/generate.py` | Builds `reconcile-roles.sh` from The Last Exam's role manifest |
| `tle-operator/reconcile-roles.sh` | Manual, rerunnable `tle-operator` client, role and composite reconciliation (generated) |
| `config/database-job.yaml` | Database credential wiring and Sync hook in `databases` |
| `../../environments/homelab/apps/keycloak.yaml` | Wave-24 Application |
| `../../infrastructure/ingress/config/keycloak-ingress.yaml` | Tailnet HTTPS entry point |
| `../../observability/monitoring/targets/keycloak.yaml` | Integration 8 on management port 9000 |
| `../../observability/monitoring/config/vault-secrets.yaml` | Grafana's client-secret delivery |
| `../databases/postgres/config/networkpolicy.yaml` | Namespace deny, PostgreSQL clients and exporter access |
| `../databases/redis/config/networkpolicy.yaml` | Redis clients and exporter access |

## Rollback

Revert the Grafana OIDC integration first if SSO needs abandoning; the
local admin form remains enabled (login acceptance is pending). Reverting
the Deployment prunes Keycloak but preserves PostgreSQL's database.
Reverting `realm.yaml` cannot remove
or overwrite an imported realm. To retire the whole Application, follow
the root README's removal procedure: add its cascade finalizer **before**
removing its file, or resources are orphaned. The database and Vault paths
survive and need deliberate, separate removal if truly unwanted.

Database policies belong to the databases, so retiring Keycloak leaves
the fence in place. If a policy regression drops `pg_up`/`redis_up` or
blocks a Sync hook, revert the responsible database-policy change and
hand the push to the operator; these failures can occur while Applications
remain Healthy.
