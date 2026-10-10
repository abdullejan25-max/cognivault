# Semantic review checkpoint

This checkpoint extends PR #12 on top of PR #11 (`2eedfaa`), starting from
`b190d18`. Main was independently rechecked at `c4e8b63e`. Both PRs are Draft.
PR #10 is closed, not merged separately; its inherited work and the P14/P15
`032698f` baseline remain reachable. No release or tag is authorized.

## Implementation

Agent-proposed `same_as`, `contradicts`, `supersedes` links include a target version,
concrete reason, immutable competing-fact snapshot and original source metadata.
Explicit semantic links require admin confirmation before promotion. `same_as`
retains canonical value and merges bounded sources; `supersedes` additionally
requires a strictly later dated original source. Historical/inference/considered
choices cannot be promoted as decided current facts. The native read-only
`fetch_memory_candidate` provides a usable review packet. Supporting History,
textbook and wrong-answer IDs can be attached to a learning candidate.

Reuse: existing Memory transactions/replay/provenance, History originals,
Gateway capabilities and source resolution, MCP discovery/validation,
three-domain evidence and bounded Chinese lexical fallback. No dependencies,
production migrations, separate stores or heavyweight frameworks were added.

Review fixes: normalized slot detection previously missed equivalent Unicode
subjects; create now enforces the same normalization under its write transaction.
Version history and review packet Memory records have pinned read snapshots.
Source roles/times/hashes are rechecked. Original times are compared in UTC.
Independent review reproduced source-capacity overflow after approval; approval
now rejects it before any current-fact mutation. A separate target-source
freshness concern was not treated as a proven baseline defect, but validation
now conservatively checks that metadata too.

## Reproducible evidence

Final targeted synthetic regression: 43 passed, including candidates, Memory,
three-domain evidence, actual stdio subprocess and identity transition. Independent
review: 11 candidate tests passed, no remaining blocking diff findings.
The full local Windows regression is running at this checkpoint. Final counts,
exact pushed-head CI runs and final package audit are recorded in the PR and
local final report rather than inferred from earlier `b190d18` checks.

Real Codex CLI / synthetic data: native tool events show an original recent
learning quote -> pending `same_as` proposal -> read-only review packet. The
Agent identified the unconfirmed ordering phrase, kept the candidate pending
and based cited recommendations on approved Memory, original History, wrong
answer and textbook. The verifier confirmed three-domain evidence, no approval
or fact writes, no external operations and pending exclusion from Memory search.
Failed over-limit/unsupported-reference calls and unavailable QMD are preserved;
the Agent corrected inputs and completed via available evidence. Exit code alone
was not counted as acceptance. Generic zero retrieval and injection defense have
prior CLI evidence; they were not unnecessarily rerun due to the GUI blocker.

Desktop preparation is available through `scripts/prepare_desktop_acceptance.py`.
It creates new, entirely invented shared data and read+write/read-only projects,
validates SDK permission denial and prepares six consolidated GUI checks.
SDK preflight is not Desktop evidence. Current Computer Use inventory identifies
the Codex app with a ChatGPT window; the skill forbids automating ChatGPT desktop
UI. No prohibited input, global configuration or trust changes were performed.
The user reported trust completed, but no isolated Desktop task receipts are yet
available. Six actual Desktop gates remain BLOCKED pending those receipts.

## Boundaries

| Scope | State at checkpoint |
|---|---|
| Reviewed semantic relations and candidate isolation | PASS, synthetic |
| New native CLI candidate and three-domain learning flow | PASS, real CLI / synthetic |
| Desktop six-case acceptance | BLOCKED, trust reported by user only |
| Automatic semantic detection across arbitrary wording | PARTIAL, Agent proposes / reviewer confirms |
| Independent truth, expiry and real current learning status | PARTIAL |
| Production data activation | NOT RUN |
| Latest-head full CI | PENDING, must inspect actual run |
| PR merge / official release | NOT MERGED / NOT RELEASED |

The review packet pins Memory versions only; cross-domain original reads are not
one distributed snapshot. Approval is configured reviewer authority, not proof
of a human review. These limitations are explicit in the public workflow.
No personal originals, private databases/configuration or Host logs enter Git.
