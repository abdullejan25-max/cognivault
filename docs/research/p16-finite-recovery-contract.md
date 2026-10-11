# P16 finite recovery status contract

P16 is **PARTIAL**: the first stage provides admin-only, read-only metadata
reconciliation for `recovery_snapshot_status(snapshot_key,
expected_manifest_sha256=None)`. It adds no database, ledger, lease, owner marker,
receipt, publish handler or automatic resume. Existing create and explicit verify
retain their behavior. All evidence is authored synthetic fixtures; production
and native Host acceptance remain UNKNOWN.

The result has `kind=recovery_snapshot`, a validated `recovery://snapshot/<key>`
reference, `state` (`not_observed`, `published_unverified`, `needs_action`, or
`observation_changed`), `metadata_consistent`, `artifact_integrity=not_verified`,
`verification_required`, `expected_manifest_match` (`not_requested`, `matches`,
`mismatch`, `unavailable`), `operation_binding=unavailable`, `owner.state=unknown`,
`resume.supported=false`, `resume.reason=NO_FENCED_RESUME_HANDLER`, bounded
`partial_observation.count/complete`, fixed `reason_codes`, and optionally a
structurally validated canonical `manifest_sha256`. Configuration shape is not
target identity. Expected digest equality is not input or ownership proof.

Only the configured recovery root, snapshots/catalog directories, the requested
final directory, its manifest/checksum and requested catalog metadata are read.
Top-level snapshot discovery is streaming and bounded; partial directories are
never followed. No plan, create, snapshot, verify, payload traversal, history
proof, SQLite connection, directory creation or write occurs. Metadata receives
existing path/reparse/hardlink/Git-root protections, bounded strict JSON parsing,
schema v1 shape and entry checks, and canonical manifest/checksum/catalog digest
comparison. Private source locations and exception text never enter the DTO.

Published self-consistent metadata returns `published_unverified`; corrupt or
missing payload can produce the same result. Artifact integrity requires the
explicit existing `verify_recovery_snapshot(snapshot_key, restore=false)`.
Missing/invalid/mismatched metadata, catalog without publication, partial-only
attempts and expected digest conflicts produce fixed actionable reasons. No
artifact means only not observed, never permission to retry or proof of no worker.
Permission/IO/path/bounds failures remain Gateway errors, not absence.

Metadata bytes and directory identities are observed twice. Detected changes
produce `observation_changed`; this finite observation is not linearizable and
cannot defeat malicious ABA or prove a writer has stopped. Truncated discovery
reports a lower-bound count with `complete=false`. An active worker, stale
heartbeat or process reopen always leaves owner unknown and resume unsupported.

Tests must first fail for the missing API, then prove actual catalog-fsync/rename
failure preserves catalog and partial (including existing same-key retry failure),
published response loss/reopen, duplicate create, paused active workers with
finally release/join, malformed metadata, digest/configuration conflicts, partial
bounds, concurrent changes, strict arguments/admin MCP discovery and denial,
payload corruption versus explicit verifier failure, and byte/file-set read-only
preservation with spies prohibiting payload/SQLite calls. Legacy schema v1 stays
compatible. Automatic resume still requires trusted input/target binding and a
fencing protocol that prevents the old worker from committing.
