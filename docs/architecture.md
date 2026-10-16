# Homelab Architecture

## Responsibility

The repository defines a single-node k3s environment. Argo CD continuously
reconciles the Git state rooted at `environments/homelab/root.yaml`. Application
repositories own application images and manifests; this repository owns their
admission into the environment and the shared platform they consume.

## Layers

- `bootstrap/` installs Argo CD and foundational namespaces.
- `infrastructure/` owns network exposure, storage, and cluster infrastructure.
- `platform/` owns shared services such as Vault, databases, registry access,
  Keycloak, and agent access.
- `observability/` owns metrics and log delivery.
- `environments/homelab/apps/` contains Argo CD Child Applications.
- `apps/` contains Homelab-specific values and supplemental manifests, never
  Child Application objects.

Sync waves express dependencies between these layers. The canonical wave and
failure semantics are documented in the root [README](../README.md); do not copy
that evolving table here.

## Boundaries

Git records desired state, Argo CD owns reconciliation, Vault owns secret values,
and the Vault Secrets Operator projects approved secrets into Kubernetes.
Application repositories remain responsible for their source, release evidence,
and concrete application deployment manifests.
