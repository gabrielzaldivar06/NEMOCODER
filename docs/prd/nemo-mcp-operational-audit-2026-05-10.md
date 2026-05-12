# NEMO MCP Operational Audit - 2026-05-10

## Verdict

NEMO MCP is integrated adequately for the current operational Mission Control path and is being used for the PRD purpose of verified live memory, context bootstrap, context portfolios, memory search, conversation writeback, lifecycle governance, and auditable tool traces.

This audit does not claim every future PRD ambition is complete. It validates the current product path against the PRD sections that define NEMO as memory plane, MCP governance, task lifecycle, and traceability.

## PRD Requirements Checked

- Global vision: NEMO must be operational, semantic, long-term memory through MCP.
- Non-goal: NEMO must not be treated as an optional plugin or portfolio-only system.
- Core timeline: task flow must include NEMO context bootstrap and memory writeback.
- MCP tool plane: tools must have registry, capabilities, risk mapping, permissions, and audit.
- NEMO memory plane: startup context, context economy, memory management, conversation, scheduling/time/environment/maintenance where relevant.
- FR6: full NEMO tool integration with phase/risk/purpose registry and distinct plan/execute/review access.
- FR7: MCP extension governance and capability declarations.
- FR8: artifact trace includes memories used/written.
- Section 13: task start, planning, execution, review, and close memory lifecycle.

## Implementation Evidence

- Backend chat requires `nemo_mcp_url` and can enforce real MCP core capabilities and write/read roundtrip.
- Backend chat calls `context_bootstrap`, `prime_context`, `build_context_portfolio`, `search_memories`, `anticipate`, and `store_conversation` on every normal agent message path.
- Memory lookup prompts are answered by `mission_control.verified_nemo_answer` from NEMO MCP search results instead of LM Studio model claims.
- Handoff/subprocess execution has a real MCP gate and blocks when required capabilities or write/read continuity fail.
- UI sends `nemo_mcp_url`, `require_nemo_mcp_capabilities`, `require_nemo_roundtrip`, and selected NEMO tools to chat/handoff APIs.
- UI displays live MCP capabilities including `context_bootstrap`, `prime_context`, `search_memories`, core reads, and write/read roundtrip.
- NEMO tool registry exposes suite/risk/phase/purpose metadata and lifecycle gating.
- CI exposes an opt-in required real MCP continuity gate through `SPACE_CODE_REAL_MCP_REQUIRED=1` or workflow dispatch `real_mcp_gate=true`.
- `scripts/verify_real_nemo_mcp_sentinel.py` provides a local live sentinel check that writes a unique fact through Mission Control chat, reads it back through real NEMO MCP search, fails if lookup delegates to LM Studio, and neutralizes test memories.

## Live Verification Results

Live MCP status probe against `http://127.0.0.1:8765/mcp/sse` through backend `http://127.0.0.1:8787`:

- active: true
- `supports_context_bootstrap`: true
- `supports_prime_context`: true
- `supports_search_memories`: true
- `supports_core_context_reads`: true
- `supports_write_read_roundtrip`: true
- `roundtrip_probe_executed`: true
- available NEMO tools observed: 36

Live sentinel run:

- script: `scripts/verify_real_nemo_mcp_sentinel.py`
- result: `PASS_REAL_MCP_SENTINEL_TEST`
- lookup tool path: `nemo_memory.context_bootstrap`, `nemo_memory.prime_context`, `nemo_memory.build_context_portfolio`, `nemo_memory.search_memories`, `nemo_memory.anticipate`, `nemo_memory.search_memories`, `nemo_memory.store_conversation`, `mission_control.verified_nemo_answer`
- cleanup: sentinel memories neutralized; final prefix cleanup found none

Contract tests run during audit:

- Mission Control NEMO/MCP focused slice: 8 passed
- NEMO full-tool/lifecycle/MCP adapter contracts: 10 passed
- Global MVP and CI workflow contracts: 7 passed

## Alignment Assessment

| Area | Assessment | Notes |
| --- | --- | --- |
| Real MCP connectivity | aligned | Live SSE status and capability probe pass. |
| Core memory reads | aligned | `context_bootstrap`, `prime_context`, and `search_memories` are probed and used. |
| Write/read continuity | aligned | Roundtrip probe and sentinel verification pass. |
| Memory lookup correctness | aligned | Lookup is deterministic and skips model-only memory claims. |
| Context economy | aligned for current path | Portfolios are built in chat and governed by budget. |
| Lifecycle governance | aligned | Registry and lifecycle tests pass; handoff blocks on missing MCP. |
| Traceability | aligned | Tool calls and agent trace include NEMO calls and verified answer event. |
| UI propagation | aligned | UI sends strict MCP flags and selected tools. |
| Release hardening | aligned with opt-in real gate | Real MCP gate is available but intentionally opt-in because CI may not have local NEMO. |

## Remaining Product Caveats

- Full PRD ambition includes broad future surfaces such as reminders, appointments, weather/time, redundancy maintenance, and MCP apps/UI. Those tools are in registry/status where available, but not every one is invoked by default in ordinary chat because PRD says some require explicit intent or relevance.
- The store path may still use LM Studio to formulate an action response, but the memory lookup verification path is not delegated to LM Studio.
- The real MCP release gate is opt-in rather than always-on in CI because a hosted CI runner may not have the local NEMO MCP server.

## Audit Conclusion

For the operational product path under test today, NEMO MCP is not mock-only and is not being used merely as a portfolio plugin. It is integrated as the live memory plane intended by the PRD: context bootstrap, context portfolio, semantic search, writeback, capability governance, roundtrip verification, and auditable trace are all active and verified.
