# Agent access

A read-only Kubernetes identity for Hermes infrastructure agents. The
`agent-access` Argo CD Application creates ServiceAccount
`agents/hermes-infra-reader` and its RBAC; no credential or long-lived
ServiceAccount token is stored in this repository.

## What the identity can do

The identity combines Kubernetes' built-in `view` ClusterRole with the
narrow cluster-scoped role in `config/infra-reader.yaml`.

It can:

- get, list, and watch ordinary namespaced resources in every namespace,
  including workloads, events, Services, ConfigMaps, and pod logs;
- get, list, and watch nodes, namespaces, persistent volumes, storage classes,
  custom resource definitions, and RBAC objects;
- read Argo CD, Vault Secrets Operator, Prometheus Operator, and cert-manager
  custom resources used to diagnose reconciliation and health; and
- read pod and node metrics when the metrics API is available.

It cannot:

- read Kubernetes Secrets or their data;
- create, update, patch, or delete cluster resources;
- exec or attach to containers, start port forwards, or use other write-like
  pod subresources; or
- obtain cluster-admin access through this ServiceAccount.

`automountServiceAccountToken: false` prevents Kubernetes from mounting a
credential automatically if this ServiceAccount is later assigned to a pod.
The RBAC manifest is the authority for the exact resource and verb list.

## Short-lived kubeconfig

The credential is created on the host by
`/srv/projects/agent-platform/routeplane/agent_kubeconfig.sh`. The script uses
an operator's admin kubeconfig only to call `kubectl create token` for
`agents/hermes-infra-reader` through Kubernetes' TokenRequest API and to copy
the cluster server URL and public CA data. It requests a 48-hour token by
default, never prints the token, and atomically writes a mode-0600 kubeconfig
to `~/.hermes/profiles/infra/kubeconfig`.

The user crontab refreshes that file daily at 03:17:

```cron
17 3 * * * /srv/projects/agent-platform/routeplane/agent_kubeconfig.sh >> $HOME/.hermes/plugin-data/routeplane/kubeconfig-refresh.log 2>&1
```

The 48-hour lifetime leaves one missed daily refresh before the existing token
expires. The script finishes by checking that pod listing is allowed while
Secret reads and pod deletion are denied; its log contains only those
verdicts. Run the script manually after creating the ServiceAccount or to
recover from an expired token. `TOKEN_DURATION` can override the requested
lifetime, and the first argument can override the output path.

Routeplane sets `KUBECONFIG` to this file only for the `infra` worker. Other
worker profiles receive `/dev/null`, and command guards prevent workers from
selecting the host's admin kubeconfig or another kubeconfig.

## How agents change the cluster

Agents diagnose live state with this read-only identity. They do not run
`kubectl apply`, mutate resources directly, push branches, or bypass Argo CD.
Every cluster change is made as files on an `agent/<task>` branch in this
repository. A human reviews and pushes or merges that branch; Argo CD then
reconciles the approved commit into the cluster. This keeps the live change
path auditable and makes Git the source of truth.

## Files

| File | Purpose |
| --- | --- |
| `config/infra-reader.yaml` | ServiceAccount, built-in view binding, and explicit cluster-scoped read permissions |
| `config/networkpolicy.yaml` | Default-deny ingress and egress for future workloads in the `agents` namespace |
| `../../environments/homelab/apps/agent-access.yaml` | Argo CD Application that reconciles this directory |
