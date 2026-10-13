# Ticket 01 — Resolve and Pin the Outline Release

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Approved design specification
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Select an official Outline release and immutable image digest, prove that its
configuration and runtime work with this cluster's restrictions, and publish a
release contract that downstream tickets can implement without guessing.

The planning baseline is `v1.10.1`. The selected release may be newer only when
the same checks are rerun and the change is recorded.

## Scope

- Confirm the official image, tag, digest, supported architecture, license, and
  freely self-hosted features needed by this design.
- Enumerate required environment variables and file-backed secret variants.
- Confirm `/auth/oidc.callback`, manual OIDC endpoints, local attachment path,
  PostgreSQL/Redis requirements, WebSocket behavior, and migration behavior.
- Probe the image with non-root execution, dropped capabilities, RuntimeDefault
  seccomp, read-only root filesystem where supported, and writable mounts only
  where required.
- Derive startup, readiness, and liveness checks from observed behavior.
- Measure baseline startup/idle resources and retain the spec's initial limits
  unless evidence requires a documented change.

## Out of scope

- Creating the namespace, database, Redis, Vault secrets, or production
  Deployment.
- Registering a Keycloak client.
- Publishing any ingress.

## Expected files and systems

- Create `apps/outline/README.md` with the release and configuration contract.
- Create `apps/outline/release.env.example` containing names and non-secret
  example values only.
- Add release-contract assertions to `tests/test_application_onboarding.py` or
  a focused `tests/test_outline.py` when clearer.
- Use temporary manifests and files under `/tmp` for runtime probes; do not
  commit generated image contents.

## Implementation checklist

- [ ] Add a failing test that rejects a mutable/tag-only Outline image and
  requires the expected callback, port, storage path, and resource baseline in
  the release contract.
- [ ] Resolve the official image digest for the selected release and verify its
  platform matches the k3s node.
- [ ] Inspect the pinned image and official release notes for configuration,
  schema migrations, breaking changes, and rollback limitations.
- [ ] Run a restricted-security probe using disposable PostgreSQL and Redis
  dependencies; record every required writable path and capability exception.
- [ ] Exercise startup, readiness, liveness, WebSocket upgrade, graceful
  termination, and idle/import/search resource behavior.
- [ ] Write the release contract and make the tests pass.
- [ ] Record the exact verification commands and outputs without credentials.

## Acceptance

- The image is referenced by exact release tag and SHA-256 digest.
- No unofficial Helm chart or mutable tag is introduced.
- The documented callback is exactly
  `https://outline.taildf6cd4.ts.net/auth/oidc.callback`.
- Downstream tickets have exact container port, command, env names, secret-file
  support, writable paths, probes, and initial resource values.
- The image either passes restricted Pod Security or every unavoidable
  exception is explicit and approved before Ticket 02 begins.
- Tests fail when the digest, callback, storage path, or security contract
  regresses.

## Rollback or abandonment

This ticket changes no running service. If the official image cannot satisfy
restricted security or required OIDC behavior without a fork, stop the epic and
record the blocking release evidence; do not silently adopt a community image.
