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

Automating this in phase 20 needs a second OAuth client with `policy_file`
write scope, separate from the operator's.
