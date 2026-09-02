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
this repository. Treat it as backup-worthy state until it moves into Vault
(plan phase 17).

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

Losing `operator-oauth` does **not** stop reconciliation — Argo CD keeps
working, and `kubectl port-forward` still reaches it. Symptoms are
hostnames that stop resolving and proxy pods that fail to authenticate:

```bash
kubectl -n tailscale logs deploy/operator --tail=50
```

Both are backup-worthy until Vault takes over in phase 16.

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
