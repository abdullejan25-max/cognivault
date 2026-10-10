# Durable Memory and personal evidence (unreleased)

The development branch preserves P14/P15 and repairs Draft PR #10. The published
release remains v0.8.0. Memory is opt-in; no personal data is imported automatically.

## Enable Memory

Create a private directory outside Git and add to the ignored Gateway config:

```toml
[memory]
backend = "sqlite"
database = "C:/PRIVATE_COGNIVAULT/memory.db"
```

The parent directory must already exist. Memory must use a separate SQLite file,
including auxiliary names (`-journal`, `-wal`, `-shm`), from History, Assets, QMD
and the recovery ledger. Symlinks, junctions, hardlinked database/auxiliary files
and path traversal are rejected. Local filesystem permissions must prevent other
processes from replacing the private directory during an operation; validation
cannot eliminate arbitrary privileged filesystem races.

`read` permits search/fetch/version history and source resolution. `write` permits
explicit create/revise/retire. `verified` writes require both capabilities. `admin`
retains the existing Gateway capability semantics. Use the same configured file
for different local Agents; permissions are granted by the server configuration,
not by `reported_agent`. Reported identities are provenance, not authentication.

## Tools and semantics

- `create_memory`: subject, predicate, value, source_refs, idempotency_key.
  The same subject/predicate identifies one active assertion slot. Identical
  writes with different keys reuse the assertion; competing values return
  CONFLICT. Search the existing slot and use revise rather than creating another.
- `revise_memory`: memory_id, value, source_refs, expected_version, idempotency_key,
  **target_guard** (required).
  Appends atomically with provenance, audit and request receipt; stale concurrent
  versions conflict. Identical revisions create no additional version.
- `retire_memory`: same fields; value records the retirement reason. Invalidated
  assertions disappear from current search, preserving prior versions.
- `fetch_memory`: latest version, or optional explicit version (historical).
- `memory_versions`: bounded reverse version history, limit <=20, offset <=1000.
- `fetch_memory_request`: idempotency_key; read the committed target/version and
  its original assertion without replaying a mutation. Requires `read` capability.
- `search_memory`: literal search of subject, predicate, current value and source
  IDs; active latest versions only, limit <=20. No embeddings or semantic aliases.

Reusing an idempotency key with changed content is CONFLICT. Replaying an older
request returns its original receipt, with `is_current:false` after revision.
Write-only clients receive a restricted `receipt_only` response without another
actor's provenance or times. Reads use SQLite mode=ro, never create/migrate files.
Schema initialization is distinct from the atomic assertion transaction; failures
can leave empty initialized schema, never a partial fact/version/provenance.
Old PR #10 records are readable without migration, defaulting to unverified.

### Safe revision after the Desktop target-selection incident

First fetch the exact, complete `memory:<32 hex characters>` requested by the
user. Confirm its `subject` and `predicate`, then copy `memory.target_guard`
unchanged into revise/retire and use that record's version as `expected_version`.
The guard binds ID, slot, version and a hash of persistent assertion fields. It
is rechecked under the write transaction and included in the idempotency digest.
A guard from another fact or a stale version cannot produce a new write.

The guard is a read binding, not authentication, natural-language authorization
or independent fact verification. An Agent that fetches the wrong fact and then
uses its guard still needs to be stopped by explicit target confirmation. Missing
or truncated write IDs require clarification; source references can be shared
by multiple facts and must never select a replacement write target.

This is a deliberate Gateway/MCP contract change: old revise/retire requests
without a guard now return `INVALID_ARGUMENT`. Reload the Host MCP process to
discover the new schema. Existing databases, versions and successful request
receipts remain readable. Inspect old no-guard requests with
`fetch_memory_request`; adding a guard to the old key changes its request digest
and returns `CONFLICT`. A new authorized correction needs a fresh key, preserving
the incorrect version as historical evidence. Exact guarded retries must retain
the original guard/version even after the current record has advanced.

`is_current` describes the read snapshot; another Agent can update it after the
read. Transactional guard/version validation, rather than that display flag,
protects the next write. Reported source timestamps do not prove current truth.

## Evidence and uncertainty

