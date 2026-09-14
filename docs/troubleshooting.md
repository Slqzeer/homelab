# Troubleshooting

Faults actually hit on this cluster, with the diagnosis that worked. Each
entry leads with the symptom, because that is what you will have.

---

## 1. Non-root containers fail to start: "permission denied" on their own binary

**The most important entry in this file.** It cost three days and broke two
Argo CD installations before it was understood.

### Symptom

A pod never starts. `kubectl describe pod` shows:

```text
Error: failed to create containerd task: failed to create shim task:
OCI runtime create failed: runc create failed: unable to start container
process: error during container init: exec: "/usr/local/bin/argocd":
stat /usr/local/bin/argocd: permission denied
```

Pod state is `StartError` / `RunContainerError` / `CreateContainerConfigError`,
`exitCode` 128 or 127, `startedAt` at the Unix epoch.

The tell: **root containers work fine, non-root containers do not.** CoreDNS,
Traefik, and anything running as UID 0 look healthy. Argo CD (UID 999), Vault,
PostgreSQL, and Prometheus do not.

### Cause

`/srv/kubernetes` carried an ACL with a `default:` (inheritable) entry:

```text
other::---
default:other::---
```

A `default:` ACL is inherited by **every directory created beneath it**. The
k3s data-dir lives at `/srv/kubernetes/data`, so when containerd extracted
image layers into its snapshotter tree, the snapshot root directory was
created `0750` instead of `0755`, with `other::---`.

After `pivot_root`, that snapshot directory **is the container's `/`**. A
process running as a non-root UID cannot traverse it, so it cannot reach any
file in its own filesystem — including its entrypoint. Directories *inside*
the image are fine (they come from the image tar with explicit modes); only the
root, created by containerd, is poisoned.

This does not happen with the default data-dir (`/var/lib/rancher/k3s`,
mode 0755). Moving the data-dir to `/srv` introduced it.

### Diagnose

```bash
# 1. Does a root container work but a non-root one fail?
kubectl run rootcheck --image=busybox:1.36 --restart=Never --rm -i \
  --command -- echo OK
# then the same with securityContext.runAsUser: 999 in a manifest

# 2. Inspect the ACL on the data-dir ancestry
getfacl -p /srv/kubernetes

# 3. Look at a snapshot root (needs root)
sudo sh -c 'd=$(find /srv/kubernetes/data/agent/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots \
  -mindepth 2 -maxdepth 2 -name fs -type d | head -1); \
  echo "$d"; getfacl -p "$d"'
```

A snapshot root at `750` with `other::---`, while `usr/` inside it is `755`,
confirms it.

### Fix

Strip only the **inheritable** ACL and repair the snapshot roots already
created wrong. Ancestor permissions are irrelevant after `pivot_root`, so
`/srv/kubernetes` keeps its `2770 root:k3s-admin` and its access ACL — the
security intent survives; only the inheritance has to go. The setgid bit
continues to provide group inheritance.

```bash
sudo systemctl stop k3s
sudo setfacl -R -k /srv/kubernetes
sudo find /srv/kubernetes/data/agent/containerd -maxdepth 4 -type d -name fs \
  -exec chmod 0755 {} +
sudo systemctl start k3s
```

Verify with a freshly pulled image (not one already on the node), running as a
non-root UID — that proves new pulls are no longer poisoned, rather than that
existing ones were patched:

```bash
kubectl run freshcheck --image=alpine:3.20 --restart=Never --rm -i \
  --overrides='{"spec":{"securityContext":{"runAsUser":999,"runAsGroup":999}}}' \
  --command -- id
```

### The follow-up that is easy to miss

**Fixing the on-disk state does not fix running pods.** A pod whose container
was created before the repair keeps its poisoned rootfs until the pod is
recreated. After applying the fix, delete any pod still failing:

```bash
kubectl delete pod -n <namespace> <pod>       # Deployments/StatefulSets recreate it
```

On this cluster CoreDNS needed a manual delete; Traefik had accumulated 706
restarts and recovered on its own.

### Where else this bites

PersistentVolume directories under `/srv/kubernetes/storage` inherited the same
ACL. Vault, PostgreSQL, Prometheus and Grafana all run non-root and mount PVCs,
so the same fault would have resurfaced at plan phases 16, 18, 22 and 23.

