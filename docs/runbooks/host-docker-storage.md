# Host Docker storage — runbook

Docker and the system containerd store their data on `/srv`, not on the root
filesystem. The root filesystem is too small for them: they hold about 130 GB of
images, containers, volumes and build cache.

## Layout

| Component | Root | Mode | Configured in |
| --- | --- | --- | --- |
| Docker | `/srv/docker` | `0710` | `data-root` in `/etc/docker/daemon.json` |
| System containerd | `/srv/containerd` | `0700` | top-level `root` in `/etc/containerd/config.toml` |
| k3s | `/srv/kubernetes/data` | unchanged | k3s data-dir, with its own embedded containerd |

The k3s containerd is a separate instance: stopping Docker and the system
containerd does not touch the cluster.

## Why both roots

Docker uses the containerd image store (`io.containerd.snapshotter.v1`), so
image content and snapshots live in the containerd root, not in `data-root`.
Setting `data-root` alone leaves most of the data where it was.

## Verify

```bash
docker info --format '{{.DockerRootDir}} {{.Driver}}'
grep -n '^root' /etc/containerd/config.toml
getfacl -p /srv/docker /srv/containerd | grep -c '^default:'
docker run --rm --pull always --user 1000 alpine:3.20 id
```

Expect `/srv/docker overlayfs`, `root = "/srv/containerd"`, a count of `0`, and
`uid=1000`.

Use a non-root UID and `--pull always`. `/srv` carries a default ACL, and a
snapshot root that inherits one breaks non-root containers while root containers
keep working. See [troubleshooting entry 1](../troubleshooting.md#1-non-root-containers-fail-to-start-permission-denied-on-their-own-binary).

## The `containerd.io` upgrade

`/etc/containerd/config.toml` is a package conffile. When `apt` asks, keep the
local version, then run the checks above. If the package version replaces it,
containerd silently starts on an empty `/var/lib/containerd` and every image
appears to be gone. The data is still in `/srv/containerd`: restore the `root`
line and restart `containerd` and `docker`.

## Move the roots again

Run this outside the 05:00 nightly reboot window. Wait until no GitHub runner has
a job and no agent work is running. Inventory the images, volumes and containers
first, so they can be compared afterwards.

```bash
sudo systemctl stop 'actions.runner.*'
sudo systemctl stop docker.socket docker containerd

sudo install -d -m 0710 -o root -g root <new-docker-root>
sudo install -d -m 0700 -o root -g root <new-containerd-root>
sudo setfacl -b <new-docker-root> <new-containerd-root>

sudo rsync -aHAXS --delete --numeric-ids --info=progress2 <old-containerd-root>/ <new-containerd-root>/
sudo rsync -aHAXS --delete --numeric-ids --info=progress2 <old-docker-root>/ <new-docker-root>/
```

Repeat both `rsync` commands with `-n --itemize-changes`. They must print
nothing. Then:

1. Point `data-root` and `root` at the new paths.
2. Rename the old roots to `<old-root>.old`. Do not delete them.
3. Start `containerd`, `docker.socket` and `docker`, then the runners.
4. Run the checks in [Verify](#verify) and compare the inventories.

Keep the `.old` directories until 48 hours have passed and one nightly reboot
has succeeded. The disk space only comes back once they are deleted.

### Rollback

Rollback needs the `.old` directories.

1. Stop `docker.socket`, `docker` and `containerd`.
2. Restore the previous `data-root` and `root` settings.
3. Rename both `.old` directories back to their original paths.
4. Start `containerd`, `docker.socket` and `docker`, then the runners.

Anything written to the new roots since the move stays there and is not in the
restored data.
