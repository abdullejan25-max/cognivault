# Deterministic canonical History

Source evidence is immutable and remains recoverable as exact original bytes.
Canonical history is a derived layer in the existing configured History SQLite
database. It does not replace old History APIs or authoritative Memory.

## Confidence and source contracts

`exact` requires explicit boundary, role, source ordering and reliable occurrence
time. `derived-safe` permits deterministically mapped structure with an unknown
optional timestamp. It never substitutes import/normalization time. Unknown roles,
conflicting native identities, incomplete messages and unproven content structures
remain source-only, with counted ambiguity. A malformed structured source produces
no partial canonical writes. Non-message runtime events are counted separately.

- Codex: JSONL `session_meta` and explicit `response_item` messages; physical
  line/byte locators preserve source order.
- WorkBuddy: native JSONL `sessionId` and explicit roles. Legacy/private format
  status is declared. Integer timestamps without a proven unit remain ambiguous.
  Hook archives and roleless tool/runtime records are not reconstructed as chat.
- Hermes: explicit session identities and ordered message arrays; the local
  seconds timestamp contract is separate from other adapters.
- ChatGPT: JSON or ZIP `conversations.json`, consistent mapping graph, all branches
  and null/hidden nodes preserved. No main branch is guessed. Real export validation
  is pending acquisition; synthetic support is not a real-data PASS.
- Gemini activity archives, legacy Markdown/facts/DBs and runtime metadata retain
  source-only status when conversation structure cannot be proved.

Original multimodal parts remain evidence-backed. Unmaterialized attachments have
explicit unresolved metadata; no guessed file lookup, network fetch or invented
Asset ref is used. Existing source/Asset evidence remains intact.

## Identity, provenance and ordering

Conversation IDs use source system and native conversation identity. Message IDs
use that conversation, native message identity (or stable source-defined position)
and exact content/time assertions. Private paths and migration clocks are excluded.
Changed payloads remain distinct variants. Multiple exports/snapshots share records
but retain independent source-specific views; their sequences are never conflated.

Canonical provenance declares `deterministic_derived`, version/method, source ID,
fingerprint, imported_at, normalized_at, occurrence quality and locators. First
provenance/time remains stable on replay. Additional evidence is append-only.
Unknown occurrence time is null. Source-defined sequence or parent-child edges
provide ordering; imported_at never determines message order.

## Gateway operations

Use formal MCP tools only for persisted V2 access:

- `history_normalization_snapshot`: pins the immutable source set.
- `normalize_history_sources`: bounded batches, per-source atomic writes/outcomes,
  read+ingest capabilities, durable private receipts. Source-set changes conflict.
- `canonical_history_summary`: separates records, views, appearances, candidates,
  ambiguous messages and source-only outcomes.
- `search_canonical_conversations`: literal text/category search, bounded results.
- `fetch_canonical_conversation`: independent view/message/node pagination. Tree
  nodes expose parent relationships and child counts; original child lists remain
  in source evidence. Do not flatten branches.
- `fetch_canonical_message`: actual bounded BLOB byte range, saved checksum/size
  and paginated evidence. Decode the canonical JSON after collecting all ranges.
- `verify_canonical_history`: verifies identities, provenance, evidence and counts;
  `reparse=true` compares original-source candidates to every actual derived row.
  The result also contains bounded, privacy-safe `diagnostics` for mismatches in
  source evidence (`count`, `by_field`, up to 20 `samples`, and `truncated`). Each
  sample names the source ID, record class and field, then reports digests and
  encoded byte counts for expected and actual values. It never returns raw source
  content, timestamps, private paths or malformed identifiers.

All tables and provenance have immutable update/delete guards. A rerun adds no
canonical records. Later acquisitions need a new source-set snapshot; completed
source/version outcomes are reused. A transformation change needs a new version,
not replacement of evidence. Tools are additive and do not alter Study, Assets,
Documents, Wrong Answer, Projection or existing History contracts.

Real manifests, sources, receipts, traces, ledger and backups stay outside Git.
Tests are entirely invented. Counts of files, sources, candidates, appearances,
conversations and messages are distinct; none substitutes for completeness proof.
