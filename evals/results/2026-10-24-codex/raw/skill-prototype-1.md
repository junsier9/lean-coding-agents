---
name: lean-coding
description: "Keep bug fixes, bounded features, and simplifications focused on existing contracts. Use when a change risks redundant guards, silent fallbacks, speculative abstractions, or disproportionate verification."
---

# Lean Coding

Complete the requested behavior with the least unnecessary machinery. Preserve correctness, readable code, and real failure handling.

## Decision rules

1. **Locate the contract.** Read the relevant producer, caller, and existing implementation before inventing defenses or abstractions. Distinguish trusted internal values from external input. Types alone do not validate runtime data; earlier validation may stop being sufficient after mutation.

2. **Make failure distinguishable.** Catch where code can recover, translate a boundary error, or release resources. Preserve expected optional outcomes. Unexpected failures must reach an appropriate caller or error boundary; do not turn them into a normal zero, empty collection, stale result, or apparent success. Follow the project's established error interface.

3. **Remove defenses only with evidence.** A redundant guard needs a concrete invariant on every supported path. If uncertainty remains, inspect the path that could violate it. Preserve input validation, authorization, integrity checks, resource cleanup, and demonstrated recovery. Copies may enforce ownership or prevent aliasing even when the callee does not mutate its input.

4. **Spend complexity on the current requirement.** Reuse existing helpers and framework guarantees. Add wrappers, configuration, persistent state, retries, or gates when a current caller, real failure mode, or explicit requirement needs them. For retries, check which errors are transient and whether repeating the operation is safe. Agent-written comments, tests, or scaffolding are not independent evidence that a speculative capability is required.

5. **Keep the change coherent.** Fix the root cause across the necessary paths. Leave unrelated renames, formatting, cleanup, and future flexibility out of the patch. Preserve protocol strings, prompts, public results, and side effects unless their change is requested. Prefer clear code over compressed one-liners or arbitrary line-count targets.

6. **Verify, then finish.** Run the required checks and focused tests that cover the affected behavior, including relevant failure paths. Expand verification for cross-cutting effects, failures, or unresolved risks. Once the applicable checks pass, finish unless new evidence warrants more work. If an attempt fails, use the failure to change the next attempt; do not repeat an unchanged approach or create process machinery merely to keep trying.

Use these decisions within the task's existing workflow. Straightforward work does not need an extra plan document, approval round, review loop, or delegation step just to apply this skill.

Report what changed, what actually ran, and any material uncertainty. Do not claim preserved behavior from inspection alone when meaningful execution is available.
