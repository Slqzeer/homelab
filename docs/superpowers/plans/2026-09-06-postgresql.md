# PostgreSQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persistent PostgreSQL whose password is generated inside Vault,
delivered by VSO, and never seen by a human — with a `pg_dump` procedure proven
by running it.

**Architecture:** A plain StatefulSet on `postgres:18.6-alpine` with a
local-path PVC. `configure-vault.sh` generates the password into Vault KV; VSO
materialises it into Secret `postgres-credentials`; the StatefulSet reads it as
`POSTGRES_PASSWORD`. No operator, no chart — the Bitnami chart renders an
unpinned `latest` tag, which on a database can change the major version across a
restart.

**Tech Stack:** k3s v1.36.4+k3s1, Argo CD 3.5.2, Vault 2.0.4, VSO 1.5.1,
PostgreSQL 18.6.

**Spec:** `docs/superpowers/specs/2026-09-06-postgresql-design.md`

## Global Constraints

- **Never run `git push`.** The user reserves all pushes. Commit locally, print
  the exact push command, and wait.
- **`kubectl` needs a group wrapper on this host:** `sg k3s-admin -c '...'`.
  Nested quoting inside it is fragile — for anything multi-line, write a script
  to the scratchpad and run that.
- **Commands the user must edit and run must be shell-safe as written.** Never
  use `<angle brackets>` as a placeholder; `<` is a shell redirection and has
  broken this user's shell twice. Keep such commands on one line.
- **No credential may become a command argument.** Use the `key=@<path>` form
  the unsealer and `configure-vault.sh` already use, so only a filename appears
  in `ps`.
- **The password is never printed, echoed, or pasted into the conversation.**
- **Image pinned to `postgres:18.6-alpine`.** No `latest`, ever, on a database.
- **Sync waves after this plan:** `argocd` -1, `namespaces` 0,
  `ingress-operator` 2, `vault` 10, `vso-operator` 21, `ingress-config` 21,
  `vso-config` 22, `postgres` 23.
- **A directory used as an Argo CD `path` may contain only valid manifests.**
- **Argo CD reporting Synced/Healthy proves only what it applied.** Every
  verification asserts on running state — `status.resources`, a live object, an
  actual query.
- Argo CD polls every 3 minutes. Poll for existence before `kubectl wait`.
- Scratchpad for temporary files:
  `/tmp/claude-1000/-srv-projects-homelab/c141665b-8004-4cff-a023-6b3a5f744b42/scratchpad`

---

### Task 1: The namespace and the Vault credential

**Files:**
- Modify: `bootstrap/namespaces/namespaces.yaml`
- Modify: `platform/vault/configure-vault.sh`

**Interfaces:**
- Produces: namespace `databases`; Vault KV path `homelab/postgres` holding
  `username` and `password`; policy `vso-postgres-read`; role `vso-postgres`
  bound to `system:serviceaccount:databases:postgres` with audience `vault`.
  Task 2's `VaultAuth` must name exactly that role and audience.

**Why the script is extended rather than duplicated.** `configure-vault.sh` is
Vault's one ceremony. A second script would mean two things to remember on a
rebuild, and the phase-17 spec placed it under `platform/vault/` precisely so
phase 18 could extend it.

- [ ] **Step 1: Read the existing script before touching it**

```bash
cat platform/vault/configure-vault.sh
free -h
sg k3s-admin -c 'kubectl get pods -A --no-headers | wc -l'
```

Note the script's shape: `set -eu`, POSIX `sh`, each guard written as the *test*
of an `if` so `set -e` does not abort on a non-zero `grep`. Match it exactly.

Record the `free -h` and pod-count output verbatim in your report — this is the
"before" half of the measurement the spec requires, and it must be captured
before the database exists.

- [ ] **Step 2: Add the namespace**

Append a fifth document to `bootstrap/namespaces/namespaces.yaml`, matching the
style of the existing four:

```yaml
---
# Stateful data services. PostgreSQL (phase 18) is the first.
apiVersion: v1
kind: Namespace
metadata:
  name: databases
```

- [ ] **Step 3: Extend the script**

