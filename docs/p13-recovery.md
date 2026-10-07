# Private Gateway recovery

Recovery is opt-in and uses the existing `admin` capability. Default read-only
Hosts do not discover recovery tools. Data locations come from trusted private
configuration; tools accept a bounded snapshot key, never arbitrary paths.
Keep configuration, snapshots, restores, manifests and receipts outside Git.

`recovery_snapshot_status(snapshot_key=..., expected_manifest_sha256=...)`
provides bounded read-only metadata reconciliation for administrators. Its
`published_unverified` state and `metadata_consistent` flag do not prove payload
integrity; explicitly call `verify_recovery_snapshot(..., restore=false)` for
that proof. Owner remains unknown and automatic resume is unsupported. P16 is
PARTIAL; see the [finite recovery contract](research/p16-finite-recovery-contract.md).

```toml
[recovery]
root = "<ABSOLUTE_PRIVATE_RECOVERY_ROOT>"
sidecar_root = "<ABSOLUTE_PRIVATE_MIGRATION_LEDGER_ROOT>"
exclude_sidecar_paths = ["<REBUILDABLE_RUNTIME>", "<ACTIVE_OPERATOR_TRACE>"]
include_study = true
```

The root must not overlap production data, migration inputs or another Git
checkout. Exclusions are explicit relative paths, recorded in the private
manifest. Original evidence, ledger and durable migration receipts must remain
included. Canonical integrity must already be available for full P13 recovery.

`include_study` defaults to true; existing full snapshots remain compatible.
An explicit false creates a **mutable-domain supplement**, excluding Study and
its QMD index/config while retaining configured History, Assets/Documents/Wrong
Answers and the selected private ledger/receipt sidecar. Both the manifest and
native response identify this scope. Its isolated restore reports Study as
`excluded_by_configuration`, never as a successful Study read. It must accompany
a separately verified full Study snapshot; it cannot replace one. This allows
late source acquisitions to be protected without another full static Study copy.
Capacity is checked for both the additional backup and restore plus the reserve.

Use actual configured native MCP tools:

1. `recovery_snapshot_plan` returns component sizes and available capacity.
2. `create_recovery_snapshot(snapshot_key=...)` captures committed SQLite WAL,
   configured Study files, Assets objects/metadata, History sources/canonical
   records, migration sidecar and explicit Gateway/QMD configuration.
3. `verify_recovery_snapshot(snapshot_key=..., restore=true)` checks the snapshot
   again and restores to a distinct target. It verifies every file checksum,
   database schema/row multiset, source/canonical identity and provenance, ledger,
   and the isolated Study no-result query. Production is never overwritten.

The plan separately reports published snapshot allocation and the additional
restore budget. That budget includes the full payload copy, disposable QMD
index/cache, a writable-index WAL estimate, and a reserve of at least 4 GiB or
10% of payload size, whichever is larger. It checks the system temporary volume
as well as the configured restore volume. Existing restore targets budget no
second payload copy, but still require temporary space and the reserve.

Restore readback uses the isolated Gateway's formal Asset, Document and Wrong
Answer APIs after whole-table logical digest verification. Aggregate states
distinguish a retrieved object/version, source-only evidence, empty domains and
unconfigured domains. An empty collection never claims an analysis-version
readback. Candidate IDs and content remain private.

Snapshot creation reserves database writers in deterministic order, recomputes
WAL-aware capacity, compares inventory and rehashes source files before publish.
Large BLOBs are streamed. Manifests have a bounded size and a separate immutable
catalog checksum anchor; only a complete verified snapshot is published. These
checksums establish integrity under the trusted local owner, not authentication
against an attacker controlling both the artifact and catalog.

Recovery MCP calls run outside the protocol event loop so long copies and
integrity checks leave native ping requests responsive. A transport interruption
is not proof that the operation failed or completed: use a fresh configured
Host to verify the snapshot key. Never start a concurrent retry while the
original Gateway process is still running. Cancellation of the MCP await does
not stop an already running worker: it may retain database reservations and
finish publishing. Recovery does not claim cooperative cancellation support.
Partial attempts remain private.

Reusing a key re-verifies its complete artifact. Restore receipts do not replace
readback: existing targets are checked again. Unrelated populated restore targets
are refused. Failure evidence is retained; a failed partial target is never
silently overwritten or deleted. Retain the failed attempt and use a new key
after correcting the cause. This API has no deletion operation.

Isolated Study proof requires the configured disposable `qmd_runtime`, its
approved software dependencies, and archived config/index. The derived restore
configuration uses restored Study/config/index and private disposable QMD state.
Legacy ambient `qmd_executable` mode cannot establish isolation and is refused.
Database modules unsupported by the verifier fail closed; no skipped table is
reported as a verified count. Immutable evidence and original configs survive.

Synthetic tests are engineering evidence. Production acceptance requires actual
native Host calls, positive Agent usage/tool traces, durable private receipts,
and returned verification states. Never inspect V2 DBs or backup stores directly
from an Agent. A backup does not itself authorize V1 retirement or deletion.


## Final acquired-data recovery closure

The final bounded archive set is reconciled before the deletion decision. Native
mutable backup/isolated restore validates 247 files / 1,049,088,134 bytes,
1,804 sources/outcomes, 487 conversations, 7,334 messages and 1,351 source-only
records. Whole digests, source provenance and ledger equality pass. Formal restored
readback verifies 198 Assets, 29 Documents and 6 Wrong Answer sources / 7 analysis
versions, including the known marked production fixture. This supplement explicitly
excludes Study and accompanies the retained full 3,778-file verified Study backup.
It is an isolated verification copy, not another authoritative data layer.
