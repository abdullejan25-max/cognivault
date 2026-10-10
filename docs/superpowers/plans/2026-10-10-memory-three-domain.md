# Memory and three-domain implementation

User-approved architecture: Agent thinks; Gateway executes. Execute inline in the existing isolated 300b worktree. Preserve all P14/P15 commits and untracked checkpoint files in b36f. No production store access, no messaging data discovery, no release or merge.

- [x] Inspect live refs, preserve baseline, integrate PR 10 in a new branch.
- [x] Reproduce Memory initialization and inherited regression failures.
- [x] Repair Memory transaction initialization; test persistence, rollback and concurrent writers.
- [x] Add semantic duplicate prevention, slot conflicts, no-op revisions and bounded version history.
- [x] Add per-version epistemic assessments; verify reference existence only through Gateway and distinguish existence from reported fact verification.
- [x] Enforce Gateway permissions for every Memory operation, including MCP discovery and source resolution; reject unsafe/config-colliding paths.
- [x] Add a bounded evidence bundle using existing History, canonical History, documents, wrong answers and Memory search. Agent supplies domains and focused queries; Gateway never generates an answer.
- [x] Supply a Codex workflow resource and project guidance for proactive, selective retrieval and untrusted source handling.
- [x] Exercise the three-domain scenario through real MCP transport using only isolated synthetic fixtures, then attempt native Codex Host acceptance with a persistent isolated project config.
- [x] Finish local Windows regression (1013 passed / 14 skipped), package/privacy audit and independent review. The delivery step publishes the reviewed branch as a Draft PR and inspects its remote CI.

Memory references may be stored as unverified claims. A verified assessment requires explicit caller verification notes and resolvable references under read permission; this is reported review, not independent truth proof. Every retrieval rechecks reference availability and labels missing evidence. Existing PR 10 records default to unverified. Schema setup is separate from the atomic record/version/provenance/request/audit transaction; reads never migrate schema.