Insert these three blocks into `platform/vault/configure-vault.sh`,
**immediately before** the final `echo "==> done"` line. Do not modify anything
above them:

```sh
echo "==> seeding homelab/postgres"
if vault kv get homelab/postgres >/dev/null 2>&1; then
  echo "    already present, leaving the credential alone"
else
  # Generated here and never displayed. It exists only in Vault and in the
  # Secret VSO derives from it -- no human sees or types it.
  #
  # Alphanumeric only: a / or @ inside a password breaks connection URLs in
  # ways that surface far from the cause.
  #
  # Written to a file so that only the FILENAME becomes an argument. A
  # password on a command line is visible in `ps`, which is the same reason
  # the unsealer reads its keys with key=@<path>.
  PWFILE=$(mktemp)
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 > "$PWFILE"
  vault kv put homelab/postgres username=postgres password=@"$PWFILE" >/dev/null
  rm -f "$PWFILE"
  echo "    generated"
fi

echo "==> policy vso-postgres-read"
# The data/ segment is REQUIRED and is not a typo -- see the note on
# vso-canary-read above.
vault policy write vso-postgres-read - <<'POLICY'
path "homelab/data/postgres" {
  capabilities = ["read"]
}
POLICY

echo "==> role vso-postgres"
# bound_service_account_names must match the ServiceAccount created in
# platform/databases/postgres/config/vault-secrets.yaml, and audience must
# match that file's VaultAuth spec.kubernetes.audiences.
vault write auth/kubernetes/role/vso-postgres \
    bound_service_account_names=postgres \
    bound_service_account_namespaces=databases \
    audience=vault \
    token_policies=vso-postgres-read \
    ttl=1h
```

- [ ] **Step 4: Verify the script still parses and the guards are right**

```bash
sh -n platform/vault/configure-vault.sh && echo "syntax OK"
grep -c 'homelab/data/postgres' platform/vault/configure-vault.sh
grep -n 'password=@' platform/vault/configure-vault.sh
grep -nE 'password=\$|password="\$' platform/vault/configure-vault.sh || echo "no password on a command line (correct)"
```

Expected: `syntax OK`; exactly `1` occurrence of the `data/` path; the
`password=@"$PWFILE"` form present; **no** match for a password expanded
directly into the argument list.

- [ ] **Step 5: Confirm `head -c 256` yields enough characters**

`tr -dc` discards roughly three quarters of random bytes, so a short input can
silently produce a short password. Prove the arithmetic rather than trusting it:

```bash
for i in 1 2 3 4 5; do
  head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 32 | wc -c
done
```

Expected: `32` every time. If any line is short, raise the input size and re-run
until five consecutive runs give 32.

- [ ] **Step 6: Commit**

```bash
git add bootstrap/namespaces/namespaces.yaml platform/vault/configure-vault.sh
git commit -m "Add the databases namespace and PostgreSQL's Vault credential

The password is generated inside the Vault pod and written to a file so
only the filename becomes an argument -- a password on a command line is
visible in ps. It is seeded only when absent, so a rebuild re-running the
ceremony never clobbers the credential a live database is using."
```

- [ ] **Step 7: Hand the push and the ceremony to the user**

Two things, and the ceremony must follow the push so the `databases` namespace
exists before Task 2 needs it. Print each on one line:

```bash
git push origin main
```

