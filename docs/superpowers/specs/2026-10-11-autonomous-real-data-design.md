# Autonomous real-data execution design

Authorized on 2026-10-11 by the user's five-day request. Reuse Gateway and existing History normalization; do not build another scheduler or data authority.

## Independent workstreams
1. Exam: audit staging against original images, preserve input snapshots, quarantine unverified mappings, then use native MCP Asset/source/analysis calls with stable provenance and idempotency keys. Read back every persisted record.
2. AI History: prepare original-byte inbox manifests from explicit authorized local roots, ingest with native MCP, pin normalization snapshots and verify actual canonical output. Source-only and unknown times remain explicit.
3. WeChat: first establish a stable allowlisted contact identity and lawful locally accessible source. Raw content stays local; no other conversation enters Gateway, logs, reports or model context. No guessed identity or automatic Memory promotion.
4. Scheduling: Codex Desktop owns the weekly schedule. An active project-scoped automation must fail closed on unavailable production Gateway or protected source prerequisites.
5. Recovery and feedback: use formal Gateway recovery; evidence-linked current learning suggestions remain reviewable, with uncertainty and version history.

## Alternatives assessed
Reuse existing Gateway orchestration is preferred to a new bulk data store. A new scheduling service violates the request. Blindly importing staging would mistake prior analysis for independent source verification.

## Verification and stop conditions
Synthetic tests verify new snapshot preparation only. Actual production native calls and readback establish real ingestion; CLI and Desktop evidence are separately labelled. No source content enters tracked files. A missing credential, identity, export, native tool or recovery prerequisite blocks that workstream while others proceed.

## First implementation plan
Create scripts/prepare_authorized_history.py and tests/test_authorized_history.py. RED tests cover explicit allowlists, path traversal/reparse rejection, changing sources, incomplete JSONL, exact bytes, manifest contracts and repeat execution. Implement minimal safe snapshots and bounded manifests, run tests, then independently review. Execution plans and private operational evidence are stored in ignored var/autonomous-20261011, with an exact continuation checkpoint. No direct MCP client fallback.
