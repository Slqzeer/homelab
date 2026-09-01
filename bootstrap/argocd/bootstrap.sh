#!/usr/bin/env bash
#
# First install and recovery path for Argo CD.
#
# This script is NOT a routine tool. After the initial bootstrap, Argo CD
# manages itself via environments/homelab/apps/argocd.yaml and upgrades happen
# by editing targetRevision and committing.
#
# It stays in the repository because self-management has one failure mode it
# cannot recover from on its own: a bad commit to values.yaml that breaks the
# controller which would otherwise reconcile the fix. Re-running this script
# restores a working Argo CD from the last good commit.
#
# Idempotent: safe to re-run at any time.

set -euo pipefail

# MUST equal targetRevision in environments/homelab/apps/argocd.yaml
CHART_VERSION="10.5.0"
NAMESPACE="argocd"
RELEASE="argocd"
CHART_REPO_NAME="argo"
CHART_REPO_URL="https://argoproj.github.io/argo-helm"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALUES_FILE="${SCRIPT_DIR}/values.yaml"

if [[ ! -f "${VALUES_FILE}" ]]; then
  echo "error: values file not found at ${VALUES_FILE}" >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE}"
kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 \
  || kubectl create namespace "${NAMESPACE}"

echo "==> Ensuring chart repo ${CHART_REPO_NAME}"
helm repo add "${CHART_REPO_NAME}" "${CHART_REPO_URL}" --force-update >/dev/null
helm repo update "${CHART_REPO_NAME}" >/dev/null

echo "==> Installing ${RELEASE} (chart ${CHART_VERSION}) into ${NAMESPACE}"
helm upgrade --install "${RELEASE}" "${CHART_REPO_NAME}/argo-cd" \
  --namespace "${NAMESPACE}" \
  --version "${CHART_VERSION}" \
  --values "${VALUES_FILE}" \
  --wait \
  --timeout 10m

echo "==> Waiting for rollout"
kubectl -n "${NAMESPACE}" rollout status deploy/argocd-server --timeout=5m

echo "==> Done. Reach the UI with:"
echo "    kubectl port-forward -n ${NAMESPACE} svc/argocd-server 8080:80"
