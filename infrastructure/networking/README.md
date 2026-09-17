# Tailnet networking

## `policy.hujson` is NOT reconciled by Argo CD

This is the only file in this repository that Argo CD does not apply and
cannot apply. It targets Tailscale's control plane, not the Kubernetes
API. **Committing and pushing it changes nothing.**

To apply a change:

1. Open <https://login.tailscale.com/admin/acls>
2. Paste the file contents
3. Save in the console

**Git will be authoritative for this file, once `apply` is wired.** Phase 20
added `tailscale/gitops-acl-action`: `.github/workflows/tailscale-acl.yaml`
already tests the policy — parses it and runs its ACL tests — on every pull
request and every push to `main` that touches it. It does **not** apply
anything yet. Flipping it to `action: apply` is deliberately the phase's
last, separate step, withheld until this file has been reconciled against
what is actually live (see below). Until that step lands, applying a change
is still the manual process above, and the console remains what is actually
running.

### A console edit will be silently reverted, once `apply` lands

Not "may" — will, at the first push after that switch that touches this
file, with no warning that survives to anyone who would act on it.
`gitops-pusher` has a drift guard and **it cannot fire in CI**:

- It compares a cached etag against the control plane's, but the cache file
  (`./version-cache.json`) does not survive a runner. `PrevETag` is therefore
  always empty on entry, and the code fills it with the *current* control
  etag — so the comparison it guards with can never be true.
- Even if it could be, `--fail-on-manual-edits` defaults to false (a printed
  warning, not a failure) and the composite action exposes no input to set
  it.

So the rule below is not a nicety to adopt later. It is the only thing that
will stand between a console edit and its deletion once `apply` is wired, and
adopting it now means it is already habit by then.

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

Like the tag owners above, this stanza only takes effect once applied by
hand in the admin console (see "`policy.hujson` is NOT reconciled by Argo
CD" above) — Argo CD has no way to apply a Tailscale policy file.

**Status: applied, but not load-bearing for the current design.** After
this stanza was applied, the Tailscale Service mechanism it approves
proved unroutable on this tailnet regardless — see design doc §10. The
Argo CD Ingress now uses a dedicated proxy (`tailscale.com/proxy-class`)
instead of the shared `ProxyGroup`, which needs no Tailscale Service and
so does not need this approval today. The stanza is left in place because
it is harmless and would be required again if ProxyGroups are ever
revisited — do not remove it thinking it is dead config, and do not
assume it is doing anything for the cluster right now.

Automating this uses a second OAuth client, held as the repository secrets
`TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`
(<https://github.com/Slqzeer/homelab/settings/secrets/actions>), consumed by
`.github/workflows/tailscale-acl.yaml`. It is deliberately separate from the
Tailscale Kubernetes operator's OAuth client: the operator's client carries
device scopes and no policy access, and widening it would let the in-cluster
operator rewrite the tailnet policy. Neither secret value exists in any
repository — GitHub stores them encrypted and exposes them only to workflow
runs.
