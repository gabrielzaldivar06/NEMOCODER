---
name: self-modification
description: Safely modify Space Code itself using sandboxed execution, validation, review gates, and NEMO writeback.
---

# Self-Modification

When modifying Space Code itself:

- Keep changes scoped to the requested objective and target files.
- Preserve public APIs unless the objective explicitly requires a contract change.
- Add or update tests for behavioral changes.
- Do not edit generated runtimes, caches, databases, secrets, or Git internals.
- Prefer small vertical changes over broad rewrites.
- Capture unresolved risks in validation or review output instead of hiding them.
- Record architectural decisions or corrections in NEMO memory when behavior changes.