# routeplane dashboard — runbook

The routeplane tracking dashboard shows Hermes task chains, worker health and
grouped failures (product: `/srv/projects/agent-platform/routeplane/dashboard.py`).
It reads the Hermes gateway's SQLite store in the user's home, which a
`restricted` pod cannot mount, so the dashboard runs **on the node** as the user
service `routeplane-dashboard`. This repository only carries the hop from the
tailnet to it:

    tailnet -> Ingress routeplane (ProxyGroup ingress)
            -> Service routeplane:80 -> relay pod (nginx) :8080
            -> node 10.42.0.1:8765 (cni0) -> dashboard.py

A selectorless Service with an EndpointSlice would need no pod, but Argo CD
excludes `Endpoints`/`EndpointSlice` by default, so the relay is a pod.

## Release pinning (promotion gate)

| Field | Value |
| --- | --- |
| Source | upstream `nginxinc/nginx-unprivileged` (no product repo of ours for the relay) |
| Release | `1.30.5-alpine` (stable branch) |
| Image | `docker.io/nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:15c994d10d6d78658721c3bcafff14cb281fba2a4bdf9d5ba92c416a472516e3` (multi-arch index) |
| Image user | `nginx` (UID/GID 101) |
| Rollback target | none yet (first deployment); record the previous tag@digest here on every upgrade |

## Ownership and state

Stateless. The relay keeps nothing (`/tmp` is an emptyDir). The data is the
gateway's `~/.hermes/plugin-data/routeplane/routeplane.db` on the node, owned
and backed up by the agent platform, not by this repository.

## Access

Read-only end to end: the dashboard has no write route, and the relay denies
everything but GET/HEAD. It has **no login**: it is reachable from the tailnet
only (`https://routeplane.taildf6cd4.ts.net`) and deliberately unlisted in the
Homelab Portal, because pages show Hermes prompts and failure excerpts. Put a
Keycloak/oauth2-proxy login in front before ever publishing it.

## Network

Default-deny both ways. In: the tailnet proxy pool on 8080. Out: only
`10.42.0.1/32:8765`. The host process binds `10.42.0.1` (the node's cni0
address), which pods reach and the LAN does not; `dashboard.py --host` refuses
wildcard and public addresses.

## Operations

| Symptom | Check |
| --- | --- |
| Relay pod not Ready, Argo `Progressing` | `/readyz` proxies to the host: `systemctl --user status routeplane-dashboard` on the node |
| Page shows "stale data" | the DB is locked, missing or corrupt on the host; the relay is fine |
| 502 from the hostname | host service down, or cni0 not yet up after a reboot (the unit restarts every 15 s until it can bind) |

Install or rebind the host side with `routeplane/install.sh` in agent-platform.
Measured use: record idle and peak memory/CPU of the relay after rollout.

## Retirement

Remove `environments/homelab/apps/routeplane.yaml` and
`infrastructure/ingress/config/routeplane-ingress.yaml`, then the namespace
entry. On the node: `systemctl --user disable --now routeplane-dashboard`.
No data to retain here.