Then the ceremony, unchanged from phase 17 — it is the same script:

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
```
```bash
sg k3s-admin -c 'kubectl -n vault exec -i vault-0 -- sh' < platform/vault/configure-vault.sh
```
```bash
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'
```

Tell the user to expect the three new `==>` lines, and that the canary lines
should say "already present" — that is the idempotency guard working.

- [ ] **Step 8: Verify what can be verified without a token**

The ceremony's third command removes the token by design, so Vault's internals
cannot be read afterwards. Check the namespace instead, and defer the rest to
Task 3's end-to-end result:

```bash
sg k3s-admin -c 'kubectl get ns databases'
sg k3s-admin -c 'kubectl -n vault exec vault-0 -- ls -la /home/vault/.vault-token' 2>&1 | tail -1
```

Expected: the namespace exists; the token file is gone.

---

### Task 2: The manifests

**Files:**
- Create: `platform/databases/postgres/config/vault-secrets.yaml`
- Create: `platform/databases/postgres/config/postgres.yaml`
- Create: `environments/homelab/apps/postgres.yaml`
- Delete: `platform/databases/.gitkeep` (if present)

**Interfaces:**
- Consumes: role `vso-postgres`, audience `vault` (Task 1); namespace
  `databases`; the VSO CRDs installed at wave 21.
- Produces: Secret `postgres-credentials`, StatefulSet `postgres`, Service
  `postgres`, PVC `data-postgres-0`.

- [ ] **Step 1: Create the Vault objects**

All four must be in namespace `databases`, because `VaultAuth` requires its
ServiceAccount to reside in the consuming secret's namespace. Create
`platform/databases/postgres/config/vault-secrets.yaml`:

```yaml
# PostgreSQL's credential path: Vault KV -> VSO -> Secret.
#
# All four objects live in `databases` and that is a requirement, not a
# preference: the VaultAuth CRD states its ServiceAccount "must reside in the
# consuming secret's namespace". This namespace therefore gets its own
# ServiceAccount, VaultConnection, VaultAuth and VaultStaticSecret rather than
# reusing the ones in `vault`.
---
# The identity Vault trusts. Its name must match bound_service_account_names
# in platform/vault/configure-vault.sh.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: postgres
  namespace: databases
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: vault
  namespace: databases
spec:
  # Plain HTTP in-cluster: single node, so pod traffic never leaves the host.
  # skipTLSVerify is required by the CRD even when the address is http, where
  # it has no effect.
  address: http://vault.vault.svc:8200
  skipTLSVerify: false
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultAuth
metadata:
  name: postgres
  namespace: databases
spec:
  vaultConnectionRef: vault
  method: kubernetes
  mount: kubernetes
  kubernetes:
    # role and audiences must both match platform/vault/configure-vault.sh.
    # A mismatch in either is a permission denial that names neither side,
    # while Argo CD reports everything Synced.
    role: vso-postgres
    serviceAccount: postgres
    audiences:
      - vault
---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: postgres-credentials
  namespace: databases
spec:
  vaultAuthRef: postgres
  mount: homelab
  type: kv-v2
  path: postgres
  refreshAfter: 60s
  destination:
    name: postgres-credentials
    create: true
```

- [ ] **Step 2: Create the database**

Create `platform/databases/postgres/config/postgres.yaml`:

```yaml
# PostgreSQL 18.6 -- the cluster's first stateful workload.
#
# A plain StatefulSet rather than a chart. The Bitnami chart renders
# `bitnami/postgresql:latest`, and an unpinned tag on a database can change the
# MAJOR version across a restart; PostgreSQL refuses to start on a data
# directory written by a different major. See the design spec section 5.
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: databases
spec:
  # Headless: required for the StatefulSet's stable network identity. With one
  # replica, `postgres.databases.svc` resolves to the pod, which is all any
  # in-cluster client needs. There is no Ingress -- the Postgres wire protocol
  # is not HTTP and does not belong behind the tailnet proxy.
  clusterIP: None
  selector:
    app: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: databases
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      serviceAccountName: postgres
      securityContext:
        # Probed from the image on 2026-09-06: the postgres user is uid 70,
        # gid 70 -- NOT the 999 usually assumed -- and the entrypoint starts
        # as root and drops privileges itself. Setting the user explicitly
        # makes it skip the root path entirely; fsGroup makes the volume
        # writable, the mechanism proven on this cluster in phase 16.
        runAsNonRoot: true
        runAsUser: 70
        runAsGroup: 70
        fsGroup: 70
      containers:
        - name: postgres
          image: postgres:18.6-alpine
          args:
            # Both below the defaults. This host is also a workstation and a
            # game server; PostgreSQL's defaults assume a dedicated machine.
            - -c
            - shared_buffers=64MB
            - -c
            - max_connections=50
          env:
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgres-credentials
                  key: password
          ports:
            - name: postgres
              containerPort: 5432
          resources:
            requests:
              cpu: 50m
              memory: 192Mi
            limits:
              memory: 512Mi
          volumeMounts:
            # Mounted at /var/lib/postgresql, NOT at the data directory
            # itself. PostgreSQL 18's image puts PGDATA at
            # /var/lib/postgresql/18/docker, so this mount leaves PGDATA a
            # subdirectory -- which is what initdb requires, since it refuses
            # to initialise into a non-empty directory and a freshly
            # provisioned volume is not reliably empty.
            - name: data
              mountPath: /var/lib/postgresql
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres"]
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres"]
            initialDelaySeconds: 30
            periodSeconds: 20
            timeoutSeconds: 5
            # Deliberately tolerant. This host has a recorded history of probe
            # timeouts under memory pressure (troubleshooting entry 4, and the
            # VSO leader-election restart in entry 10). Killing a database
            # because the host was briefly busy is worse than waiting.
            failureThreshold: 6
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes:
          - ReadWriteOnce
        storageClassName: local-path
        resources:
          requests:
            # local-path does not enforce capacity -- a PV is a directory on
            # /srv, which has 852G free. Nominal, not a quota.
            storage: 10Gi
