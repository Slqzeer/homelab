# Storage

PersistentVolumes are provisioned by k3s's bundled `local-path-provisioner`
onto the data SSD at `/srv/kubernetes/storage`.

This is configured by a **k3s server flag**, not by an in-cluster manifest:

```yaml
# /etc/rancher/k3s/config.yaml
default-local-storage-path: /srv/kubernetes/storage
```

Do not edit the `local-path-config` ConfigMap directly. k3s owns
`${data-dir}/server/manifests/local-storage.yaml` and reverts changes to it on
every upgrade.

The `local-path` StorageClass is the cluster default and uses
`volumeBindingMode: WaitForFirstConsumer`, so a PVC stays `Pending` until a Pod
consumes it. That is expected, not a fault.

## Verified 2026-09-03: non-root pods can use local-path PVCs

Before Vault (phase 16) this cluster had never had a PersistentVolume.
`docs/troubleshooting.md` entry 1 predicted that PVC directories under
`/srv/kubernetes/storage` would break non-root pods, so it was tested
first rather than discovered during a Vault install.

A pod running `runAsUser: 100`, `runAsGroup: 1000`, `fsGroup: 1000` — the
same context the Vault chart uses — wrote a file to a local-path PVC, and
a second pod read it back after the first was gone.

Record what was observed:

- ACL on `/srv/kubernetes/storage` at the time:
  ```
  # file: /srv/kubernetes/storage
  # owner: root
  # group: k3s-admin
  user::rwx
  user:slqzeer:rwx	#effective:---
  group::r-x	#effective:---
  mask::---
  other::---
  ```
- Probe result: write pod logged `uid=100 gid=1000 groups=1000`, wrote
  `probe.txt` successfully, and printed `PROBE_WROTE_THIS` back. The write
  pod was then deleted and confirmed gone (`kubectl get pod
  storage-probe-write` returned `NotFound`, not just `Succeeded`) before a
  second pod was applied; it read `/data/probe.txt` and printed
  `PROBE_WROTE_THIS` — the volume is real, persisted storage, not a mount
  shared between two live pods.
- Host path of the provisioned PV:
  `/srv/kubernetes/storage/pvc-51a5dd5f-2ff6-4e8d-8402-55941abf0e57_default_storage-probe`

The write pod's `ls -la /data` showed the volume directory group-owned by
`1000` with the setgid bit set (`drwxrwsrwx ... 1000`) — `fsGroup` had
already been applied to the volume itself, not to the parent. That is why
the parent's `mask::---` was never on the container's path: the ACL
governs traversal of `/srv/kubernetes/storage` from the host, and the pod
never has to traverse it to reach a directory it's already been granted
group ownership of. Parent ACLs still matter for anything reading these
directories from the host — backups, for instance.
