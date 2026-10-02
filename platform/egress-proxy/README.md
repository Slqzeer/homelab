# Egress proxy

Squid as an allowlisting HTTPS forward proxy. It is the only workload with
broad internet egress (TCP 443 to public addresses); application namespaces
keep default-deny egress and reach the internet only through it, which keeps
them inside the onboarding contract's "no 0.0.0.0/0" rule.

| Item | Value |
| --- | --- |
| Image | `docker.io/ubuntu/squid:6.6-24.04_beta@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029` (Canonical; `_beta` is Canonical's channel name for its maintained images) |
| Runs as | `proxy` (UID 13), read-only root FS, squid started directly (the image entrypoint expects root) |
| Endpoint | `http://egress-proxy.egress.svc.cluster.local:3128` |
| Allowed | `CONNECT` to port 443 of hosts in `allowed-domains.txt` (`config/configmap.yaml`); everything else is denied. No caching, no TLS interception |
| Memory | ~33 MiB steady, ~36 MiB peak at start (limit 128Mi; needs `max_filedescriptors`, see configmap) |
| Clients | `omniroute` only (`config/networkpolicy.yaml`, rule `egress-proxy-clients`) |

Operations:

- **Add a destination:** append to `allowed-domains.txt` in
  `config/configmap.yaml` (leading dot = domain and subdomains; no overlapping
  entries), bump `egress-proxy.homelab.io/config-revision` in
  `config/egress-proxy.yaml`, push.
- **Add a client namespace:** add a `from` entry to `egress-proxy-clients`, and
  in the client namespace an egress rule to `egress-proxy:3128` plus
  `HTTPS_PROXY`.
- **See what is blocked:** `kubectl -n egress logs deploy/egress-proxy | grep DENIED`.
- **State:** none (stateless; restart at will).
