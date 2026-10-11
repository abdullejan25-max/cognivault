# Source-only History ingestion

This additive layer preserves original source evidence independently of canonical
conversations. It does not infer roles, order, timestamps or conversation boundaries.
Malformed and unsupported inputs can still be preserved byte for byte.

## Configuration and boundary

The existing `[history]` SQLite configuration accepts an optional `migration_inbox`:
an explicitly selected private directory outside Git and outside authoritative
Study/History/Assets storage. Existing configurations remain valid. No directories
are discovered automatically. The inbox and source files reject path traversal,
reparse points, hardlinks, target overlap and changed bytes.

`ingest_history_sources` requires both `ingest` and `read`; its mandatory checksum
readback is a Gateway operation. Read/search/summary/verify tools require `read`.
Actual agents use the configured MCP Host; offline preparation reads legacy inputs
and its migration ledger, never V2 storage files. An unavailable server is not
replaced with a direct database writer or standalone MCP client.

## Manifest version 1

The private UTF-8 JSON manifest has exactly `schema_version: 1` and `entries`.
An entry has `source_system`, `source_format`, `record_kind`, `sha256`, `byte_count`
and exactly one of:

- `relative_path`: unchanged bytes inside the configured inbox;
- `legacy_record_id`: a verified existing legacy source, reused without copying its BLOB;
- `document_uri`: a verified existing Gemini archive manifest, reused with its original
  Asset ranges. This mode validates the manifest and complete archive checksum.

The manifest is addressed by relative path and expected SHA-256. It permits at
most 5,000 entries; each call selects at most 128 using its returned cursor. Entries
are committed independently and every error retains its index and fixed error code.
Interrupted batches can be repeated without changing successful evidence.

## Identity, evidence and receipts

`source-file:` identity hashes the source category and original byte digest. It
does not depend on filenames, absolute paths, import time or reported client.
Exact bytes within one category reuse the original evidence/provenance. Equal
bytes from distinct origins preserve distinct source records and share byte storage
where applicable; this is not an automatic semantic merge.

The immutable evidence records preserve nullable occurrence time separately from
import time. An explicitly supplied occurrence time is normalized to UTC; its
original representation remains in original bytes. Runtime/session metadata keeps
its explicit record kind and is never counted as conversation content.

The Gateway verifies complete source bytes using bounded BLOB reads and persists
each batch receipt under the private inbox. Receipts contain individual destinations,
dispositions, checksums and provenance digests. MCP `compact: true` returns aggregate
counts, error entries and a checksum while retaining all details in that receipt.
The ledger records receipt identity/digest and does not replace current Gateway
readback. Original sources are immutable; subsequent normalization is a separate
derived layer.

New acquisition-ledger `imported` / `reused` evidence uses authority `cognivault`.
Already stored `study_system` authority remains valid as deprecated historical
provenance and is read without rewriting its payload. The rename changes no
schema, source identity, original-byte hash, or existing receipt digest.

Counts of acquired files, legacy records, indexed sources, archive members and
activity entries have distinct meanings. `canonical_messages_created` describes
this operation (always zero), not a query for a future canonical table's total.

## Preparing explicit authorized local sources

`scripts/prepare_authorized_history.py` prepares original-byte snapshots and
manifest references from repeatable `--source platform=absolute_path` arguments
and an existing `--inbox`. Supported preparation labels are `codex`, `workbuddy`,
`hermes`, and `chatgpt`; format support remains subject to normalization contracts.
It does not discover accounts, call a Gateway, read authoritative stores, or import.

Use only explicitly authorized raw input directories and the already configured
private migration inbox. Do not select Study, History, Assets, Memory, backup or
credential stores as inputs. Preparation rejects output/source overlap, links,
reparse points and Git inboxes; it excludes credential names and database files.
Stable sources are read twice and compared before publishing exact-byte snapshots.
Malformed JSONL and changing files are retained at their original locations and
reported as skipped. Limits are 512 MiB per file, 8 GiB total selected bytes,
25,000 traversed entries and 5,000 entries per manifest.

The JSON result returns counts, fixed error codes, manifest relative paths and
SHA-256 values without source text or original absolute paths. `PREPARED_ONLY`
does not establish completeness, ingestion or retrieval success. Inspect skipped
counts. Existing output conflicts are preserved and return a nonzero exit code.
Use the normal native MCP Host for subsequent ingestion, normalization, checksum
verification and independent readback. Matching existing source bytes can still
conflict on declared format, record kind or occurrence-time metadata; reconcile
those fields against formal Gateway evidence instead of inventing replacements.
