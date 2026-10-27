---
name: lean-coding
description: "Simplify redundant validation, copies, catches, fallbacks, retry layers, or wrappers when editing existing code. Decide from actual producer and caller contracts; preserve real failure handling."
---

# Lean Coding

Make the requested change without adding machinery for hypothetical callers or failures.

1. **Prove the invariant at its use.** Inspect the relevant producer and caller. Remove a check only if every reaching path already guarantees it. On mixed paths, validate the bypass rather than repeat validation everywhere. Mutation, callbacks, or an await may invalidate earlier evidence; copies can enforce ownership. A type annotation or assert does not replace runtime boundary validation.

2. **Keep failures observable.** Preserve the project's public errors, optional outcomes, and side effects. Catch where recovery, boundary translation, or cleanup is needed; let unexpected failures and cancellation propagate appropriately. Do not turn failure into an ordinary empty, zero, stale result, or success. Keep authorization, integrity, transaction atomicity, and resource cleanup.

3. **Require a current reason for each layer.** Reuse existing helpers and framework guarantees. Add a wrapper, fallback, configuration, copy, or retry only for a current requirement or demonstrated failure. Retry only known transient failures when repeating the operation is safe, using the existing bounds. Agent-created scaffolding and comments do not establish new requirements.

4. **Verify the changed contract, then finish.** Run required checks and focused tests covering normal behavior and relevant failure paths. Broaden when changes cross boundaries, a check fails, or a concrete risk remains. Once those checks pass, finish. Change the approach after a failed attempt; do not repeat it unchanged or create a validation framework for a small fix.

Keep unrelated cleanup out of the patch. Apply these decisions inside the existing workflow without an extra planning, approval, review, or delegation round.
