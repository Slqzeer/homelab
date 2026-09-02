# Tailnet networking

## `policy.hujson` is NOT reconciled by Argo CD

This is the only file in this repository that Argo CD does not apply and
cannot apply. It targets Tailscale's control plane, not the Kubernetes
API. **Committing and pushing it changes nothing.**

To apply a change:

1. Open <https://login.tailscale.com/admin/acls>
2. Paste the file contents
3. Save in the console

Until phase 20 wires up `tailscale/gitops-acl-action` in GitHub Actions,
this file is a record of what should be live, kept under review, and
applied by hand. It can drift. Treat the console as authoritative and
this file as the reviewed copy.

## Applying replaces everything

The policy file is not merged into the live policy — it **replaces** it.
Before editing, copy the live policy into this file first, so an apply
cannot delete rules that were added in the console.

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

Automating this in phase 20 needs a second OAuth client with `policy_file`
write scope, separate from the operator's.
