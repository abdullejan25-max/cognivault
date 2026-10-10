# History to reviewed Memory

This workflow is opt-in. Ordinary answers only retrieve evidence. It reuses the
existing History Gateway, Memory SQLite database, MCP and versioned Memory writes.
No production migration runs automatically and no new dependency is introduced.

## Actual tools and authority

1. `find_memory_candidates(query="目标", limit=3)` reads bounded History excerpts
   and flags user statements containing lexical goal/preference/change cues.
   Suggestions are untrusted, have unknown confidence and are not persisted.
2. The Agent fetches the original with `fetch_history_item` or
   `fetch_canonical_message`, interprets the claim and proposes an exact quote:

   ```json
   {"source_ref":"message:<64 hex>","quote":"目前复习重点是受力分析和摩擦力。",
    "subject":"self","predicate":"学习重点","value":"受力分析和摩擦力",
    "classification":"fact_update","confidence":0.9,
    "basis":"近期用户明确报告学习重点；不是独立能力测评"}
   ```

   Call `propose_memory_candidate` with read+write permission. It records original
   source ID, original hash/time/role, exact Gateway-visible quote, reported
   confidence, extraction basis, interpretation and a bounded existing-slot
   version snapshot. The same proposal is reused, including its first snapshot.
3. `list_memory_candidates(limit=5)` reads proposals and immutable final reviews.
   Pending/rejected proposals never appear in `search_memory` or answer evidence.
4. A separately configured trusted reviewer with **admin** capability calls
   `review_memory_candidate(candidate_id, decision="approve", note="...")`.
   Review the source, interpretation, time and competing facts. A possible
   conflict additionally requires `resolution="confirmed_update"`. Approval
   records a reported review, not independent factual truth. The reviewer has
   configured authority; the API does not authenticate a human identity.
5. A read+write client calls `commit_memory_candidate(candidate_id)`. It rechecks
   the original source/hash and uses the existing Memory create/revise operation,
   a fixed idempotency key and the proposal's expected target version. Concurrent
   target changes produce CONFLICT. Retries after a successful promotion return
   its original immutable version receipt, even after a later Memory revision.

Do not give admin to ordinary Agent configurations. Admin is powerful existing
Gateway authority, not a permission granted by a claimed Agent name. Read-only
clients cannot propose/promote; write-only clients cannot read/propose/review.
Review approval and promotion are deliberately separate transactions: an approved
proposal can be resumed after restart. Promotion itself retains the existing
atomic fact/version/provenance/request/audit transaction. Approval does not change
any current assertion. Rejection is final; a corrected interpretation needs a new
proposal. Pending multiple/uncertain conflicts cannot be silently merged.

## Scope and limitations

Classification supports fact_update, preference_change, historical_statement and
inference. Historical statements, inference, non-user sources, unknown source
times, multiple matched facts and truncated conflict scans cannot be promoted as
current facts. A past source may describe a still-current goal, but the reviewer
must explicitly establish that; storage time cannot establish current relevance.

Duplicate/conflict triage normalizes Unicode, case and whitespace while preserving
meaningful punctuation (C and C++ differ). It matches subject/predicate slots in a
bounded search. This is **not complete semantic alias or contradiction detection**.
The Agent interprets semantics; the Gateway checks evidence and review/version
constraints. Paraphrases in different slots can be missed: PARTIAL. No embedded
LLM, knowledge graph or unattended extraction service is introduced.

Memory itself is forbidden as a candidate source, preventing a model-generated
claim from being repeatedly cited as fresh original History evidence. A quote's
existence does not establish entailment. Wrong answers show observed mistakes,
not general ability or a diagnosis. Candidate confidence stays reported and never
automatically increases Memory trust.

Focused `retrieve_evidence` queries support a small transparent bilingual fallback
for physics topics, only after empty results and at most two aliases. Unverified
Memory and inference have distinct temporal labels; evidence includes original
source time and `currency_verified=false`. Retrieval errors retain existing domain
failure diagnostics and partial results.

## Isolated reproduction

```powershell
uv run --locked --extra dev pytest -q tests/test_memory_candidates.py tests/test_evidence_bundle.py
uv run --locked python scripts/demo_personal_evidence.py --learning --root C:/path/outside-git/new-empty-demo
```

The demo creates invented original History, physics textbook/error, a reviewed
current learning-priority Memory and an unapproved historical candidate. It saves
`candidate-lifecycle.json`, `evidence.json` and a read-only `gateway.toml` outside
Git. Never reuse a nonempty root; earlier runs are preserved. Follow
[Memory Host setup](memory-mvp.md) for a named isolated native CLI profile. CLI and
Desktop are separate acceptance scopes. Do not point a demo at private production.
