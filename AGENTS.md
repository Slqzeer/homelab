# Agent Entry Point

Use this repository for the declarative state and operating procedures of the
Homelab environment.

## Reading Order

1. Read the [global knowledge conventions](https://github.com/Slqzeer/global-knowlegde/blob/main/docs/foundations/knowledge-conventions.md) before changing documentation.
2. Read [CONTEXT.md](CONTEXT.md) before naming GitOps and environment concepts.
3. Use the [documentation index](docs/README.md) to select the applicable
   architecture, onboarding, troubleshooting, or runbook document.
4. Read the nearest component `README.md` before changing its manifests.

## Working Rules

- This repository owns cluster composition, GitOps declarations, and Homelab
  operating procedures. Application repositories own their code, images, and
  application-specific manifests.
- Change desired state through Git and preserve the documented manual ceremonies
  for secrets, recovery, and destructive component retirement.
- Treat GitHub Issues as work requests and Hermes cards as execution projections.

## Completion Gate

Run the repository validation workflow locally where its dependencies are
available, including `python3 -m unittest discover -s tests -v`. Report every
command and observed result; never claim live-cluster acceptance from static
validation alone.
