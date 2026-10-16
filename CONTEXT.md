# Homelab

This repository defines the desired state and recovery knowledge for one
single-node k3s environment reconciled by Argo CD.

## Language

**Root Application**:
The Argo CD Application defined by `environments/homelab/root.yaml` that discovers
child Application declarations.
_Avoid_: parent deployment, bootstrap app

**Child Application**:
An Argo CD Application under `environments/homelab/apps/` that reconciles one
component or application repository.
_Avoid_: component manifest, app file

**Application repository**:
The repository that owns an application's source, image build, and deployable
manifests consumed by a Child Application.
_Avoid_: service folder, Homelab app

**Ceremony**:
A documented human-controlled procedure for sensitive or non-declarative state,
such as Vault initialization, recovery, or credential rotation.
_Avoid_: setup script, manual fix

**Sync wave**:
The Argo CD ordering value that expresses a real reconciliation dependency.
_Avoid_: phase, priority
