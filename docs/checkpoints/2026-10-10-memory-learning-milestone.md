# Memory learning milestone checkpoint

## Repository and preserved work

Live main was rechecked at c4e8b63e. PR #10 remains reachable through the PR #11
integration merge; the P14/P15 baseline 032698f is also an ancestor. Its b36f
checkout and untracked docs/checkpoints were left untouched. No production store
or private source was inspected, modified or submitted. No release is authorized.

PR #11 review fixes are commit 2eedfaa: write-only revise/retire receipts no longer
expose stored subject/predicate; Memory search pins a SQLite read snapshot through
count, selected records and assessments. Both regressions were observed failing
before the fix. Its Ubuntu/Windows full tests, build, privacy audit and clean
installation passed in CI run 38053582935. Desktop remains a merge gate.

## Implemented in codex/memory-learning-milestone

History candidate triage, immutable source-bound proposals and final reviews,
admin-gated review, approved promotion through existing versioned Memory writes,
hash checks, optimistic target versions and idempotent replay. Pending/rejected
candidates are excluded from answer evidence; Memory cannot be an original source.
Non-user/historical/inference/unknown-time proposals cannot become current facts.
Review and promotion are separate resumable steps, not one distributed transaction.

Five native MCP tools: find_memory_candidates, propose_memory_candidate,
list_memory_candidates, review_memory_candidate and commit_memory_candidate.
Their discovery and execution enforce the same configured capabilities.

Focused Chinese physics/goal queries gain bounded bilingual lexical fallback.
Evidence carries source time separately from record time, currency uncertainty
and explicit inference/unverified labels. No heavy framework or new dependency.

Independent review identified and regression-tested two further issues before
delivery: punctuation normalization (C++ must differ from C), and stable duplicate
promotion receipts after later fact revisions. Both were fixed and re-reviewed.

## Validation evidence and boundary

Targeted regression: 65 passed / 1 skipped, including native in-memory MCP,
actual stdio subprocess, permissions, Memory and three-domain evidence.
Wheel/sdist build and package privacy audit passed (73/279 entries at this
checkpoint); isolated installed wheel imports the candidate module and packaged
personal-answer workflow. Full final regression and pushed-SHA CI remain required;
their final results are recorded in the review PR and local acceptance report.

Real Codex CLI used a new synthetic root, with a fresh process and read-only
Gateway profile. The natural Chinese prompt asked for a review plan based on past
physics errors, recent learning and textbooks. Actual MCP calls obtained all three
domains and relevant originals; its answer cited message, versioned Memory,
textbook page and wrong answer. It connected omitted friction to force diagrams,
friction direction and Newton's second law, without inferring general ability.
Generic 2+2 used zero MCP calls. Native-event verifier returned PASS.

Local synthetic evidence only:
C:/Users/feng/AppData/Local/CogniVaultSynthetic/20261010/learning1/
- candidate-lifecycle.json: original recent learning -> pending -> admin review ->
  Memory v1; a separate historical candidate remains pending.
- learning-host-events.log / generic-host-events.log: actual native CLI events.
- learning-answer.txt / host-verification.json: cited answer and acceptance result.
- installed/: clean installed package smoke (never production).

Host anomalies are retained: resources/list failed, direct workflow URI succeeded;
two fetch calls omitted ID prefixes, were rejected and corrected. Successful exit
alone was not counted as Host acceptance.

## Gates and next work

| Scope | State |
|---|---|
| Candidate lifecycle and Memory integrity | PASS, isolated synthetic |
| Native three-domain learning answer | PASS, real CLI / synthetic data |
| Generic question zero retrieval | PASS, real CLI / synthetic data |
| Desktop independent acceptance | BLOCKED |
| Complete semantic alias/contradiction detection | PARTIAL |
| Independent truth / current-relevance verification | PARTIAL, reported reviewer |
| Production learning-data activation | NOT RUN |
| Merge/release | NOT MERGED / NOT RELEASED |

Computer Use inspection returned one OpenAI.Codex app-identity window whose native
state identifies ChatGPT.exe with only a disabled pane; no independent Codex
acceptance entry was established. Computer Use guidance forbids operating ChatGPT
Desktop UI. We stopped at read-only identification. No private profile, security
setting or production configuration was changed. Actual Desktop acceptance needs
a trusted isolated project/session exposed to the Host; CLI evidence is separate.

Semantic matching is bounded normalized subject/predicate matching plus Agent
interpretation, not a complete paraphrase engine. Next priority: explicit semantic
relation proposals with competing-source/date evidence and a practical reviewer
experience, alongside the blocked Desktop matrix. Keep uncertain interpretations
pending. See [actual tool workflow](../memory-candidate-workflow.md).