---

## 2. Cluster DNS dead: CoreDNS in CrashLoopBackOff

### Symptom

```text
plugin/forward: failed to resolve "/etc/resolv.conf":
lookup /etc/resolv.conf: no such host
```

CoreDNS restarts endlessly. Everything that resolves a name breaks — including
Argo CD's repo-server reaching `github.com`, so Applications sit at
`Unknown` with no obvious explanation.

### Cause

Not a DNS misconfiguration, despite appearances. CoreDNS's Corefile has
`forward . /etc/resolv.conf`; when the forward plugin cannot **stat** that path
it falls back to treating the string as a hostname, producing the misleading
"no such host".

On this cluster the file was present and correct — the container simply could
not read it, because of fault #1 above. CoreDNS's image snapshot predated the
ACL repair.

### Rule out the obvious first

Confirm the kubelet is actually producing a good `resolv.conf` before touching
any host DNS config:

```bash
kubectl run dnsprobe --image=busybox:1.36 --restart=Never --rm -i \
  --overrides='{"spec":{"dnsPolicy":"Default"}}' \
  --command -- cat /etc/resolv.conf
```

If that returns sensible nameservers, the kubelet is fine and the problem is
inside the CoreDNS container.

### Fix

```bash
kubectl -n kube-system delete pod -l k8s-app=kube-dns
```

Then verify both external and internal resolution:

```bash
kubectl run dnstest --image=busybox:1.36 --restart=Never --rm -i \
  --command -- sh -c 'nslookup github.com; nslookup kubernetes.default.svc.cluster.local'
```

---

## 3. `kubectl` fails with "permission denied" reading the kubeconfig

### Symptom

```text
error: error loading config file "/etc/rancher/k3s/k3s.yaml":
open /etc/rancher/k3s/k3s.yaml: permission denied
```

...even though `getent group k3s-admin` lists your user and the file is
`0640 root:k3s-admin`.

### Cause

Supplementary groups are fixed at **login**. If the `k3s-admin` membership was
granted after your current session started, the session's process credentials
predate it. `getent` reads the group database; `id` reads your process.

```bash
getent group k3s-admin   # shows you as a member
id                       # does NOT list k3s-admin  <- the discrepancy
```

### Fix

Log out and back in. That is the only real fix.

Within an existing session, `sg` is the workaround:

```bash
sg k3s-admin -c 'kubectl get nodes'
```

Note that nested quoting inside `sg ... -c '...'` is fragile; for anything
multi-line, write a script to a file and run that instead.

---

## 4. `argocd-repo-server` CrashLoopBackOff on a loaded host

### Symptom

Pod restarts repeatedly. `lastState.terminated` shows `exitCode: 0`,
`reason: "Completed"` — a graceful SIGTERM, **not** an OOM kill. Events show:

```text
Container repo-server failed liveness probe, will be restarted
Liveness probe failed: /healthz?full=true: context deadline exceeded
```

### Cause

The process is alive but too slow to answer its health probe. This node is
simultaneously a workstation, a homelab and a game host; under memory pressure
the upstream probe defaults (`timeoutSeconds: 1`, `failureThreshold: 3`) kill a
perfectly healthy pod.

Distinguish from a real OOM: an OOM kill reports `reason: OOMKilled` and
`exitCode: 137`. `exitCode: 0` with `Completed` means the kubelet asked it to
stop.

### Fix

Already applied in `bootstrap/argocd/values.yaml` — the repoServer probes are
widened there, with the invariant documented (readiness must trip before
liveness). If it recurs, check host memory first:

```bash
free -h
ps -eo pmem,rss,comm --sort=-rss | head -6
kubectl top node
```

---

## 5. Argo CD has not noticed a pushed commit

### Symptom

You pushed, but the Application still reports the old revision, or a newly
committed child Application has not appeared.

### Cause

Usually nothing is wrong. Argo CD's default reconciliation interval is
**3 minutes**; it polls rather than receiving webhooks.

```bash
kubectl -n argocd get application <name> \
  -o jsonpath='{.status.sync.revision}{"\n"}'
```

Compare against `git rev-parse HEAD`.

### Fix

