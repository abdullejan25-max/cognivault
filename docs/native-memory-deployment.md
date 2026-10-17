# Native Memory first deployment and recovery

Native Memory reuses the existing SQLite assertion store, immutable versions,
request receipts, source resolution and reviewed History candidates. It does not
replace History archives or turn every message into a fact.

An unconfigured Gateway and a configured but missing Memory database are both
unavailable. A successful empty search requires an accessible initialized store;
it is not evidence that an unknown historical database never existed.

## First deployment

Establish the original binding and recovery evidence before adopting any old
database. When old deployment identity remains uncertain, prepare a separate,
previously unused private directory and explicitly label it as first deployment.
Retain the old configuration, snapshots, source records and future reconciliation
plan. Never bind a synthetic fixture as production or overwrite an unknown file.

Configure the existing private `[memory]` section as described in
[Memory](memory-mvp.md). The parent directory must already exist. With a normal
persistent registration and explicitly granted maintenance `admin` capability,
call `initialize_memory({})`. Paths are fixed by configuration, not tool input.
Initialization reserves the database and its SQLite auxiliary names exclusively;
any pre-existing entry causes a conflict. Failed partial creation is retained
and must be investigated rather than blindly retried or deleted. Empty reserved
WAL/shared-memory files can remain; they do not switch the database to WAL mode.

Initialization creates schema only, not personal assertions. Success is not old
Memory restoration. Subsequent writes use existing create/revise/retire and
candidate-review tools. Unknown identity, undated or third-party statements,
considered choices and model inferences must not become confirmed current facts.
Approval remains reported review, not independent truth or proof of currency.

## Integrity and recovery gates

`verify_memory_integrity({})` requires `read`. It never initializes or migrates
storage. The result separates `complete` from `verified`; incomplete or failed
checks cannot report verified. It checks native schema constraints and immutable
triggers, SQLite structure/foreign keys, version/provenance/request relationships,
and candidate/review/audit structure and bindings. Verification uses a read
snapshot and finite row/instruction budgets. It returns aggregate diagnostics,
not fact text, identifiers or private paths. A structurally different legacy
schema requires separate compatibility review rather than automatic repair.

Structural integrity does not establish that a statement is true, still current
or supported by its source. Use bounded original-source reads and the existing
Memory evidence assessments for those questions. Competing source dates, semantic
links and target guards keep their existing contracts.

Request-key prefixes are not reserved. Candidate receipt checks cover keys that
match an approved candidate; the existing schema cannot authenticate whether an
ordinary caller deliberately reused that exact key. A successful structural
check is not authentication of the caller or the origin of an idempotency key.

Take and verify a Gateway recovery snapshot before changing bindings, then take
another snapshot including configured Memory after deployment. Perform isolated
restore through the Gateway and compare original logical IDs, versions and
provenance. Old snapshots must be verified with their original matching component
configuration; a new Memory binding does not retroactively add it to old backups.
Do not restore over live production or remove old source evidence.

Withdraw maintenance `admin` after the initialization/review/recovery window.
Normal readers cannot discover or call initialization; ordinary writers cannot
approve candidates. Review and maintenance require a fresh Host that loaded the
intended configured capabilities. Local filesystem access must protect the private
directory: SQLite's path-based journal operations are not an operating-system
sandbox against malicious processes running as the same user.

## Host and learning acceptance

Verify actual native health and tools after a normal Host reload. Keep Desktop,
CLI, WorkBuddy and Hermes evidence separate. An ordinary natural-language task
must cause the Agent to choose retrieval; a scripted tool call alone does not
prove proactive retrieval. Generic questions need no personal search.

Preserve each dated student attempt as distinct original evidence. Revising an
Agent's analysis is not another student attempt, and an absent retest is not
improvement. Synthetic chronology/conflict/rollback checks establish software
behavior only. Real learning progress requires later real user work.
