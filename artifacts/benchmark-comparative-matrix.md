# Benchmark Comparative Matrix

Date: 2026-05-09
Suite: quick (2 cases, repeats=1, no warmup, timeout=60s)
Harness: NEMOCODE headless benchmark

## Matrix

| Model | NEMO Mode | avg_wall_time_ms | avg_mutation_duration_ms | avg_tokens_per_second | success_rate | noop_rate | validation_pass_rate | avg_changed_files | avg_memory_calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nvidia.agentic.coder-4b | Local persistent adapter | 109112.5 | 50804.0 | 11.80 | 0.50 | 0.50 | 0.00 | 1.0 | 7.0 |
| nvidia.agentic.coder-4b | Remote NEMO MCP | 105458.5 | 47112.0 | 14.48 | 0.50 | 0.50 | 0.00 | 1.0 | 6.0 |
| opus4.7-gods.ghost.codex-4b.gguf | Local persistent adapter | 120876.0 | 60163.0 | 0.00 | 0.50 | 0.50 | 0.00 | 0.5 | 7.0 |
| opus4.7-gods.ghost.codex-4b.gguf | Remote NEMO MCP | 153013.5 | 48836.5 | 6.45 | 1.00 | 0.00 | 0.00 | 2.0 | 6.0 |

## Local vs MCP Delta by Model

| Model | Delta avg_wall_time_ms (MCP-Local) | Delta avg_mutation_duration_ms (MCP-Local) | Delta avg_tokens_per_second | Delta success_rate | Delta noop_rate |
|---|---:|---:|---:|---:|---:|
| nvidia.agentic.coder-4b | -3654.0 | -3692.0 | +2.68 | +0.00 | +0.00 |
| opus4.7-gods.ghost.codex-4b.gguf | +32137.5 | -11326.5 | +6.45 | +0.50 | -0.50 |

## Readout

- nvidia.agentic.coder-4b: MCP gives a small speed gain and higher throughput, but no quality change on this quick suite.
- opus4.7-gods.ghost.codex-4b.gguf: MCP improves task completion signal (success_rate/noop_rate), but increases end-to-end wall time.
- validation_pass_rate remains 0.00 for all four runs; the next bottleneck is functional validation closure, not only context/memory routing.

## Source Artifacts

- artifacts/bench-nvidia-coder-4b-quick-baseline.json
- artifacts/bench-nvidia-coder-4b-quick-mcp.json
- artifacts/bench-opus47-codex4b-quick-local.json
- artifacts/bench-opus47-codex4b-quick-mcp.json

## External Internet References (May 2026)

Fetched: 2026-05-09 UTC

These references are for orientation only. They are not directly comparable 1:1
with this local quick suite because harnesses, tasks, and scoring definitions
are different.

### SWE-bench Verified (Bash-only LM comparison)

Source: https://www.swebench.com/index.html and https://www.swebench.com/verified.html

| Model (public leaderboard) | % Resolved | Listed date |
|---|---:|---|
| Claude 4.5 Opus (high reasoning) | 76.80 | 2026-02-17 |
| Gemini 3 Flash (high reasoning) | 75.80 | 2026-02-17 |
| MiniMax M2.5 (high reasoning) | 75.80 | 2026-02-17 |
| Claude Opus 4.6 | 75.60 | 2026-02-17 |
| GPT-5-2 Codex | 72.80 | 2026-02-19 |
| GPT-5 Mini | 56.20 | 2026-02-17 |

### EvalPlus (rigorous code correctness tests)

Source: https://evalplus.github.io/leaderboard.html

| Model (public leaderboard) | EvalPlus score |
|---|---:|
| O1 Preview (Sept 2024) | 89.0 |
| O1 Mini (Sept 2024) | 89.0 |
| Qwen2.5-Coder-32B-Instruct | 87.2 |
| GPT 4o (Aug 2024) | 87.2 |
| DeepSeek-V3 (Nov 2024) | 86.6 |

### Aider Code Editing Leaderboard (legacy page)

Source: https://aider.chat/docs/leaderboards/edit.html

| Model (public leaderboard) | % completed correctly | Last updated noted on page |
|---|---:|---|
| o1 | 84.2% | 2025-04-12 |
| claude-3-5-sonnet-20241022 | 84.2% | 2025-04-12 |
| gemini-exp-1206 (whole) | 80.5% | 2025-04-12 |
| qwen2.5-coder:32b (ollama) | 72.9% | 2025-04-12 |

### CRUXEval (code reasoning / execution)

Source: https://crux-eval.github.io/leaderboard.html

| Model (CRUXEval-I leaderboard) | pass@1 |
|---|---:|
| gpt-4-turbo-2024-04-09+cot (n=3) | 75.7 |
| gpt-4o+cot (n=3) | 75.6 |
| gpt-4-0613+cot | 75.5 |
| claude-3-opus+cot (n=1) | 73.4 |

## Comparability Notes

- This matrix measures local NEMOCODE end-to-end behavior with our quick suite,
	including memory routing (local adapter vs remote MCP).
- SWE-bench Verified reports issue-resolution rate on a curated real-world issue
	set and often a specific agent harness.
- EvalPlus focuses on functional correctness under strict tests (HumanEval+/MBPP+).
- Aider leaderboard measures coding/editing completion in aider-specific edit loops.
- CRUXEval emphasizes code reasoning and execution understanding rather than only
	file-edit task closure.