Wait, or force a poll:

```bash
kubectl -n argocd patch application <name> --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
```

A hard refresh forces a re-read of the repository. It is **not** a manual
apply, so it does not undermine the app-of-apps model.

If it still does not appear, the credential is the next suspect — see below.

---

## 6. Every Application stops reconciling at once

### Symptom

All Applications go `ComparisonError` / `Unknown` simultaneously. Repo-server
logs show `Permission denied (publickey)`.

### Cause

The `repo-homelab` Secret in the `argocd` namespace holds the read-only GitHub
deploy key every Application clones with. If that key is revoked, expires, or
the Secret is lost, the whole loop stops — including Argo CD's management of
itself.

This Secret exists **only in the cluster** and is reproducible from nothing in
this repository. Treat it as backup-worthy state permanently, not until some
future migration — it can never move into Vault; see below for why.

### Diagnose

```bash
# Is the key still accepted by GitHub?
ssh -i ~/.ssh/argocd_homelab_deploy -o IdentitiesOnly=yes -T git@github.com
# Expect: "Hi Slqzeer/homelab! You've successfully authenticated"
# A repo-scoped greeting confirms a correctly registered deploy key.
```

Note that `ssh` silently refuses a key whose file permissions are too open,
reporting `UNPROTECTED PRIVATE KEY FILE` rather than an auth failure — and
`~/.ssh` on this machine carries ACLs that can make a newly generated key
group-readable. If in doubt:

```bash
chmod 600 ~/.ssh/argocd_homelab_deploy
setfacl -b ~/.ssh/argocd_homelab_deploy
```

### Note on verification

Do **not** try to test the credential by exec'ing into repo-server and running
`git ls-remote`. Argo CD injects repository credentials through its own repo
layer; it does not mount the key as the pod's SSH identity, and the pod has no
`~/.ssh` at all. That check fails regardless of whether the Secret is correct.
Test from the host with `ssh -i` as above.

### The second cluster-only secret

`repo-homelab` is no longer the only one. `operator-oauth` in the
`tailscale` namespace holds the OAuth client the Tailscale operator uses,
and it exists in no repository either.

| Secret | Namespace | Loss impact |
| --- | --- | --- |
| `repo-homelab` | `argocd` | All Argo CD reconciliation stops |
| `operator-oauth` | `tailscale` | All tailnet ingress stops; every `.ts.net` URL dies |
| `vault-unseal-keys` | `vault` | Vault stops auto-unsealing; recoverable by hand from the password manager |

`vault-unseal-keys` is the only one of the three that is reproducible,
because the operator holds a copy of the keys in a password manager.
`repo-homelab` can never move into Vault — Argo CD needs it to clone this
repository, which is how Vault itself gets deployed; a credential required
to deploy the secret store cannot live inside it. `operator-oauth` can
**never** migrate either, for a different reason: the Tailscale operator
mounts it at sync-wave 2, and Vault does not exist until wave 10 — wave 2
gates wave 10, so a rebuild would wait forever for a secret that itself
requires Vault to be up. This corrects an earlier claim in the phase-16
design spec that landing the Vault Secrets Operator (phase 17) would be
enough; it is not. See
`docs/superpowers/specs/2026-09-05-vault-secrets-operator-design.md` §10.

Losing `operator-oauth` does **not** stop reconciliation — Argo CD keeps
working, and `kubectl port-forward` still reaches it. Symptoms are
hostnames that stop resolving and proxy pods that fail to authenticate:

```bash
kubectl -n tailscale logs deploy/operator --tail=50
```

All three are backup-worthy. Phase 16 landed without either
`repo-homelab` or `operator-oauth` moving into Vault — see above for why.

---

## 7. Argo CD itself is broken by a bad commit

Argo CD manages its own release, so a bad commit to
`bootstrap/argocd/values.yaml` can break the controller that would otherwise
reconcile the fix.

See the **Recovery** section of the repository `README.md`. The order matters:
revert the bad commit and **push** first, then run
`./bootstrap/argocd/bootstrap.sh` — the script reads `values.yaml` from the
working tree, and the `argocd` Application still targets `main` with
`selfHeal`, so a restored Argo CD will re-apply whatever `main` holds.

---