```

- [ ] **Step 3: Create the Application**

Create `environments/homelab/apps/postgres.yaml`:

```yaml
# PostgreSQL, the first stateful workload.
#
# Wave 23 -- after vso-config (22), because the StatefulSet cannot start
# without Secret postgres-credentials and VSO creates that at 22. On a rebuild
# the database therefore comes up last, after the Vault ceremony, which is
# correct: its credential does not exist until then.
#
# Nothing sits behind Postgres, so an unhealthy database gates nothing.
# Anything added later that does NOT depend on Postgres belongs at a wave
# below 23, not above it out of habit.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgres
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "23"
spec:
  project: default
  source:
    repoURL: git@github.com:Slqzeer/homelab.git
    targetRevision: main
    path: platform/databases/postgres/config
  destination:
    server: https://kubernetes.default.svc
    namespace: databases
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 4: Validate the manifests locally**

```bash
for f in platform/databases/postgres/config/*; do
  printf "%s -> " "$f"
  python3 -c "import yaml;print([d['kind'] for d in yaml.safe_load_all(open('$f')) if d])"
done
python3 -c "
import yaml
a=yaml.safe_load(open('environments/homelab/apps/postgres.yaml'))
print(a['kind'], a['metadata']['name'], a['metadata']['annotations'])
print('path:', a['spec']['source']['path'], '| dest ns:', a['spec']['destination']['namespace'])
"
```

Expected: `vault-secrets.yaml` gives
`['ServiceAccount', 'VaultConnection', 'VaultAuth', 'VaultStaticSecret']`;
`postgres.yaml` gives `['Service', 'StatefulSet']`; the Application is
`postgres` at the **quoted string** `'23'`, path
`platform/databases/postgres/config`, namespace `databases`.

- [ ] **Step 5: Validate against the live API server**

The CRDs and the `databases` namespace both exist by now, so this validates
without creating anything:

```bash
sg k3s-admin -c 'kubectl apply --dry-run=server -f platform/databases/postgres/config/vault-secrets.yaml'
sg k3s-admin -c 'kubectl apply --dry-run=server -f platform/databases/postgres/config/postgres.yaml'
```

Expected: every object reports `created (server dry run)`. A rejection here is
far cheaper than one after a push.

- [ ] **Step 6: Confirm both sides agree on role, ServiceAccount and audience**

```bash
echo "--- from the script ---"
grep -A5 'role vso-postgres' platform/vault/configure-vault.sh | grep -E 'bound_service_account|audience='
echo "--- from the manifest ---"
grep -E 'role:|serviceAccount:|- vault$' platform/databases/postgres/config/vault-secrets.yaml
```

Expected: role `vso-postgres`; ServiceAccount `postgres`; namespace
`databases`; audience `vault` on both sides.

- [ ] **Step 7: Commit**

