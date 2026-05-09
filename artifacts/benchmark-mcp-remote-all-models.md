# Remote NEMO MCP Benchmark Matrix (All Tested Models)

Date: 2026-05-09
Suite: quick (2 cases, repeats=1, no warmup, timeout=60s)
Mode: Remote NEMO MCP only

## MCP Connectivity Check

- Verified with benchmark-path run via `--mcp-url http://localhost:8765/mcp/sse` and `--mcp-prefix nemo.`
- Probe artifact: `artifacts/_mcp_connectivity_probe.json`

## Comparative Matrix

| Model | Mode | avg_wall_time_ms | avg_mutation_duration_ms | avg_tokens_per_second | success_rate | noop_rate | validation_pass_rate | avg_changed_files | avg_memory_calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nvidia.agentic.coder-4b | Remote NEMO MCP | 128788.0 | 54746.5 | 8.52 | 1.00 | 0.00 | 0.00 | 2.0 | 6 |
| opus4.7-gods.ghost.codex-4b.gguf | Remote NEMO MCP | 96733.0 | 47637.0 | 0.00 | 0.50 | 0.50 | 0.00 | 0.5 | 6 |
| gemma-4-e4b | Remote NEMO MCP | 53814.5 | 21000.5 | 0.00 | 0.00 | 1.00 | 0.00 | 0.0 | 6 |
| Qwen3.5-9B-MoQ-3.65.gguf | Remote NEMO MCP | 49859.5 | 18621.5 | 0.00 | 0.00 | 1.00 | 0.00 | 0.0 | 6 |

## Quick Readout

- Best balanced remote MCP run in this consistent `*-mcp-all` set: `nvidia.agentic.coder-4b` (success_rate=1.0 and non-zero throughput).
- `opus4.7-gods.ghost.codex-4b.gguf` is faster in wall time than NVIDIA here, but only solved one of two tasks (`success_rate=0.5`, `noop_rate=0.5`).
- `gemma-4-e4b` and `Qwen3.5-9B-MoQ-3.65.gguf` both failed both tasks in this run (`success_rate=0.0`, `noop_rate=1.0`).
- Fast wall time alone is not enough in this suite; task completion currently dominates default selection.
- `validation_pass_rate` remains `0.00` across all models; quality bottleneck is still validation closure.

## Source Artifacts

- artifacts/bench-nvidia-coder-4b-quick-mcp-all.json
- artifacts/bench-opus47-codex4b-quick-mcp-all.json
- artifacts/bench-gemma4-e4b-quick-mcp-all.json
- artifacts/bench-qwen35-9b-moq-365-quick-mcp-all.json
- artifacts/_mcp_connectivity_probe.json
- artifacts/benchmark-mcp-remote-all-models.csv