## 8. Tailnet ingress: state the Tailscale operator creates but Argo CD can't see

This is the entry that cost the most time getting tailnet ingress working.
A shared `ProxyGroup` was tried first and abandoned (see the design spec
§10 and `infrastructure/ingress/README.md`) in favor of a dedicated proxy
per Ingress via `tailscale.com/proxy-class`. Switching designs mid-stream
left cluster state behind that Argo CD never created and so never prunes,
and each leftover independently blocked the replacement from coming up.

### Start here

The three faults below are specific to migrating away from a ProxyGroup
and **cannot occur on a clean rebuild** — none of their artifacts exist
until a ProxyGroup has run at least once. If ingress will not come up on a
fresh install, work through this checklist first, in order:

1. **Operator pod stuck `ContainerCreating`** — the `operator-oauth`
   Secret is missing, or its keys are misnamed. They must be exactly
   `client_id` and `client_secret`; anything else and the chart's env vars
   (`CLIENT_ID_FILE` / `CLIENT_SECRET_FILE`) point at files that don't
   exist. See `infrastructure/ingress/README.md`.
2. **Ingress never gets a hostname, or gets the wrong one** — check
   `spec.tls[0].hosts[0]` on the Ingress. It must be a **short** name
   (`argocd`), not the full FQDN — the operator appends the MagicDNS
   suffix itself, so a full FQDN there produces a wrong or malformed
   hostname.
3. **Is the proxy pod even running?**

   ```bash
   sg k3s-admin -c 'kubectl -n tailscale get pods'
   ```

   No dedicated proxy pod for the Ingress means nothing further downstream
   (certificate, routing) can work yet — fix this before looking at
   anything else.
4. **API permission error in the operator log** — an error naming a
   missing permission or scope means the OAuth client is under-scoped.
   Check its scopes against `infrastructure/ingress/README.md` (exactly
   `services`, `devices:core`, `auth_keys`, all read+write).
5. **Are HTTPS certificates still enabled on the tailnet?**

   ```bash
   tailscale status --json | jq .CertDomains
   ```

   `null` means HTTPS Certificates is disabled tailnet-wide and no
   certificate will ever issue, no matter how correct everything else is.
   Enable it: admin console → DNS → HTTPS Certificates.

### Faults specific to migrating away from a ProxyGroup

The three faults below were all hit while switching this cluster's live
Ingress off a shared `ProxyGroup` and onto a dedicated
`tailscale.com/proxy-class` proxy. Every one of them requires a ProxyGroup
(or ProxyGroup-backed Ingress) to have existed at some point to leave the
state behind — **none of them can happen on a fresh install**, where no
ProxyGroup is ever created. They are kept here in case this design is
revisited and a similar migration happens again.

### An Ingress never gets an ADDRESS: "input does not match format"

**Symptom**

```bash
kubectl -n <ns> get ingress <name>
# ADDRESS column stays empty
```

```bash
kubectl -n tailscale logs deploy/operator --tail=50
# "failed to provision: failed to create or get API key secret: input does not match format"
# "tls: failed to find any PEM data in certificate input" at the secret's creation timestamp
```

**Cause**

An orphaned Secret of type `kubernetes.io/tls`, named after the hostname,
left behind by a previous proxy (here, one created by the abandoned
ProxyGroup), with **empty** `tls.crt` and `tls.key`. The operator tries to
reuse that Secret and fails validation before it ever reaches Let's
Encrypt — the "input does not match format" and the PEM error are the
same underlying empty-data problem logged from two different code paths.

**Fix**

Confirm the Secret's data is actually empty, then delete it:

```bash
kubectl -n <ns> get secret <hostname> -o jsonpath='{.data.tls\.crt}' | wc -c
# 0 confirms it is empty
kubectl -n <ns> delete secret <hostname>
```

The operator provisions a fresh Secret and certificate within a minute.

### An Ingress stuck `Terminating` forever

**Symptom:** `kubectl delete ingress <name>` never completes; the object
stays `Terminating` indefinitely. `metadata.finalizers` still lists
`tailscale.com/ingress-pg-finalizer` — the finalizer a ProxyGroup-backed
Ingress carries.

**Cause:** after switching the Ingress away from a ProxyGroup, no
controller owns that finalizer any more, so nothing ever clears it.