```bash
git rm -q --ignore-unmatch platform/databases/.gitkeep
git add platform/databases/postgres environments/homelab/apps/postgres.yaml
git commit -m "Deploy PostgreSQL 18.6 at sync-wave 23

The PVC mounts at /var/lib/postgresql rather than at the data directory,
because PostgreSQL 18's image puts PGDATA at /var/lib/postgresql/18/docker
and initdb refuses a non-empty directory. Runs as uid 70, probed from the
image rather than assumed.

The Vault objects live here rather than with VSO because VaultAuth
requires its ServiceAccount in the consuming secret's namespace."
```

- [ ] **Step 8: Hand the push to the user and wait**

```bash
git push origin main
```

- [ ] **Step 9: Verify the Secret arrives before blaming the database**

Check the credential path first — if it fails, the pod's symptoms are a
consequence, not the cause:

```bash
for i in $(seq 1 60); do
  sg k3s-admin -c 'kubectl -n databases get secret postgres-credentials' >/dev/null 2>&1 && { echo "Secret arrived after ~$((i*5))s"; break; }
  sleep 5
done
sg k3s-admin -c 'kubectl -n databases get secret postgres-credentials'
sg k3s-admin -c 'kubectl -n databases get vaultstaticsecret postgres-credentials -o jsonpath={.status.conditions[*].reason}'; echo
```

Expected: the Secret exists; conditions read `Synced Healthy Ready`.

**If it does not appear**, read VSO's logs before touching anything. The two
most likely causes are a policy missing the `data/` segment and an audience
mismatch:

```bash
sg k3s-admin -c 'kubectl -n vault-secrets-operator-system logs deploy/vso-vault-secrets-operator-controller-manager --tail=40'
```

- [ ] **Step 10: Verify the database and what the Application manages**

```bash
for i in $(seq 1 60); do
  R=$(sg k3s-admin -c 'kubectl -n databases get pod postgres-0 -o jsonpath={.status.containerStatuses[0].ready}' 2>/dev/null)
  [ "$R" = "true" ] && { echo "postgres-0 Ready after ~$((i*5))s"; break; }
  sleep 5
done
sg k3s-admin -c 'kubectl -n databases get pods,pvc,svc'
sg k3s-admin -c 'kubectl get pv -o custom-columns=NAME:.metadata.name,CLAIM:.spec.claimRef.name,PATH:.spec.local.path'
sg k3s-admin -c 'kubectl -n argocd get application postgres -o jsonpath={.status.resources}' | tr ',' '\n' | grep -oE '"kind":"[A-Za-z]+"|"name":"[a-z0-9-]+"'
```

Expected: `postgres-0` 1/1 Ready; PVC `data-postgres-0` Bound with its
directory under `/srv/kubernetes/storage`; the Application's resource list
contains the Service, the StatefulSet and all four Vault objects.

**If `postgres-0` will not start**, check the logs for `initdb` complaining
about a non-empty directory — that would mean the mount path is wrong:

```bash
sg k3s-admin -c 'kubectl -n databases logs postgres-0 --tail=30'
```

---

### Task 3: Prove the credential path and that data survives

**Files:** none. This task only observes.

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.

**Why this task exists.** A running database proves it started. It does not
prove the password came from Vault, that a client can actually authenticate
with it, or that anything written survives the pod.

- [ ] **Step 1: Confirm the Secret matches Vault**

The database password is a real credential — **never print it**. Compare
fingerprints instead:

```bash
export SCRATCH=/tmp/claude-1000/-srv-projects-homelab/c141665b-8004-4cff-a023-6b3a5f744b42/scratchpad
sg k3s-admin -c 'kubectl -n databases get secret postgres-credentials -o jsonpath={.data.password}' | base64 -d | sha256sum | cut -c1-16
```

Record the first 16 hex characters. Ask the user to run this, which prints the
matching fingerprint from Vault itself, and to confirm the two agree:

```bash
sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- sh -c "vault login >/dev/null && vault kv get -field=password homelab/postgres | sha256sum"'
```

Expected: the first 16 characters match. A fingerprint proves equality without
either value reaching a terminal or a log.

- [ ] **Step 2: Authenticate from a SEPARATE pod**

Connecting from inside `postgres-0` would use the local socket and might never
check the password, proving nothing. Use a throwaway client pod that takes the
password from the Secret as an environment variable:

