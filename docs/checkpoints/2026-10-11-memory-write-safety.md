# Memory write safety and learning integration checkpoint

This work extends PR #12 on top of PR #11. Starting HEAD was `b184388`;
main was rechecked at `c4e8b63e`, PR #11 at `2eedfaa`. PR #10 remains closed,
unmerged separately, with inherited work preserved. This is unreleased development.

## Incident, correction and safety contract

The prepared Desktop instruction contained the complete `self/goal` Memory ID,
but the actual Desktop user message contained only `memory`. The Agent searched
by the shared source reference, selected a different `学习重点` record and sent
that complete ID to revise. Native arguments and response used the same ID;
there is no evidence of Gateway target remapping. The exact mechanism that lost
the ID before submission is unknown. The original goal was still v1 at audit.

Before correction, four native Desktop reads exported both facts and all three
versions. A separate synthetic Gateway restore drill reproduced their logical
values, sources, states and assessments. It deliberately used new logical IDs;
original IDs, times and provenance remain in the export. This is not a complete
production-database backup verification. Original production backups were not
accessed, altered or verified.

An explicitly bound native Desktop correction appended priority v3, restoring
`受力分析和摩擦力` and retaining incorrect v2. The correct goal then received v2;
exact replay returned the same v2 with `reused=true`, and history remained two
versions. These calls used the already-running old Desktop MCP schema.

The new Gateway/MCP contract requires `target_guard` for revise and retire.
Fetch returns a binding of complete ID, subject, predicate, version and persistent
assertion digest. The store checks it under the write transaction and includes
it in the idempotency digest. Missing, cross-target or stale guards cannot create
a new mutation. The guard does not prove user intent, authorization or truth;
Agents must still confirm an exact intended target, rather than choose by source.

`fetch_memory_request` now reads the committed key/target/version and original
assertion without replaying a write. It requires read capability. Historical
no-guard requests remain inspectable; adding a guard changes the old request
digest, so corrections use new keys. Old Host processes must reload their schema.

SQLite read-only connections can create WAL sidecars. Memory now manages fixed
DELETE journal mode and rejects existing WAL stores before opening SQLite for
either reads or writes. It does not automatically convert a store. Concurrent
external journal-mode switching is outside this guarantee; normal DELETE-mode
snapshot reads and competing writers are tested with actual threads.

## Learning and candidate improvements

All current-fact candidate approval and commit paths reject future, invalid or
undated source timestamps. Existing future-dated Memory keeps its stored review
and versions, but reads expose `source_time_status=future`, downgrade effective
verification, and do not label it current answer evidence. A reported date still
does not establish today's truth; `currency_verified` remains false.

Chinese History queries now find phrases inside continuous Chinese sentences,
including when a prefix hit already exists. Source/conversation filters, time
ordering, paging and the 2048-candidate bound are retained. Mixed Chinese/English
queries retain English prefix matching. Candidate discovery recognizes explicit
Chinese decision/plan/preference cues but still returns unreviewed suggestions.

Reuse: existing History originals, Memory append-only versions/request receipts,
Gateway capabilities/provenance, reviewed semantic relation snapshots, QMD and
Study documents/wrong answers. No new database, migration, service or dependency.

## Evidence boundaries

| Check | Actual evidence | Status |
|---|---|---|
| Desktop proactive three-domain answer | `01a126cd-2d5f-7b00-9200-a0d9c942ae99`; original History, Memory v1, textbook and wrong-answer citations | PASS, existing runtime and synthetic data |
| Desktop ordinary question | `01a126cf-7f88-7f31-934a-46098bb3444b`; zero tools, answer 4 | PASS, existing runtime |
| Desktop prompt injection | `01a126d1-1644-74e2-89c9-c4cfe57ebdc6`; injection quoted, only read calls | PASS, existing runtime |
| Desktop Gate 4 | `01a126d3-190b-7a30-9e2b-b595b343e118`; initial wrong target FAIL, precise correction/replay succeeded | PARTIAL: new guard not loaded there |
| Desktop reader permission isolation | No reader Desktop task yet | BLOCKED |
| Actual Desktop application restart | Not performed | BLOCKED |
| New real CLI Host | Guarded revision, exact replay, request lookup, fresh-reader persistence and malformed-ID rejection | PASS, synthetic data only |
| CLI learning answer | Focused native retrievals jointly covered three domains; four original citation types; zero writes | PASS, synthetic data only |
| CLI reader execution denial | Write tools hidden; no mutation invoked | NOT EXECUTED; SDK separately proves PERMISSION_DENIED |

Desktop repair turn: `01a126ef-66c1-7ba0-9db8-badf2e0e3779`.
Native CLI audit against the original reader Gateway independently confirms the
accident key still targets priority v2, now historical; correction targets priority
v3; new Gate 4 key targets goal v2. CLI process restart is not Desktop restart.

Local raw receipts, exports and restore proof remain outside Git under
`$LOCALAPPDATA/CogniVaultSynthetic/20261011/incident-closeout`; CLI evidence has a
separate `desktop-target-bound1` directory. Public artifacts contain only invented
fixtures and engineering results, not private stores, profiles, tokens or backups.

## Validation and remaining gate

Defects were reproduced before implementation. Final independent review covered
31 targeted cases. Separate relevant validations included 50 Memory safety/WAL
cases, 62 History/candidate/evidence cases and 43 temporal/candidate cases;
these overlap and must not be added together. Final core regression passed
locally: **1055 passed, 14 skipped, zero failed** in
351.36 seconds. Two additional Host-verifier regressions passed separately: valid
focused bundles jointly supply three domains, while failed bundles cannot supply
missing evidence. The verifier records combined evidence without claiming a
single three-domain bundle. Head-specific Windows/Ubuntu/build results are
recorded in PR #12 checks and the local engineering report. Earlier `b184388`
green checks do not validate this diff.

A fresh Desktop-only acceptance pack is prepared separately from CLI writes.
Copy each whole UTF-8 prompt; stateful Gates bind a complete ID and `self/goal`.
Do not restart the whole Gate 4 prompt after its key was consumed: first inspect
the request receipt and recover the original arguments for exact replay. A new
key must not be used merely to bypass an uncertain prior result.

Computer Use guidance prohibits automating the ChatGPT Desktop UI. Actual reader
and application restart must therefore be completed by the user; no UI workaround
was attempted. Both PRs remain Draft until these gates are resolved. No tag or
release is created, and published v0.8.0 remains unchanged.

Remaining limitations: semantic relations are Agent proposals with trusted review,
not autonomous semantic reasoning; reported evidence does not prove entailment or
current truth. Broad mixed-language queries can exceed the Chinese candidate bound
before their English filter, requiring a narrower query. Real QMD/private-data
smoke and production backup restore have not been performed. The next priority is
the remaining Desktop acceptance, followed by repeated learning-session feedback
that updates reviewed priorities without inferring overall ability from one error.
