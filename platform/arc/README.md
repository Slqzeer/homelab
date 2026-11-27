# Actions Runner Controller (CI runners)

Ephemeral GitHub Actions runners for the `The-Last-Exam` organization, run as
pods on a runner-labelled node (single node for now). Replaces the host-installed organization
runners. Tracks issue #113 (homelab side of The-Last-Exam/server#248).

| Piece | Where |
| --- | --- |
| Controller (chart `gha-runner-scale-set-controller` 0.15.1) | `controller/values.yaml`, Application `arc-controller`, ns `arc-systems` |
| Scale set `ci` (build, lint, test) | `ci/values.yaml`, Applications `arc-ci` + `arc-ci-config`, ns `arc-runners` |
| Scale set `publish` (image publishing) | `publish/values.yaml`, Applications `arc-publish` + `arc-publish-config`, ns `arc-publish` |
| Toolchain image for job containers | `ci-image/Dockerfile` |
| Procedures | [`docs/runbooks/arc-runners.md`](../../docs/runbooks/arc-runners.md) |

Workflows select a set with `runs-on: ci` or `runs-on: publish` (the server
repo sets `vars.CI_RUNNER`).

## Isolation model

- **Container mode, one pod per job.** The runner pod runs the runner agent and
  the Kubernetes container hooks; every step runs in a separate job pod (the
  workflow's `container:`, enforced by `ACTIONS_RUNNER_REQUIRE_JOB_CONTAINER`)
  with service containers (Postgres, Valkey, NATS) in the same pod. No Docker
  daemon, no host socket. Both pods are deleted after the job.
- **Node placement (single node for now).** Every pod in the runner
  namespaces carries `nodeSelector homelab.io/runner=true`, and a
  `ValidatingAdmissionPolicy` (`config/node-admission.yaml`) rejects any pod
  there without it. The cluster has one node, which carries that label, so
  runners share it with the platform: **there is no node-level isolation yet**.
  Mitigations: `maxRunners: 1` per set and memory limits on job pods. When a
  second machine exists, join it with the label AND the taint
  `homelab.io/runner=true:NoSchedule` (pods already tolerate it), then
  remove the label from the control-plane node; no manifest change needed.
- **Network.** Default-deny both ways. Every pod may reach DNS and the egress
  proxy only; Squid allowlists GitHub, GHCR and the package registries
  (`platform/egress-proxy`). Only the *runner* pod (`homelab.io/arc-role=runner`)
  may reach the Kubernetes API; job pods (`homelab.io/arc-role=job`) cannot, and
  nothing reaches Vault, databases or any platform namespace.
- **Identity.** Job pods use the namespace `default` ServiceAccount: no Role,
  no mounted token. The runner pod's ServiceAccount `arc-runner` holds a
  namespaced Role (pods, pods/exec, pods/log, jobs, and `create`/`delete` on
  Secrets) because the container hooks require it.
- **GitHub App, not a PAT.** `homelab/arc-github-app` in Vault -> VSO ->
  Secret `arc-github-app` in each runner namespace, read by the controller.
  The runner Role has no `get`/`list` on Secrets, and job pods have no token.

## Deviations from the issue text (decided in review of #113)

- The kubernetes container mode requires the runner pod to call the API
  server. The criterion is therefore met as: **job pods** (where workflow code
  runs) cannot reach the API, Vault or platform namespaces and hold no
  credential; the **runner pod** has API access through a namespaced Role only.
- The runner namespaces enforce Pod Security `baseline`, not `restricted`
  (audit and warn stay `restricted`): job and service images such as `rust`,
  `golang` and `postgres` start as root. Baseline still forbids privileged
  pods, hostPath, host namespaces and extra capabilities.
- The chart's generated kube-mode Role is not used: it grants `get`/`list` on
  Secrets, which would expose the GitHub App key to the runner container.

## Prerequisites outside Git

1. The node labelled `homelab.io/runner=true` (runbook, "Dedicated node").
2. Argo CD knows the OCI registry (`ghcr.io/actions/actions-runner-controller-charts`,
   `type: helm`, `enableOCI: true`) -- a repository entry, like the Git
   credentials, not stored here.
3. The GitHub App created, installed on the organization, and its credential
   seeded in Vault (`docs/runbooks/arc-runners.md`).
4. The `publish` runner group created in the organization and restricted to
   the server repo's publish workflow on `main` and release tags.

Until (1) and (3) are done the scale-set Applications sync but no runner pod
can start; jobs stay queued. The host-installed runners keep working until the
decommission step.

## Open points, resolved here

- **Node:** the existing single node, labelled. A VM on it was rejected (RAM:
  15 GiB total, ~4 GiB available). A separate machine remains the target for
  the "dedicated node" criterion of #113.
- **Cache:** GitHub Actions cache (`actions/cache`, `Swatinem/rust-cache`)
  through the proxy (`.blob.core.windows.net` is allowlisted). No shared
  volume: it would let one job poison the next.
- **Names:** `ci` and `publish`.
