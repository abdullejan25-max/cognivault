# Wrong-answer storage diagnostics

`wrong_answer_storage_diagnostics` is a read-only Gateway/MCP tool available
when the document store is configured and the caller has read access. It takes
no arguments and does not create tables, apply migrations, or backfill records.

The response reports six fixed table contracts, column and extra-column counts,
two fixed migration flags, the count of nonpositive analysis versions, and fixed
backfill table availability. Unknown dependent checks return `null`. Unexpected
column names, defaults, table contents, local paths, SQL definitions and raw
storage errors are not returned.

An extra column does not establish corruption. In particular, additive nullable
or defaulted columns may remain compatible with explicit-column writes. The
diagnostic is evidence for investigating a failure, not a repair instruction.

Use the registered Host tool to inspect authoritative data. Do not inspect the
private database directly or substitute a separate client. Synthetic test
results establish the diagnostic contract, not production acceptance.
