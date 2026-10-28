---
name: lean-coding
description: "Use only for requested edits to existing Python code involving redundant validation, defensive copies, exception handling, fallback values, retries, or wrappers. Preserve real failure handling while simplifying. Do not use for read-only explanation or review, documentation-only requests, or non-Python work."
---

# Lean Coding

Make the requested change without adding machinery for hypothetical callers or failures.

1. **Prove the invariant at its use.** Inspect producers, including overrides and bypasses, before relying on shared state; trace its callers and consumers. Follow the supported value shape through consumers, including nesting introduced by existing option combinations. Remove a check only if every reaching path already guarantees it. On mixed paths, validate the bypass rather than repeat validation everywhere. Preserve existing coercion and validation order before branching on values. Mutation, callbacks, or an await may invalidate earlier evidence; copies can enforce ownership. A type annotation or assert does not replace runtime boundary validation.

2. **Preserve observable contracts.** Preserve outputs, public errors, optional outcomes, call forms, signatures, object protocols, metadata, and side effects. Keep explicit input distinct from omission. Classify data using existing parsing or binding state; a control-like spelling alone is insufficient. Optional inspection and observational metadata must not make existing overrides or previously accepted calls fail. Metadata alone does not preserve binding. A forwarding proxy's receiver must not intercept the wrapped callable's keywords; keep the wrapped receiver's original name and keyword eligibility. Catch where recovery, boundary translation, or cleanup is needed; let unexpected failures and cancellation propagate appropriately. Do not turn failure into an ordinary empty, zero, stale result, or success. Keep authorization, integrity, transaction atomicity, and resource cleanup.

3. **Require a current reason for each layer.** Reuse existing helpers and framework guarantees. Add a wrapper, fallback, configuration, copy, or retry only for a current requirement or demonstrated failure. Retry only known transient failures when repeating the operation is safe, using the existing bounds. Agent-created scaffolding and comments do not establish new requirements.

4. **Verify the changed contract, then finish.** Run required checks and focused tests covering normal behavior, affected public call forms and overrides (bound calls, legal class calls using the original receiver keyword, and explicit values resembling control syntax), accepted input protocols, and relevant failure paths including optional inspection failures. Cover existing supported combinations when they change the affected value shape or parsing state; separate mode tests may miss their interaction. Broaden when changes cross boundaries, a check fails, or a concrete risk remains. Once those checks pass, finish. Change the approach after a failed attempt; do not repeat it unchanged or create a validation framework for a small fix.

Keep unrelated cleanup out of the patch. Apply these decisions inside the existing workflow without an extra planning, approval, review, or delegation round.