```bash
sg k3s-admin -c 'kubectl -n databases run pgclient --rm -i --restart=Never --timeout=180s --image=postgres:18.6-alpine --env=PGPASSWORD_FROM=unused --overrides="{\"spec\":{\"containers\":[{\"name\":\"pgclient\",\"image\":\"postgres:18.6-alpine\",\"command\":[\"psql\",\"-h\",\"postgres.databases.svc\",\"-U\",\"postgres\",\"-c\",\"select version();\"],\"env\":[{\"name\":\"PGPASSWORD\",\"valueFrom\":{\"secretKeyRef\":{\"name\":\"postgres-credentials\",\"key\":\"password\"}}}]}]}}"'
```

Expected: the PostgreSQL version string. This is the whole credential path
exercised end to end — Vault generated it, VSO delivered it, and the database
accepted it.

That command is long and quoting-sensitive. If it fights the `sg` wrapper, use
this Pod manifest instead — write it to `$SCRATCH/pgclient.yaml`, apply it, read
the logs, delete it. Record in the report which route was used.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pgclient
  namespace: databases
spec:
  restartPolicy: Never
  containers:
    - name: pgclient
      image: postgres:18.6-alpine
      command:
        - psql
        - -h
        - postgres.databases.svc
        - -U
        - postgres
        - -c
        - select version();
      env:
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-credentials
              key: password
```

```bash
sg k3s-admin -c "kubectl apply -f $SCRATCH/pgclient.yaml"
for i in $(seq 1 24); do
  P=$(sg k3s-admin -c 'kubectl -n databases get pod pgclient -o jsonpath={.status.phase}' 2>/dev/null)
  case "$P" in Succeeded|Failed) break;; esac
  sleep 5
done
sg k3s-admin -c 'kubectl -n databases logs pgclient'
sg k3s-admin -c 'kubectl -n databases delete pod pgclient --ignore-not-found'
```

Expected: the version string in the logs, and `Succeeded`. A `Failed` pod with
`password authentication failed` means the Secret and the database disagree —
which, if the database was initialised before the Secret existed, is the
divergence described in the design spec §10.

- [ ] **Step 3: Write data**

```bash
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- psql -U postgres -c "create table phase18 (id int primary key, note text); insert into phase18 values (1, '"'"'survives a pod deletion'"'"');"'
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- psql -U postgres -c "select * from phase18;"'
```

Expected: `CREATE TABLE`, `INSERT 0 1`, then the row.

- [ ] **Step 4: Delete the pod and read the row back**

This is the load-bearing check of the whole task:

```bash
sg k3s-admin -c 'kubectl -n databases delete pod postgres-0'
sleep 15
for i in $(seq 1 48); do
  R=$(sg k3s-admin -c 'kubectl -n databases get pod postgres-0 -o jsonpath={.status.containerStatuses[0].ready}' 2>/dev/null)
  [ "$R" = "true" ] && { echo "postgres-0 Ready again after ~$((15+i*5))s"; break; }
  sleep 5
