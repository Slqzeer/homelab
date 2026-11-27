# CI runners (Actions Runner Controller) -- runbook

Design and isolation model: [`platform/arc/README.md`](../../platform/arc/README.md).
Manifests are reconciled by Argo CD; the steps below are the human ceremonies.

## Dedicated node

The runner pods only schedule on a node labelled `homelab.io/runner=true` and
tainted `homelab.io/runner=true:NoSchedule`. Join a machine or VM as a k3s
agent with both set at join time, so there is no window where it takes other
workloads:

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://192.168.1.201:6443 K3S_TOKEN=<node-token> \
  sh -s - agent \
  --node-label homelab.io/runner=true \
  --node-taint homelab.io/runner=true:NoSchedule
```

The node-token is read on the server from `/var/lib/rancher/k3s/server/node-token`
(this cluster's data dir is `/srv/kubernetes/data`). Verify:

```bash
kubectl get nodes -L homelab.io/runner
kubectl describe node <node> | grep -A1 Taints
```

Allow 6443 from the node to the server, and keep the node off the tailnet
ACL groups that reach platform services.

## GitHub App and Vault

1. Create a GitHub App on the organization with permissions *Actions: read*,
   *Administration: read and write (organization self-hosted runners)*,
   *Metadata: read*. Install it on the organization. Note the App ID and the
   installation ID, and generate a private key (`.pem`).
2. Apply Vault intent (policy `vso-arc-github-app-read`, role `vso-arc`), as
   in [`tle-dev-vault-ceremony.md`](tle-dev-vault-ceremony.md) step 1.
3. Seed the credential, reading the key from the file so it is never an
   argument or in history:

   ```bash
   sg k3s-admin -c 'kubectl -n vault exec -it vault-0 -- vault login'
   sg k3s-admin -c "kubectl -n vault exec -i vault-0 -- sh -c 'cat > /tmp/arc.pem'" < app.pem
   sg k3s-admin -c "kubectl -n vault exec vault-0 -- vault kv put homelab/arc-github-app \
     github_app_id=<id> github_app_installation_id=<id> github_app_private_key=@/tmp/arc.pem"
   sg k3s-admin -c 'kubectl -n vault exec vault-0 -- rm -f /tmp/arc.pem /home/vault/.vault-token'
   shred -u app.pem
   ```

Rotate by generating a new key, repeating `vault kv put`, then deleting the old
key in GitHub. VSO refreshes within five minutes.

## Runner groups

Create the group `publish` in the organization and restrict it to the server
repository and the publish workflow on `main` and release tags. The `ci` set
uses the `default` group. This restriction is what limits the publish runners;
ARC itself has no ref filter.

## Toolchain image

Job containers use a toolchain image built from `platform/arc/ci-image/Dockerfile`,
pinned by digest in the server workflows' `container:`:

```bash
BASE=$(docker buildx imagetools inspect debian:bookworm-slim --format '{{.Manifest.Digest}}')
docker build --build-arg BASE=debian:bookworm-slim@$BASE --build-arg MISE_VERSION=<ver> \
  -t ghcr.io/the-last-exam/ci-toolchain:<date> platform/arc/ci-image
docker push ghcr.io/the-last-exam/ci-toolchain:<date>
docker buildx imagetools inspect ghcr.io/the-last-exam/ci-toolchain:<date>   # note the digest
```

Update `container:` in the server reusable workflows to `...@sha256:<digest>`.
The image must run non-root-capable jobs on `HOME` set by the hooks; if a step
needs a writable `HOME`, set `HOME=/tmp` in the workflow.

## Rebuilding the runners

Everything is declarative, so a rebuild is: restore the cluster (see
`docs/deployment.md`), join the dedicated node, re-run the Vault ceremony
(the key survives in Vault; only the policy/role need re-applying), and let
Argo CD sync `arc-*`. Bump the runner image by digest in
`platform/arc/{ci,publish}/values.yaml`
(`docker buildx imagetools inspect ghcr.io/actions/actions-runner:<version>`).

## Verification

```bash
# Runner pod cannot read the GitHub App key, job pods have no token
kubectl auth can-i get secret/arc-github-app -n arc-runners \
  --as=system:serviceaccount:arc-runners:arc-runner          # no
kubectl auth can-i --list -n arc-runners \
  --as=system:serviceaccount:arc-runners:default             # no verbs
# Pods only on the dedicated node
kubectl get pods -A -o wide --field-selector spec.nodeName=<node>
# Ephemeral: no runner/job pods remain after the queue drains
kubectl -n arc-runners get pods
```

From a job: `curl -m5 -k https://kubernetes.default.svc` and `nc -vz
vault.vault.svc 8200` must fail; `ls /var/run/docker.sock` and `sudo -n true`
must fail.

## Decommission the host runners

After a green run of The-Last-Exam/server on the new sets (CI and publish):

1. Set the server repo's `vars.CI_RUNNER` to `ci` (publish workflow: `publish`).
2. On the host: `sudo ./svc.sh stop && sudo ./svc.sh uninstall` for each
   runner, then `./config.sh remove --token <removal-token>`; delete the
   runner directories.
3. Remove the runner account's access: `sudo gpasswd -d <runner-user> docker`
   and `sudo gpasswd -d <runner-user> k3s-admin`, delete its sudoers entries,
   its kubeconfig and SSH keys, then `sudo passwd -l <runner-user>` (or
   `userdel -r`). Check with `sudo -l -U <runner-user>` and `id <runner-user>`.
4. Confirm the runners are gone in the organization's Actions settings.

Record the result in issue #113.
