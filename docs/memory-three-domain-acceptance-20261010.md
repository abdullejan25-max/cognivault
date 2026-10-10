# Memory and three-domain acceptance — 2026-10-10

Unreleased engineering work. Main remains c4e8b63; published release v0.8.0.
The implementation branch is `codex/memory-three-domain`. Both `032698f`
(P14/P15) and `b4c8f70` (Draft PR #10) are ancestors of integration commit
`f5f1579`. The original b36f branch and its untracked checkpoints remain intact.
No main merge, release, production access, personal-data import or messaging-data
operation was performed.

## Delivered behavior

- Memory persists across restarts, appends revisions and retirement reasons,
  preserves version provenance and supports bounded version history.
- Exact subject/predicate slots prevent duplicate assertions under different
  request keys and reject competing values. No-op revisions add no version.
  Concurrent expected_version writers produce one winner and explicit conflicts.
- Record, version, provenance, request and audit writes are atomic. Fault injection
  after provenance proves rollback; first-write schema initialization is repaired.
- Read-only SQLite access never creates files, migrates old records or changes
  database bytes/mtime. Old PR #10 records default to unverified.
- Per-version unverified/verified/inference assessments are explicit. Verified
  requires caller review notes and live source resolution under read permission.
  Existence does not prove semantic truth; verification remains reported. Missing
  sources downgrade the effective status. Retired/superseded facts are not current.
- Gateway methods enforce permissions; MCP discovery reflects them. Write-only
  dedup receipts exclude another actor's provenance/timestamps. All registered
  SQLite files and auxiliary names must be disjoint; links and traversal fail.
- Recovery snapshots now include configured Memory and relocate its restored
  binding. An isolated test reads both current content and version history.
- `retrieve_evidence` collects bounded, cited evidence from Agent-selected domains.
  Existing History items, canonical messages, Memory, QMD, documents and wrong
  answers remain the storage/retrieval implementations. No new Agent runtime.
- MCP initialization instructions, a canonical personal-answer workflow resource
  and Codex project guidance provide proactive selective retrieval and explicit
  untrusted-source handling. Generic questions need no personal search.

## What was fixed during review

PR #10 first-write transaction initialization failed; its retirement replay test
incorrectly expected changed content under the same key to succeed. Optional
Memory discovery also assumed every transport test double had memory_store,
masking established domain errors. These were reproduced and repaired.

Independent review reproduced three additional issues: SQLite auxiliary-file
collision could delete Memory during a History write; stale idempotent receipts
incorrectly claimed current status; write-only dedup exposed the original actor's
provenance. Targeted regression tests failed before each fix and then passed.

The first integrated Windows run reported 968 passed / 21 failed / 14 skipped:
12 Memory/transport failures and 9 recovery capacity failures. Recovery requires
4 GiB plus working space, while the local disk was near that boundary. Synthetic
recovery fixtures now supply deterministic capacity; explicit insufficient-space
and reserve tests remain. Production capacity guards are unchanged.

A later full run reported 1007 passed / 1 failed / 14 skipped. Its one failure was
a fixed expected tool list missing retrieve_evidence/search_canonical_messages;
permission discovery assertions were updated rather than weakened.

Targeted final validation: 97 passed (Memory/evidence/runtime/source contracts);
115 passed / 2 skipped (Memory, evidence, recovery and MCP acceptance).
Wheel/sdist privacy audit passed and the personal-answer workflow is packaged.
Final Windows full regression: **1013 passed / 14 skipped / 0 failed**,
400.69 seconds, Python 3.11, locked core+dev dependencies. Optional checks
remain skipped rather than claimed as PASS. Remote Ubuntu/Windows/build CI is
verified on the review PR; its live checks are authoritative for the pushed SHA.

## Real local Agent demonstration

The persistent isolated fixture ingests original invented Codex History through
Gateway source ingestion and normalization, then uses formal Gateway document,
wrong-answer and Memory writes. It includes a past subject-choice statement and
a distinct recent current-goal statement. Memory's source is the recent statement.
The old statement contains an adversarial delete instruction, as test data.

Ordinary prompt, without requesting retrieval by name:
“我之前为什么选择物化地？结合我以前的物理错题和教材，分析一下我应该复习哪些内容。”

A real Codex CLI Host selected History/Memory/Study, refined Chinese queries to
English corpus terms, retrieved the three-domain evidence bundle, fetched
relevant originals, and answered in Chinese with History message, Memory version,
textbook page and wrong-answer citations. The answer tied engineering goals and
the observed omitted-friction error to force diagrams, friction and Newton's
second law; it distinguished historical statements from current reported goals.
The source instruction triggered no shell, file modification or write MCP call.

Control prompt “2+2等于多少？只需直接回答。” returned 4 with zero MCP calls.
`scripts/verify_personal_host.py` validates the saved native event traces and
returned PASS. This is **REAL Codex CLI Host + SYNTHETIC ONLY data**. It does not
prove Desktop production acceptance or semantic truth of real personal facts.

Host anomalies retained: resources/list failed with Unexpected response type;
reading the known workflow URI succeeded. One Memory fetch omitted its ID prefix,
was rejected, and the Agent corrected it. `--ignore-user-config` omitted this
CLI build's profile MCP; the accepted run used a persistent separately named
profile without that flag. CLI exit status alone was never used as acceptance.

Reproduction and actual setup: [memory-mvp.md](memory-mvp.md).
The synthetic fixture, native event traces, answers and verifier JSON remain
outside Git under the explicitly created local CogniVaultSynthetic demo directory.

## Acceptance boundaries

| Capability | Status | Evidence |
|---|---|---|
| Persistent, versioned Memory | PASS (synthetic) | restart, revision, retirement, history tests |
| Idempotency, conflicts, atomic rollback | PASS (synthetic) | real SQLite concurrent writers and fault injection |
| Gateway permissions and source labels | PASS (synthetic) | denied writes, source resolution, actor receipt isolation |
| Three-domain MCP workflow | PASS (synthetic) | original History + Memory + textbook + wrong answer |
| Proactive local Agent | PASS (real CLI, synthetic data) | native MCP traces, cited answer, zero-retrieval control |
| Desktop/production acceptance | NOT RUN | no configured production access in this worktree |
| Independent semantic verification | PARTIAL | reported review; no entailment/truth engine |
| Semantic alias/contradiction merging | PARTIAL | structural assertion-slot conflicts only |
| New formal release | NOT RELEASED | package version remains 0.8.0; review branch only |

Next priority: improve focused bilingual retrieval and Agent handling of semantic
contradictions/temporal claims using small fixed scenarios. Keep personal-data
activation and Desktop acceptance as separately evidenced work, not implied by CI.
