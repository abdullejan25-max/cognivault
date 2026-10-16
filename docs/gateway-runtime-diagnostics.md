# Gateway runtime diagnostics

`health_report` includes a Memory configuration state and a runtime identity.
These additions help distinguish a disconnected backend from a successful empty
search, and a retained stdio process from a newly constructed Gateway.

| Field | Meaning |
|---|---|
| `memory.configured=false`, `status=not_configured` | No Memory store was bound to this Gateway. Retrieval is unavailable. |
| `memory.configured=true`, `status=not_checked` | A store was bound. Health does not read it, prove schema compatibility or confirm readiness. |
| `runtime.instance_id` | Random identifier fixed for this Gateway instance. A new instance receives another identifier. |
| `runtime.startup_source_sha256` | SHA-256 of bounded package Python source files at construction, or `null` when unavailable. |

The source digest uses sorted package-relative names and bytes. It excludes
configuration, user stores, caches and other non-Python files. Scanning rejects
links/reparse points, hardlinked sources, unreadable subtrees and budget excess:
1024 entries, 128 directories, depth 8, 256 Python files, 512 KiB per file and
8 MiB total. Diagnostics never create or migrate Memory. Fingerprint failure
does not prevent the Gateway from starting.

The digest is a startup disk observation, not authenticated build attestation or
proof of every loaded Python code object. Editing source files does not refresh
an existing instance. Equal digests across two Hosts do not establish equal
private configuration, permissions or data. Verify actual native tool behavior
after a supported Host restart; CLI results cannot stand in for Desktop results.

For `STORAGE_UNAVAILABLE`, first establish the original authoritative Memory
binding from trusted private configuration and engineering receipts. A missing
binding does not prove an old database was lost. Do not point production at a
synthetic demonstration store or initialize a replacement empty database.
After restoring a confirmed binding, use the registered Gateway's bounded
Memory reads, exact-ID version history and source resolution. Controlled
updates require the existing target guard, expected version, idempotency and
review contracts described in [Memory](memory-mvp.md). Read failures remain
unavailable, not successful zero-hit searches.

A client with only the old health fields has not demonstrated this diagnostic
contract. When no supported reload control is exposed to the Agent, retain that
Host's last actual result and report the required normal restart separately.
Never simulate a Host restart with a direct database client or a registration
override.