done
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- psql -U postgres -c "select * from phase18;"'
sg k3s-admin -c 'kubectl -n databases get pvc data-postgres-0'
```

Expected: the row is still there and the PVC is Bound to the same volume. A
database that came back **empty** would mean the PVC is not persisting — report
that immediately, it would invalidate the storage conclusion from phase 16.

- [ ] **Step 5: Confirm no credential leaked**

```bash
sg k3s-admin -c 'kubectl -n databases logs postgres-0 --tail=100' | grep -ciE "password|PGPASSWORD" || echo "0 password mentions in logs (correct)"
sg k3s-admin -c 'kubectl get secrets -A -o json' | grep -ciE '"vault[_-]?token"|hvs\.' || echo "0 Vault tokens in any Secret (correct)"
```

Expected: no password material in the database's logs; still zero Vault tokens
anywhere in the cluster.

- [ ] **Step 6: Prove the ceremony is idempotent against a live credential**

This is the check that matters most for a rebuild: re-running
`configure-vault.sh` must **not** replace the password a running database is
already using. Ask the user to run the three ceremony commands again, then:

```bash
sg k3s-admin -c 'kubectl -n databases get secret postgres-credentials -o jsonpath={.data.password}' | base64 -d | sha256sum | cut -c1-16
```

Expected: **identical** to the fingerprint recorded in Step 1. A changed
fingerprint means the seed guard failed, the Vault value was replaced, and the
running database now disagrees with its own Secret — report it immediately.

Then confirm the database still accepts the credential by repeating Step 2.

- [ ] **Step 7: Record memory**

```bash
free -h | head -2
sg k3s-admin -c 'kubectl top pod -n databases'
sg k3s-admin -c 'kubectl get pods -A --no-headers | wc -l'
```

Report the measured figures against the "before" recorded in Task 1 Step 1.
Task 5 documents them; they are never estimated.

---

### Task 4: Backups

**Files:** none yet — this task establishes the procedure that Task 5 documents.

**Interfaces:**
- Consumes: a running database containing the `phase18` table from Task 3.

- [ ] **Step 1: Establish whether a local dump needs a password**

The official image's `pg_hba.conf` decides this, and it must be checked rather
than assumed:

```bash
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- sh -c "grep -vE \"^#|^$\" \$PGDATA/pg_hba.conf"'
```

Record the output. If `local all all trust` is present, `pg_dumpall` over the
local socket needs no password. If it is `scram-sha-256`, the dump command must
take `PGPASSWORD` from the Secret and Task 5 must document that instead.

- [ ] **Step 2: Take a dump**

```bash
mkdir -p /backups/databases/postgresql
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- pg_dumpall -U postgres' | gzip > /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz
ls -la /backups/databases/postgresql/
```

`/backups` is mode 0777 on its own ext4 disk, so no `sudo` is needed — the same
finding that simplified phase 16's snapshot procedure.

Expected: a non-empty `.sql.gz`. Record its exact size.

- [ ] **Step 3: Prove the dump contains the data — do not just check it exists**

```bash
zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -c "CREATE TABLE public.phase18"
zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -A3 "COPY public.phase18"
zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | wc -l
```

Expected: the table definition appears, and the row `1 survives a pod deletion`
is present in the `COPY` block. **A backup whose contents were never inspected
is not a verified backup** — this repository already records one documented
verification method that could never have worked.

- [ ] **Step 4: Confirm no credential is in the dump**

`pg_dumpall` emits role definitions, which can include password hashes:

```bash
zcat /backups/databases/postgresql/all-$(date +%Y%m%d).sql.gz | grep -icE "PASSWORD 'SCRAM|PASSWORD 'md5" || echo "0 password hashes in the dump"
```

Record the result honestly either way. If hashes are present, Task 5 must say
so plainly — it changes how the backup file should be treated, and
`/backups` is mode 0777.

---

### Task 5: Documentation

**Files:**
- Create: `platform/databases/postgres/README.md`
- Modify: `README.md`
- Modify: `docs/troubleshooting.md`
- Modify: `platform/vault-secrets-operator/README.md`

**Interfaces:**
- Consumes: the measured figures and observed behaviour from Tasks 3 and 4.

- [ ] **Step 1: Write the component README**

Create `platform/databases/postgres/README.md` covering:

- What it is: PostgreSQL 18.6, one StatefulSet, one PVC, no operator, in-cluster
  only. State why there is no chart — the Bitnami chart renders `latest`, and on
  a database that can change the major version across a restart.
- **How to connect**, with a working command that takes the password from the
  Secret and never prints it.
- **The rotation limitation, stated plainly.** `POSTGRES_PASSWORD` is read only
  at `initdb`; changing the value in Vault updates the Secret but **not** the
  database, and they diverge silently. Real rotation needs an `ALTER USER`.
  This is the single most important thing in the file.
- **Backups**: the verified command from Task 4, its measured size, and whether
  a password is required (Task 4 Step 1). Restate the roadmap's warning: never
  copy a live data directory — a `pg_dump` is consistent, a filesystem copy of a
  running database is not, and the failure appears at restore time.
- **Restore**, from HashiCorp's and PostgreSQL's own documentation. Label it
  **not exercised**, because testing it means destroying the live database.
- The three strings that must agree across `configure-vault.sh` and
  `vault-secrets.yaml`: role `vso-postgres`, ServiceAccount `postgres`,
  audience `vault`.
- Measured resource usage from Task 3 Step 6, dated.

- [ ] **Step 2: Update the root README**

- Add `platform/databases/postgres/` to the Layout table.
- Add wave 23 to the ordering note.
- In "First install / rebuild": the ceremony now also seeds PostgreSQL's
  credential. Note that a rebuild which unseals but skips `configure-vault.sh`
  leaves both `vso-config` (22) and `postgres` (23) unhealthy — and that,
  because both sit above ingress at 21, no tailnet URL is affected.

- [ ] **Step 3: Add troubleshooting entry 11**

Append a numbered entry, `PostgreSQL will not start, or rejects the password`,
leading with the symptom. Cover, in this order:

1. **Is the Secret there?** `kubectl -n databases get secret postgres-credentials`. If not, this is a
   Vault path problem, not a database problem — go to entry 10.
2. **`initdb` refusing a non-empty directory.** The symptom is a pod that
   crash-loops with `directory "..." exists but is not empty`. The cause is
   mounting the PVC at the data directory instead of at
   `/var/lib/postgresql`. Note that PostgreSQL 18 put `PGDATA` at
   `/var/lib/postgresql/18/docker`, which is not where older guides say.
3. **The password works but a rotation did not take.** Explain §10's divergence:
   the Secret changed, the database did not. This will look like a broken
   credential and is not.
4. **A permission error on the data directory** — check `fsGroup: 70` and the
   uid the image runs as, and cross-reference entry 1.
5. Close with the standing point: a Synced Application says what Argo CD
   applied, never what is running.

- [ ] **Step 4: Add PostgreSQL to the VSO README's consumer list**

`platform/vault-secrets-operator/README.md` currently describes the canary as
the only thing exercising the path. There are now two consumers. Add a short
section listing them and their namespaces, and note that each namespace needs
its own ServiceAccount, `VaultConnection` and `VaultAuth`, because `VaultAuth`
requires the ServiceAccount in the consuming secret's namespace.

- [ ] **Step 5: Verify every documented command**

Run each command quoted in the new documentation, or confirm its syntax where
running it would be destructive or need a Vault login. Report which were
executed and which were only syntax-checked.

Confirm no `<angle bracket>` placeholder appears in any command a reader is
expected to type, and that no command prints the database password.

- [ ] **Step 6: Commit**

```bash
git add platform/databases/postgres/README.md README.md docs/troubleshooting.md platform/vault-secrets-operator/README.md
git commit -m "Document PostgreSQL: connecting, backups, and what static credentials cannot do