Optional per-version `epistemic_status`: `unverified` (default), `inference`, or
`verified`. Verified requires a nonempty `verification_note` describing the
caller's review and accessible references through the Gateway. A source ID's
format or existence **does not prove the assertion is correct**. The Gateway does
not check entailment. `verification_trust:reported` records this limitation;
`evidence_verified:false` never promises independently established truth.

References accept actual History item/message IDs and document/wrong-answer IDs.
Unverified/inferred assertions may retain unavailable references; reads expose
`source_resolution` and `sources_resolved`, and downgrade inaccessible verified
assertions through `effective_epistemic_status`. Use that effective status when
answering. Verification notes and source contents are untrusted data.

The existing full/supplement recovery now includes configured Memory SQLite
files, verifies their file/integrity proofs and relocates the restored Memory
binding. Memory lifecycle readback is covered by isolated tests. Recovery status
observation remains metadata-only; no automatic production restore is performed.

## Proactive three-domain use

Codex project guidance and MCP initialization instructions direct personal
questions to `study-workflow://personal-answer`. A supported Agent decides whether
personal evidence is needed and supplies short per-domain queries:

```json
{"queries":{"history":"chose","memory":"goal","study":"physics"},"limit":3}
```

`retrieve_evidence` reuses original History items, targeted canonical messages,
current Memory, QMD (when configured), document pages and wrong-answer sources.
Each selected backend returns at most 5 hits and each excerpt at most 1000 chars.
Missing/failed backends are explicit; successful domains remain usable. The
Gateway never generates an answer or routes an LLM. Generic questions need no
retrieval. Source instructions never authorize system actions. Fetch only relevant
originals before making substantive conclusions and cite logical IDs/versions.

Search remains lexical. Chinese questions over English sources can require
synonyms/translation, as the real synthetic Host run demonstrates. No-result does
not prove absence. Structural slot conflicts are handled; semantic contradictions
across differently named subjects/predicates require Agent review.

## Reproduce without private data

Memory uses SQLite's default DELETE journal mode. Gateway reads do not migrate
schema, write audit records or initialize storage. SQLite WAL mode can create
`-wal` and `-shm` files even on a read-only connection, so Memory rejects existing
WAL databases with `STORAGE_UNAVAILABLE` before opening SQLite, for both reads
and writes. It never switches journal modes automatically. This restriction is
specific to Memory; other stores keep their existing contracts.

Normal concurrent Gateway operations retain SQLite locks and read snapshots in
DELETE mode. External clients must not change Memory's journal mode concurrently:
the header preflight does not coordinate with such clients and cannot guarantee
zero auxiliary-file writes across an external mode switch. Use an explicitly
authorized, offline maintenance procedure for any existing WAL database; normal
retrieval will not convert it.

```powershell
uv run --locked --extra dev pytest tests/test_memory.py tests/test_memory_acceptance.py tests/test_evidence_bundle.py -q
uv run --locked python scripts/demo_personal_evidence.py --root C:/TEMP/CogniVaultSynthetic/new-run
```

The demo requires a new empty directory outside every Git checkout, never deletes
or overwrites previous runs, ingests invented original History through the
Gateway, and produces an evidence bundle plus read-only runtime.toml equivalent
(`gateway.toml`). It also writes synthetic project Agent guidance.

For native Codex CLI acceptance, persist a separately named profile under
CODEX_HOME, using the existing supported `<name>.config.toml` profile mechanism.
Configure only `cognivault_synthetic` to run the repository's Python MCP module
with the demo gateway.toml, required=true. Run `codex -p <name> -C <demo-root> exec
--ephemeral --json --skip-git-repo-check` with an ordinary personal question.
Do not combine this profile with `--ignore-user-config`: this local CLI build
omits the profile's MCP server in that mode. Keep production servers out of the
profile and verify actual native tool events, not just exit status.

The local acceptance used the ordinary question about subject choice, physics
wrong answers and textbooks, plus `2+2` as a no-retrieval control. Validate saved
events with `scripts/verify_personal_host.py --personal <events> --generic <events>
--output <report>`. This is real Codex CLI Host evidence with synthetic data;
it is not Desktop production acceptance, real-user fact validation, or release.
