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