Records that POSTGRES_PASSWORD is read only at initdb, so rotating the
value in Vault updates the Secret but not the database and the two
diverge silently. That is the cost of static credentials and it belongs
in the operational doc, not only in the spec."
```

- [ ] **Step 7: Hand the push to the user**

```bash
git push origin main
```

- [ ] **Step 8: Final state check**

```bash
git status --short
sg k3s-admin -c 'kubectl -n argocd get applications -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
sg k3s-admin -c 'kubectl -n databases get pods,pvc'
sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- psql -U postgres -c "select count(*) from phase18;"'
printf "argocd ingress uid (baseline 8b33ddee-4c43-4dda-a1da-c5c28d7106ca): "
sg k3s-admin -c 'kubectl -n argocd get ingress argocd -o jsonpath={.metadata.uid}'; echo
curl -sS  --max-time 45 -o /dev/null -w 'argocd -> %{http_code} tls_verify=%{ssl_verify_result}\n' https://argocd.taildf6cd4.ts.net
curl -sSL --max-time 45 -o /dev/null -w 'vault  -> %{http_code} tls_verify=%{ssl_verify_result}\n' https://vault.taildf6cd4.ts.net
free -h | head -2
```

Expected: clean tree; 9 Applications Synced/Healthy; `postgres-0` 1/1; the test
row still readable; the Argo CD Ingress uid **unchanged**; both URLs 200 with
`tls_verify=0`.
