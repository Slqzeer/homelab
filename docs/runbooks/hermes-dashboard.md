# Hermes dashboard — runbook

The native Hermes dashboard manages the same machine-level configuration,
profiles and sessions as the host `hermes-gateway` service. It therefore runs
on the node instead of mounting the credential-bearing `~/.hermes` directory
into Kubernetes. Kubernetes owns only the stateless relay:

    tailnet -> Tailscale Ingress `hermes`
            -> Service `hermes-dashboard`:80
            -> nginx relay:8080
            -> node 10.42.0.1:9119
            -> native Hermes dashboard

The relay supports HTTP and WebSocket upgrades. NetworkPolicy admits only the
shared Tailscale ProxyGroup and permits egress only to the dashboard listener.

## Authentication

The public URL is `https://hermes.taildf6cd4.ts.net`. Hermes uses its bundled
generic self-hosted OIDC provider against the Keycloak `homelab` realm. The
`hermes-dashboard` Keycloak client is public, uses authorization-code flow with
PKCE, and has the single redirect URI
`https://hermes.taildf6cd4.ts.net/auth/callback`. It has no client secret.

The host `~/.hermes/config.yaml` must contain:

```yaml
dashboard:
  public_url: https://hermes.taildf6cd4.ts.net
  oauth:
    provider: self-hosted
    self_hosted:
      issuer: https://keycloak.taildf6cd4.ts.net/realms/homelab
      client_id: hermes-dashboard
      scopes: openid profile email groups
```

## Host service

The canonical unit is `apps/hermes-dashboard/host/hermes-dashboard.service`.
Install it as the Hermes user's unit, reload systemd, and enable it. The
dashboard binds the node's pod-network address, not the LAN or wildcard:

```bash
install -m 0644 apps/hermes-dashboard/host/hermes-dashboard.service \
  "$HOME/.config/systemd/user/hermes-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable --now hermes-dashboard.service
```

## Checks

```bash
systemctl --user status hermes-dashboard --no-pager
curl -fsS http://10.42.0.1:9119/api/health
kubectl -n hermes-dashboard get deploy,po,svc,ingress,networkpolicy
curl -I https://hermes.taildf6cd4.ts.net/
```

The HTML shell and health endpoint are public, while management APIs return
401 until the Keycloak login completes. A 502 means the host dashboard is
down or not bound to `10.42.0.1`; an unready relay reports the same condition
to Argo CD.

## Retirement

Remove the Ingress and Application first, confirm the relay is pruned, then
disable the host unit. The dashboard owns no independent data; Hermes state
remains under `~/.hermes`.
