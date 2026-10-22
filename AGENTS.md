# Agent Entry Point

Use this repository for the declarative state and operating procedures of the
Homelab environment.

## Reading Order

1. Before changing documentation, read the global knowledge conventions. Prefer
   `/srv/projects/global-knowlegde/docs/foundations/knowledge-conventions.md`
   only after `git -C /srv/projects/global-knowlegde fetch origin main`
   succeeds, `git -C /srv/projects/global-knowlegde branch --show-current`
   returns `main`, and its `HEAD` equals `origin/main`. If the checkout is
   missing or any check fails, read the private repository through authenticated
   GitHub CLI access with `gh api -H 'Accept: application/vnd.github.raw+json' 'repos/Slqzeer/global-knowlegde/contents/docs/foundations/knowledge-conventions.md?ref=main'`.
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
