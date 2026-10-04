# Tailnet networking

## `policy.hujson` is NOT reconciled by Argo CD

This is the only file in this repository that Argo CD does not apply and
cannot apply. It targets Tailscale's control plane, not the Kubernetes
API, and Argo CD has no way to reconcile it.

**Git is authoritative for this file.** Phase 20 added
`tailscale/gitops-acl-action`: `.github/workflows/tailscale-acl.yaml` tests
the policy — parses it and runs its ACL tests — on every pull request that
touches it, and applies it (`action: apply`) on every push to `main` that
touches it. **Committing and pushing a change to `main` applies it to the
live tailnet policy**, wholesale, through that workflow — not through Argo
CD. This is now the normal way to apply a change: edit the file, commit,
push.

A manual path still exists, for recovery when the tailnet itself is down or
console access is the only thing available:

1. Open <https://login.tailscale.com/admin/acls>
2. Paste the file contents
3. Save in the console

A change made this way is not permanent — see the next section.

### A console edit is silently reverted

Not "may" — will, at the next push that touches this file, with no warning
that survives to anyone who would act on it. `gitops-pusher` has a drift
guard and **it cannot fire in CI**:

- It compares a cached etag against the control plane's, but the cache file
  (`./version-cache.json`) does not survive a runner. `PrevETag` is therefore
  always empty on entry, and the code fills it with the *current* control
  etag — so the comparison it guards with can never be true.
- Even if it could be, `--fail-on-manual-edits` defaults to false (a printed
  warning, not a failure) and the composite action exposes no input to set
  it.

So the rule below is not a nicety. It is the only thing that stands between
a console edit and its deletion, and it needs to already be habit.

### Before editing this file, copy the live policy into it first

The policy file is not merged into the live policy — it **replaces** it. Open
<https://login.tailscale.com/admin/acls>, copy what is actually live into
this file, and make your change on top of that. Skipping this deletes any
rule added in the console since this file was last reconciled against it.

If console drift becomes a recurring problem rather than an occasional one,
the escape hatch is to abandon the composite action and invoke
`gitops-pusher` directly with `--fail-on-manual-edits` and a committed
`version-cache.json`. That was considered and rejected for this phase — see
the phase-20 spec, §7.3 — but the reasoning is recorded so it need not be
rediscovered.

## What the cluster depends on

The Tailscale Kubernetes operator will not work without these tag owners:

    "tagOwners": {
      "tag:k8s-operator": [],
      "tag:k8s":          ["tag:k8s-operator"],
    },

The operator identifies as `tag:k8s-operator`. Proxy pods it creates are
tagged `tag:k8s`, owned by the operator so it can create them unattended.
Removing either breaks all tailnet ingress.

A ProxyGroup-backed Ingress also needs `autoApprovers.services`:

    "autoApprovers": {
      "services": {
        "tag:k8s": ["tag:k8s"],
      },
    },

Publishing a Tailscale Service is a double opt-in action: the operator
advertises the Service, and the tailnet policy must separately approve it.
The operator tags both the ProxyGroup devices and the Services they
advertise with `tag:k8s`, so this stanza is what grants that approval.
Without it, the Ingress hostname still resolves via MagicDNS but nothing
answers on it — the Service was advertised and never approved, so it
routes nowhere.

Like the tag owners above, this stanza only takes effect once applied —
via a push to `main` that triggers `tailscale-acl`'s `apply` job, or by hand
in the admin console (see "`policy.hujson` is NOT reconciled by Argo CD"
above) — Argo CD has no way to apply a Tailscale policy file.

**Status: applied and load-bearing.** Every tailnet Ingress is served by
the shared `ingress` ProxyGroup (`infrastructure/ingress/config/proxygroup.yaml`),
which publishes each hostname as a Tailscale Service. Without this stanza
every one of those hostnames resolves and routes nowhere. When it was first
applied on 2026-09-02, the Service VIPs still did not route (design doc
§10). They did on 2026-10-05 with 1.102.3 everywhere and this policy
unchanged, so that is when the pool was adopted.

Automating this uses a second OAuth client, held as `TS_OAUTH_CLIENT_ID` and
`TS_OAUTH_SECRET` — Environment secrets on the `homelab` GitHub Actions
environment, not repository secrets
(<https://github.com/Slqzeer/homelab/settings/environments>), consumed by
`.github/workflows/tailscale-acl.yaml`. Both jobs in that workflow declare
`environment: homelab` for exactly this reason: `secrets.*` resolves an
environment's secrets only inside a job that declares that environment. It
is deliberately separate from the
Tailscale Kubernetes operator's OAuth client: the operator's client carries
device scopes and no policy access, and widening it would let the in-cluster
operator rewrite the tailnet policy. Neither secret value exists in any
repository — GitHub stores them encrypted and exposes them only to workflow
runs.
