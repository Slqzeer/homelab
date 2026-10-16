# Homelab Deployment

## Reconciliation

`environments/homelab/root.yaml` is the only manifest applied manually. After
bootstrap, reviewed Git changes drive reconciliation through Argo CD. Add a
component by committing its Child Application under
`environments/homelab/apps/` and choosing a sync wave that reflects an actual
dependency.

Application repositories provide immutable image references and manifests.
Homelab Child Applications select an explicit revision; deployed images use an
immutable digest. Secrets are delivered through Vault and the Vault Secrets
Operator rather than committed files.

## Update and Rollback

Update desired state through a reviewed Git change and wait for Argo CD to report
the intended revision as `Synced` and `Healthy`. Roll back by reverting the
reviewed Git change. Follow the component runbook when state, secrets, or a manual
ceremony are involved.

Removing a component follows the finalizer and prune sequence in the root
[README](../README.md). A file deletion alone can orphan resources and is not an
approved retirement procedure.

## Verification

Static validation proves repository consistency only. Live acceptance records
the Argo CD revision and health, Kubernetes workload state, relevant endpoint or
protocol checks, and rollback readiness without exposing secret values.