**Fix:** verify no proxy StatefulSet or Secret remains for that Ingress
(its own cleanup already ran), then clear the finalizer with a merge
patch:

```bash
kubectl -n <ns> patch ingress <name> --type merge -p '{"metadata":{"finalizers":[]}}'
```

### A hostname comes up as `<name>-1` instead of `<name>`

**Symptom:** the Ingress provisions successfully, but the hostname is
`<name>-1.taildf6cd4.ts.net`, not the expected `<name>.taildf6cd4.ts.net`.

**Cause:** a stale Tailscale Service or device from an earlier attempt
still holds the plain name in the tailnet's device list, so the operator
is forced to disambiguate.

**Fix:** delete the stale device or Service in the Tailscale admin
console (<https://login.tailscale.com/admin/machines>), then delete the
Ingress so the operator re-provisions and claims the now-freed name.

### The lesson

Argo CD prunes only what it manages. Secrets and finalizers the Tailscale
operator creates directly against the Kubernetes API are invisible to
Argo CD and survive a design change untouched — long enough to poison
whatever is meant to replace them.

---

## 9. Vault is sealed, or will not come back after a restart

### Symptom

`vault-0` is Running but never becomes Ready, and

    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- vault status'

reports `Sealed true`. Anything depending on Vault fails.

### A sealed Vault is normal at two moments

Immediately after deployment and before the init ceremony, and for a few
seconds after any pod restart. Only a Vault that stays sealed is a fault.

### Three faults hit during phase 16, each with a green Argo CD and a broken cluster

`Synced`/`Healthy` on the `vault` Application was **not** evidence any of
these were fine. Every one of them looked correct from Argo CD's side while
Vault stayed sealed.

**1. Keys present and correct, helper running, still sealed — the unseal
command itself was wrong.** The helper's own logging swallows the real
error with `>/dev/null 2>&1`, so its logs say only "still sealed" no matter
what actually failed. Diagnose by running one unseal by hand and reading
the API's answer directly:

    sg k3s-admin -c 'kubectl -n vault exec deploy/vault-unsealer -- sh -c "VAULT_ADDR=http://vault-0.vault-internal:8200 vault write -format=json sys/unseal key=@/unseal/key1"'

`'key' must be a valid hex or base64 string` means the key argument itself
is malformed — including the case where a literal `-` was sent as the key,
which is what `vault operator unseal - < "$k"` actually does. The Vault CLI
has no `-` stdin convention; that form is rejected outright, and piping to
the no-argument form fails differently again, with
`file descriptor 0 is not a terminal`. The mechanism that works is
`vault write -format=json sys/unseal key=@"$k"` — see
`platform/vault/README.md` and `platform/vault/config/unsealer.yaml` for why.

**2. A corrected script that never runs.** The unsealer's script was fixed
in git, Argo CD showed Synced, and Vault stayed sealed anyway, because the
script originally lived in a ConfigMap. Editing a ConfigMap does not roll
the pods that mount it, so the running pod kept executing the old script
from memory indefinitely. Check the pod's age against when the fix was
pushed:

    sg k3s-admin -c 'kubectl -n vault get pods -l app=vault-unsealer'

A pod older than the fix is running stale code no matter what git or Argo
CD say. This is why the script now lives inline in the pod template
(`config/unsealer.yaml`): any change to it changes the pod template, which
rolls the Deployment the way every other change does.

**3. A manifest in git that nothing applies.** The unsealer manifest sat in
`platform/vault/` with no Application source pointing at it, so it was
never applied at all — not missing, not failing, simply never submitted to
the API server — while the `vault` Application still reported
Synced/Healthy, because Argo CD only reports on what it was told to manage.
Confirm the object you expect is actually one Argo CD knows about:

    sg k3s-admin -c "kubectl -n argocd get application vault -o jsonpath='{.status.resources}'"

If the resource you expect is missing from that list, Argo CD was never
told to look at it, regardless of whether the file exists in git.

**Synced/Healthy means Argo CD applied everything it knows about. It says
nothing about a file it was never told to look at.** All three faults above
are instances of that one fact, and it is the most transferable thing this
phase produced — checking Argo CD's status is not the same as checking that
the cluster does what the repository claims.

### Other checks, if the three above don't match

**Is the Secret present with the right key names?**

    sg k3s-admin -c 'kubectl -n vault get secret vault-unseal-keys -o go-template="{{range \$k, \$v := .data}}{{\$k}}{{\"\n\"}}{{end}}"'

Must print exactly `key1`, `key2`, `key3`, one per line. The helper loops
over those literal paths; any other name means it silently never unseals.
The Secret mount is `optional: true`, so the helper runs happily without it
at all — a running helper proves nothing about whether it can unseal.

Do not use `-o jsonpath={.data}` piped through `grep` to pull out the key
names: that emits key names *and* the base64-encoded key values in the
same stream, distinguished only by a character-class pattern, so a value
that happened to look like a bare word would print alongside the names.
The `go-template` form above ranges over the map and prints only the keys.

**Is Vault initialised at all?** `Initialized false` in `vault status`
means the init ceremony never ran. See `platform/vault/README.md`. Do not
run `vault operator init` against a Vault that already holds data.

**Did the PVC survive?**

    sg k3s-admin -c 'kubectl -n vault get pvc data-vault-0'

A Vault that returns *uninitialized* after a restart has lost its storage —
a much more serious fault than a seal problem, meaning the PV is not
persisting.

### The way back in without the helper

Manual unseal always works with the keys from your password manager, using
the TTY prompt a human has and the helper's Deployment does not:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault operator unseal'

Run it three times, once per key.

---

## 10. Secret from Vault is missing or stale

### Symptom

`kubectl -n vault get secret vault-canary` returns nothing, or the value it
holds no longer matches what is in Vault.

### Start here: is the Secret there at all?

    sg k3s-admin -c 'kubectl -n vault get secret vault-canary'

If it exists, check its age against when you last changed the Vault value —
`refreshAfter` is `60s`, so anything older than a minute or two past your
change is stale, not merely slow.

### What does VSO say?

Read its logs before changing anything — the failure is usually named
there, not guessed at:

    sg k3s-admin -c 'kubectl -n vault-secrets-operator-system logs deploy/vso-vault-secrets-operator-controller-manager -c manager --tail=50'

### Likely causes, in order

**1. The KV v2 `data/` segment, missing from the policy.** This is the most
common mistake made with KV v2, and it produces a permission denial that
reads exactly like a wrong path. KV v2 stores values one level below the
mount, so a policy written against `homelab/canary` matches nothing; it
must be `homelab/data/canary`. Check the live policy:

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault policy read vso-canary-read'
    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'

**2. Audience, role, or ServiceAccount disagree between the script and the
manifest.** Three strings — role `vso-canary`, ServiceAccount
`vault-canary`, audience `vault` — must match exactly between
`platform/vault/configure-vault.sh` and
`platform/vault-secrets-operator/config/vault-secrets.yaml`. A mismatch in
any one of them is a permission denial naming neither side. Compare
mechanically rather than by eye, run from the repository root (both paths
are relative to it):

    grep -E 'bound_service_account_names|bound_service_account_namespaces|audience=|auth/kubernetes/role/' platform/vault/configure-vault.sh
    grep -E 'role:|serviceAccount:|- vault$|name: vault-canary' platform/vault-secrets-operator/config/vault-secrets.yaml

Expected: role `vso-canary` on both sides; ServiceAccount `vault-canary`;
namespace `vault`; audience `vault`.

**3. The VSO manager restarted under host memory pressure — not a
misconfiguration at all.** Observed once on this cluster, about 8 hours
after install:

    restartCount=1, reason=Error, exitCode=1     (NOT OOMKilled / 137)

with, in the previous container's logs:

    Error retrieving lease lock ... context deadline exceeded
    Failed to renew lease
    problem running manager: leader election lost

A slow API server call timed out, the manager lost leader election, and
restarted cleanly — the same family as entry 4
(`argocd-repo-server` failing its liveness probe on a loaded host), and
told apart from a real memory problem the same way: `exitCode 1` with
lease-renewal messages means API server latency from host contention,
nothing to tune in the component; `OOMKilled` / `exitCode 137` means a
genuine memory limit, and then the limits in
`platform/vault-secrets-operator/values.yaml` are the thing to raise.
Measured usage here is 36Mi against a 96Mi limit, so trimmed limits were
**not** the cause of the observed restart — don't raise them reflexively
on the strength of this fault alone.

**4. Was the ceremony run on this cluster at all?**

    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
    sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault auth list'
    sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /home/vault/.vault-token'

No `kubernetes/` mount in the list means `configure-vault.sh` never ran on
this cluster — see `platform/vault/README.md` and
`platform/vault-secrets-operator/README.md` for the ceremony.

### The general point, again

As in entry 9: **a Synced Application describes what Argo CD applied,
never what is working.** `vso-config` can report all four objects
Synced/Healthy — `ServiceAccount`, `VaultConnection`, `VaultAuth`,
`VaultStaticSecret` — while authentication fails and the Secret quietly
stops updating. Check the `VaultStaticSecret`'s own status, not the
Application's:

    sg k3s-admin -c 'kubectl -n vault get vaultstaticsecret vault-canary -o jsonpath={.status.conditions[*].message}'

---

## 11. PostgreSQL will not start, or rejects the password

### Symptom

`postgres-0` never becomes Ready, crash-loops shortly after starting, or a
client that connected successfully before now gets
`password authentication failed for user "postgres"`.

### 1. Is the Secret there at all?

    sg k3s-admin -c 'kubectl -n databases get secret postgres-credentials'

If this returns nothing, this is not a database problem — it is the same
Vault-path problem entry 10 already covers, just for a second consumer. Go
to entry 10 and work through it with these substitutions in place of the
canary's names:

| Canary | PostgreSQL |
| --- | --- |
| Namespace `vault` | Namespace `databases` |
| ServiceAccount `vault-canary` | ServiceAccount `postgres` |
| Role `vso-canary` | Role `vso-postgres` |
| Policy `vso-canary-read` | Policy `vso-postgres-read` |
| Secret `vault-canary` | Secret `postgres-credentials` |
| `platform/vault-secrets-operator/config/vault-secrets.yaml` | `platform/databases/postgres/config/vault-secrets.yaml` |

Entry 10's own commands name the canary's policy and manifest path
literally — reading them for PostgreSQL without swapping those two in
particular checks the wrong policy and greps the wrong file, and both
still return something that looks plausible.

### 2. `initdb` refusing a non-empty directory

The symptom is a pod that crash-loops with, in its own logs:

    directory "/var/lib/postgresql/18/docker" exists but is not empty

The cause is the PVC mounted directly at the data directory instead of one
level up, at `/var/lib/postgresql`. PostgreSQL 18's official image moved
`PGDATA` to `/var/lib/postgresql/18/docker` — **not**
`/var/lib/postgresql/data`, where older guides for this image still point.
Mounting at `/var/lib/postgresql` leaves `PGDATA` a subdirectory of the
mount, which is what `initdb` requires; a freshly provisioned volume is not
reliably empty at exactly the mount point itself. Confirm the live value:

    sg k3s-admin -c 'kubectl -n databases exec postgres-0 -- sh -c "echo PGDATA=\$PGDATA"'

### 3. The password works but a rotation did not take

This is entry 10's divergence again, on a real running database instead of
a canary Secret. `POSTGRES_PASSWORD` is read only once, at `initdb` —
changing `homelab/postgres` in Vault updates `postgres-credentials`, not
the database itself. A client using the newly-rotated Secret gets a real,
correctly-formed credential the database has simply never seen. This will
look exactly like a broken credential and is not; see
`platform/databases/postgres/README.md`'s rotation-limitation section for
the `\password` fix and why no automatic path exists yet.

### 3b. The same divergence, reached without anyone rotating anything

Rotation is not the only way to get here. If Vault is rebuilt from empty —
not restored from a snapshot — while the PostgreSQL PVC on `/srv` survives
(exactly the property phase 16 established for `/srv/kubernetes/storage`),
the seed guard in `configure-vault.sh` finds no `homelab/postgres`, so it
generates a **new** password. VSO delivers that new value into
`postgres-credentials` as usual, and the surviving database still holds the
old one. The symptom, cause, and fix are identical to §3 above — the only
difference is how the two sides came to disagree. Since a Vault rebuild
with a surviving PVC is the far more likely way to meet this cluster's
history than a human forgetting to run `\password`, check this route
first if no one on the team remembers rotating anything.

### 4. A permission error on the data directory

Cross-reference entry 1 — the same ACL-inheritance fault that poisoned
non-root containers cluster-wide affects PVC directories under
`/srv/kubernetes/storage` for exactly the same reason, and PostgreSQL runs
as a non-root uid. Confirm the pod's security context matches what the
image actually needs:

    sg k3s-admin -c 'kubectl -n databases get pod postgres-0 -o jsonpath={.spec.securityContext}'

Expect `{"fsGroup":70,"runAsGroup":70,"runAsNonRoot":true,"runAsUser":70}` —
the image's `postgres` user is uid/gid 70, not the 999 usually assumed for
other images in this cluster. A mismatch here, or a poisoned snapshot root
per entry 1, produces a permission-denied error at container start, before
`psql` ever gets a chance to run.

### 5. The Application itself was permanently OutOfSync

Unrelated to the four faults above and not specific to PostgreSQL — the
`postgres` Application spent time permanently OutOfSync with no visible
difference, from `volumeClaimTemplates`'s atomic-list ownership under
server-side apply. See entry 12, which generalizes it: it will recur on the
next StatefulSet, not just this one.

### The standing point, again

As entries 9 and 10 both established: a Synced Application says what Argo
CD applied, never what is running. `postgres` can show Synced/Healthy while
crash-looping on a non-empty-directory error, or silently refusing every
client because of the rotation divergence in §3 above.

---

## 12. An Application is permanently OutOfSync with no visible difference

### Symptom

An Application never reaches `Synced`. It sits `OutOfSync` forever, runs a
sync operation on every reconcile, and each one reports success — then goes
`OutOfSync` again on the next poll. `kubectl diff --server-side` against the
live object comes back clean: no field, no line, nothing a human would call
a difference.

### Cause

`volumeClaimTemplates` on a StatefulSet is an **atomic list** under
server-side apply — `argocd-controller` owns the whole entry as one unit,
not field by field. The API server echoes back `apiVersion: v1` /
`kind: PersistentVolumeClaim` on every read of the embedded PVC, because it
defaults that embedded object exactly as if it were a standalone one. Argo
CD's StatefulSet diff normalizer already knows to discount some fields the
API server adds here (`spec.volumeMode`, `status.phase`) — but not these
two. Omit them from the manifest in git, and the target's atomic-list value
can never equal the live one on that one sub-field, even though every field
a human would think to compare already matches — because normalization
here isn't per-field, it's all-or-nothing per list entry.

### Diagnose

    sg k3s-admin -c 'kubectl -n databases get statefulset postgres -o json --show-managed-fields'

(substitute the affected namespace and StatefulSet name). Look for
`f:volumeClaimTemplates: {}` under the `argocd-controller` entry — an
atomic list claimed whole, unlike the field-by-field ownership every other
section of the output shows.

### Fix

Declare `apiVersion: v1` and `kind: PersistentVolumeClaim` explicitly on
the embedded PVC inside `volumeClaimTemplates`, matching what the API
server already echoes back on every read. Vault's StatefulSet (rendered by
its Helm chart) never hits this, only because the chart happens to write
both fields already — a hand-written manifest does not get that for free.

### The general shape

When Argo CD's normalizer discounts *some* server-added fields in an atomic
list but not all, the target can never equal live, and the Application
reports `OutOfSync` forever for a difference that does not exist by any
comparison a human can run. This is the sharpest instance yet of the
principle entries 8 through 11 all converge on: a `Synced` Application
describes what Argo CD applied and never what is running — and here even
`OutOfSync` stops meaning what it usually means, since `kubectl diff
--server-side` confirms there is nothing actually different.

Confirmed on this cluster on the `postgres` StatefulSet, fixed in commit
`b6ceb74` — see `platform/databases/postgres/config/postgres.yaml`'s
`volumeClaimTemplates` comment for the specific fix, and entry 11 above
for the rest of that component's faults. Nothing about the mechanism is
PostgreSQL-specific: it will recur on the next StatefulSet, or any other
atomic list the API server defaults fields into.
