# Memory MVP (development branch, unreleased)

This is a first executable slice of CogniVault's durable Memory domain.
It is NOT a completed Study + History + Memory integration.

## Setup

Create a private directory outside Git, then add this to an ignored
`config.local.toml`:

```toml
[memory]
backend = "sqlite"
database = "C:/PRIVATE_COGNIVAULT/memory.db"
```

With `[permissions] capabilities = ["read"]`, only `fetch_memory` and
`search_memory` are advertised. Add `write` only when explicit durable fact
mutation is intended. Omit `[memory]` to disable Memory entirely.

## What works

- `create_memory`: immutable fact identity plus an initial version.
- `revise_memory`: optimistic version-checked correction.
- `retire_memory`: append-only invalidation; retains earlier evidence.
- `fetch_memory` and `search_memory`: latest version and active-only search.
- Idempotency keys and conflict protection; audit and reported actor provenance.
- Only the configured local SQLite path is used; no automatic scanning/import.

## Critical evidence boundary

`source_refs` are required, syntactically checked **unverified claims**.
`evidence_verified: false` is intentional. This MVP does not verify that an
existing History message or Study evidence matches the reference. Unverified
claims must never be described as established facts. A future Gateway resolver
must independently check existence, domain access, exact content where needed,
and cross-domain permission before changing that status.

The initial persistence component also does **not** deduplicate separate
idempotency keys into a unified assertion, classify sensitive facts, decide
whether to remember autonomously, or resolve contradictions across fact IDs.
It does not yet provide an automatic Agent router or Obsidian Memory graph.

## Acceptance criteria before integration

Run `pytest -q tests/test_memory.py`, plus full regression, permission
and Windows CI checks on a review branch. Confirm synthetic evidence only;
do not open private databases directly. This branch is not a release.
