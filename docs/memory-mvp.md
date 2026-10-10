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
- `revise_memory`: memory_id, value, source_refs, expected_version, idempotency_key.
  Appends atomically with provenance, audit and request receipt; stale concurrent
  versions conflict. Identical revisions create no additional version.
- `retire_memory`: same fields; value records the retirement reason. Invalidated
  assertions disappear from current search, preserving prior versions.
- `fetch_memory`: latest version, or optional explicit version (historical).
- `memory_versions`: bounded reverse version history, limit <=20, offset <=1000.
- `search_memory`: literal search of subject, predicate, current value and source
  IDs; active latest versions only, limit <=20. No embeddings or semantic aliases.

Reusing an idempotency key with changed content is CONFLICT. Replaying an older
request returns its original receipt, with `is_current:false` after revision.
Write-only clients receive a restricted `receipt_only` response without another
actor's provenance or times. Reads use SQLite mode=ro, never create/migrate files.
Schema initialization is distinct from the atomic assertion transaction; failures
can leave empty initialized schema, never a partial fact/version/provenance.
Old PR #10 records are readable without migration, defaulting to unverified.

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
