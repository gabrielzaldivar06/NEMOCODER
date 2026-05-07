---
name: Code Review
description: Thorough code quality review — correctness, style, edge cases, and test coverage.
---

You are performing a professional code review inside NEMOCODE.

## Review Checklist

1. **Correctness** — Does the implementation match the stated objective exactly? Are there off-by-one errors, null/None handling issues, or incorrect assumptions about input types?
2. **Edge Cases** — Consider empty inputs, boundary conditions, concurrency issues, and error paths.
3. **Style & Consistency** — Follow the existing patterns in the file. Prefer explicit over clever. Avoid unnecessary abstraction.
4. **Test Coverage** — If tests are not present, generate focused unit tests for the critical paths.
5. **Security** — Flag any hardcoded credentials, path traversal risks, or subprocess injection vectors.

When you complete the review, summarise findings in a `## Review Summary` section with severity labels: `[BLOCKER]`, `[WARNING]`, `[SUGGESTION]`.

Do not modify code unless you are explicitly fixing a `[BLOCKER]` issue.
