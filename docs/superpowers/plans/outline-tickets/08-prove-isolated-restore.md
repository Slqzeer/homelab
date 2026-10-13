# Ticket 08 — Prove an Isolated Restore

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 05 and 07
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Restore a seeded local recovery set into an isolated namespace and prove the
complete service, data, permissions, authentication, encryption, search, and
four-hour recovery objective.

## Scope

- Seed known public-to-members, restricted, rejected-decision, and attachment
  fixtures in the source workspace.
- Restore PostgreSQL and attachments in documented order into non-production
  names and storage.
- Recover `SECRET_KEY` through the documented Vault/offline procedure without
  exposing it in evidence.
- Provide a non-production access route that cannot affect the canonical
  hostname or production Keycloak client.
- Validate hierarchy, documents, attachments, users, roles, permissions,
  encrypted state, login/logout, and search.
- Measure elapsed recovery time and clean up only drill resources.

## Out of scope

- Off-host-source restore; Ticket 09 repeats the drill from the remote copy.
- Replacing production state.
- Declaring success from pod readiness alone.

## Expected files and systems

- Create `apps/outline/operations/restore-outline.sh` or an equivalent
  parameterized, non-production-safe restore driver.
- Extend `tests/test_outline_backup.py` with restore safety tests.
- Complete restore sections and an evidence record in
  `docs/runbooks/outline-recovery.md`.

## Implementation checklist

- [ ] Add failing tests that reject the production namespace, database, PVC,
  hostname, and Keycloak client as restore targets.
- [ ] Seed and record checksums/IDs for representative documents and files.
- [ ] Take and validate a fresh recovery set with Ticket 07 tooling.
- [ ] Create isolated namespace, database, Redis, PVC, secrets, and route names.
- [ ] Restore in the documented order and start the pinned Outline release.
- [ ] Verify seeded content, hierarchy, permissions, attachments, encrypted
  state, search, and OIDC using non-production configuration.
- [ ] Record elapsed time, commands, checksums, and pass/fail evidence without
  secret values.
- [ ] Remove the isolated resources and prove production was unchanged.

## Acceptance

- Restore completes in less than four hours.
- Every seeded fixture and attachment matches its expected checksum/content.
- Restricted content remains restricted and the administrator set is correct.
- OIDC login/logout and search work in isolation.
- Restored encrypted state is readable with recovered key material.
- Production namespace, database, PVC, Keycloak client, and ingress show no
  drill mutation.

## Rollback

Abort immediately if any command resolves a production target. Remove only
resources carrying the drill's unique namespace/name labels. Preserve the
source recovery set and failed-drill evidence for diagnosis.
