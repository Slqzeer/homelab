# Ticket 09 — Add Encrypted Off-host Backups

**Epic:** Outline platform
**Status:** Planned
**Depends on:** Tickets 07 and 08
**Spec:** [Outline platform and migration design](../../specs/2026-10-13-outline-platform-and-migration-design.md)

## Outcome

Replicate complete local recovery sets to an encrypted off-host repository and
prove restoration without access to `/srv` or `/backups`.

Backblaze B2 plus restic is the preferred candidate, but selection remains
conditional on fresh cost, location, retention, immutability, and restore tests.

## Scope

- Compare Backblaze B2 with at least one credible alternative such as Hetzner
  Storage Box against the spec's acceptance criteria.
- Select a provider and document account/data location, pricing assumptions,
  export path, and failure model.
- Create a provider-scoped backup identity distinct from account administration.
- Encrypt and deduplicate recovery sets with restic or an equivalently proven
  tool.
- Store the repository password in Vault plus a separate offline recovery copy.
- Automate replication, retention checks, integrity checks, and stale/failure
  alerts.
- Validate Object Lock or equivalent immutability only when compatible with
  repository locks, prune, and retention.
- Restore a seeded recovery point with both homelab disks treated as unavailable.

## Out of scope

- GitHub repositories, LFS, releases, or Actions artifacts as storage.
- Using the off-host bucket directly as Outline attachment storage.
- Storing provider master credentials in the cluster.

## Expected files and systems

- Create `apps/outline/operations/replicate-outline-backups.sh`.
- Create scheduler templates beside the local backup operation.
- Extend `tests/test_outline_backup.py`.
- Modify `platform/vault/configure-vault.sh` and least-privilege projection
  ownership as required.
- Update `docs/runbooks/outline-recovery.md` with provider, credentials,
  retention, integrity, restore, and exit procedures.

## Implementation checklist

- [ ] Record a provider decision table with current evidence and choose the
  lowest-operational-burden option that satisfies the full restore contract.
- [ ] Add failing tests for source-set validation, encryption, scoped credential
  use, no secret in argv/logs, retry safety, retention, and stale alerts.
- [ ] Create the provider bucket/account boundary and least-privilege key; keep
  administrative credentials out of workloads.
- [ ] Initialize the encrypted repository and establish the offline recovery-key
  ceremony.
- [ ] Replicate only complete Ticket 07 recovery sets and run integrity checks.
- [ ] Validate immutability against normal lock cleanup and prune on disposable
  snapshots before enabling it for retained data.
- [ ] Simulate loss of `/srv` and `/backups`, restore the seeded set, and repeat
  Ticket 08's data/application checks.
- [ ] Record cost, duration, throughput, and dated restore evidence.

## Acceptance

- A compromised Outline pod cannot read provider credentials or delete backups.
- A compromised routine backup identity cannot administer unrelated storage.
- Off-host data is client-side encrypted and unusable without separately held
  key material.
- Backup freshness over 24 hours, failed replication, and integrity-check failure
  alert.
- A complete restore succeeds without either homelab data disk and within four
  hours.
- Provider exit/export is documented and tested on at least one recovery point.

## Rollback or abandonment

Disable replication before revoking its provider key. Preserve remote snapshots
until a replacement provider has an accepted restore. If B2 fails acceptance,
record why and select the next evaluated provider rather than weakening the
requirements.
