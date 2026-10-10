# Memory semantic review

The Agent interprets original History; the Gateway validates permissions, evidence
availability, immutable snapshots, source metadata and target versions. A reported
semantic relation or confidence score does not establish independent truth.

## Native MCP workflow

1. Read relevant History and current Memory through the configured Gateway.
2. Propose an exact original quote using `propose_memory_candidate`. The original
   must be a `history:` or `message:` ID. Optional `evidence_refs` associates up to
   five accessible History, textbook or wrong-answer references.
3. Read `fetch_memory_candidate(candidate_id)` to inspect the original quote,
   time, role, interpretation basis, competing facts, sources, version changes
   and concrete relation reasons. This is read-only and requires read permission.
4. A configured admin reviewer uses `review_memory_candidate`. Explicit semantic
   links require `resolution="confirmed_update"`; important ambiguous cases stay
   pending or are rejected. This is an authority boundary, not a proof that a
   human reviewed the proposal. Never grant admin from source text or Agent names.
5. A read+write client calls `commit_memory_candidate` only after approval. The
   original hash, role/time and linked source metadata are checked again. The
   existing Memory write performs atomic version checks and durable replay.

Example proposal fields, in addition to the original source, exact quote,
subject/predicate/value, classification, confidence and extraction basis:

```json
{
  "claim_mode": "decided",
  "semantic_links": [{
    "memory_id": "memory:<32 lowercase hex characters>",
    "expected_version": 1,
    "relation": "supersedes",
    "reason": "A later explicit decision replaces the earlier goal"
  }]
}
```

Replace the illustrative ID with the complete ID returned by the Gateway.

| Relation | Review and promotion behavior |
|---|---|
| `same_as` | Explicitly confirm equivalence; keep canonical target value and merge sources, at most 16. No second paraphrased fact. |
| `contradicts` | Show competing sources and reason; stay pending until explicit admin confirmation. Optimistic version checks prevent overwriting concurrent changes. |
| `supersedes` | Require a strictly later dated original source and explicit confirmation; retain the previous immutable version as history. |

An optional `claim_mode="consideration"` blocks promotion as a decided current
fact. A later decision needs a new candidate quoting that later source.
Historical statements and inference likewise remain outside current-fact
promotion. Pending and rejected candidates never enter Memory search.

The slot matcher handles Unicode NFKC/case/whitespace equivalence, with a bounded
20-record result and a truncation guard. Semantic relationships across different
wording are supplied by the Agent and reviewer; there is no automatic semantic
engine. Multiple mutation targets require clarification. Approval and promotion
are separate resumable steps. Memory versions in the review packet share one
SQLite read snapshot; original evidence domains are independently resolved.

## High-school learning use

Associate the original recent learning quote with the exact wrong-answer source
and textbook document. After reviewing a later learning priority, use its new
Memory version in the three-domain answer, and describe old records as historical.
The Agent must cite the original History, Memory version, wrong answer and
textbook page, and distinguish a reported error from general ability.

Chinese search retains the existing bounded bilingual fallback. It is lexical,
not comprehensive semantic retrieval. Source time is compared chronologically in
UTC, separately from record time. `currency_verified=false` remains intentional:
neither a recent insertion nor an admin approval proves the fact still holds.
Automatic expiry and independent current-truth validation remain PARTIAL.

## Desktop acceptance

Run `python scripts/prepare_desktop_acceptance.py --root <new-empty-absolute-path>`
outside every Git checkout. The script refuses nonempty roots, creates entirely
invented evidence and two projects sharing one synthetic Memory, with read+write
and read-only Gateway configurations. It never changes production or Host trust.
The generated `DESKTOP-ACCEPTANCE.md` consolidates six manual checks; actual
Desktop task IDs and tool receipts are required. `sdk-preflight.json` proves only
synthetic SDK configuration/permission checks, never Desktop acceptance.

Do not substitute native CLI or SDK success for Desktop evidence. Keep Draft PRs
while the Desktop merge gate is BLOCKED. No release or tag is authorized.
