# Global PRD Alignment Matrix

This document tracks auditable traceability from PRD functional requirements to specs, executable tests, and implementation anchors.

Legend:

- `aligned`: implemented with spec, contract tests, and code anchors.
- `partial`: implemented core path exists, but one or more product-level expectations still need explicit hardening.
- `missing`: no sufficient coverage yet.

## FR Traceability

| FR | Status | Spec Coverage | Contract Tests | Implementation Anchors | Remaining Gap |
| --- | --- | --- | --- | --- | --- |
| FR1 Desktop App Shell | aligned | docs/specs/desktop-product-contract.md, docs/specs/desktop-ui-flows.md | tests/test_desktop_product_contract.py, tests/test_desktop_ui_flows_contract.py | src/nemo_coding_platform/core/product.py:61, src/nemo_coding_platform/mission_control_server.py:999 | none |
| FR2 Local Model Provider | aligned | docs/specs/desktop-ui-flows.md, docs/specs/fr2-runtime-execution-contracts.md | tests/test_desktop_ui_flows_contract.py, tests/test_model_config.py, tests/test_mission_control.py, tests/test_mission_control_server.py, tests/test_fr2_runtime_execution.py | src/nemo_coding_platform/core/model_config.py:10, src/nemo_coding_platform/core/model_config.py:29, src/nemo_coding_platform/core/role_execution_profile.py:1, src/nemo_coding_platform/core/engine_interface.py:69, src/nemo_coding_platform/mission_control_server.py:1059 | none |
| FR3 Aider-Quality Editing | aligned | docs/specs/quality-mutation-pipeline.md | tests/test_quality_core_contract.py | src/nemo_coding_platform/core/mutations.py:24, src/nemo_coding_platform/core/mutations.py:37 | none |
| FR4 OpenCode-Style Workflow | aligned | docs/specs/task-run-model.md, docs/specs/headless-run-mvp.md, docs/specs/desktop-ui-flows.md | tests/test_agent_runtime_contract.py, tests/test_desktop_ui_flows_contract.py, tests/test_mission_control_server.py | src/nemo_coding_platform/mission_control_server.py:2068, src/nemo_coding_platform/mission_control_server.py:1893 | none |
| FR5 OpenHands-Style Autonomy | aligned | docs/specs/autonomy-model.md, docs/specs/full-handoff-autonomy.md | tests/test_autonomy_policy_contract.py, tests/test_agent_runtime_contract.py | src/nemo_coding_platform/core/product.py:25, src/nemo_coding_platform/core/product.py:132 | none |
| FR6 NEMO Full Tool Integration | aligned | docs/specs/nemo-full-tooling.md, docs/specs/nemo-lifecycle.md | tests/test_nemo_full_tool_contract.py | src/nemo_coding_platform/core/memory.py:73, src/nemo_coding_platform/core/memory.py:382, src/nemo_coding_platform/mission_control_server.py:1806 | none |
| FR7 MCP Extension Governance | aligned | docs/specs/mcp-extension-governance.md | tests/test_mcp_server.py, tests/test_mcp_adapter.py | src/nemo_coding_platform/mcp_server.py:50, src/nemo_coding_platform/mcp_server.py:90, src/nemo_coding_platform/mcp_server.py:205 | none |
| FR8 Artifact Trace | aligned | docs/specs/task-run-model.md, docs/specs/desktop-ui-flows.md | tests/test_desktop_ui_flows_contract.py, tests/test_agent_runtime_contract.py | src/nemo_coding_platform/core/task_run.py:76, src/nemo_coding_platform/core/review_package.py:82, src/nemo_coding_platform/core/persistence.py:84 | none |
| FR9 Autonomous Background Tasks | aligned | docs/specs/autonomy-model.md, docs/specs/full-handoff-autonomy.md, docs/specs/worktree-runtime.md | tests/test_agent_runtime_contract.py, tests/test_desktop_ui_flows_contract.py | src/nemo_coding_platform/mission_control_server.py:2068, src/nemo_coding_platform/mission_control_server.py:2085, src/nemo_coding_platform/mission_control_server.py:2089 | none |
| FR10 Evaluation and Replay | aligned | docs/specs/agent-evals.md | tests/test_agent_evals_contract.py, tests/test_evals_export.py | src/nemo_coding_platform/core/evals.py:147, src/nemo_coding_platform/core/persistence.py:84, src/nemo_coding_platform/core/persistence.py:316, src/nemo_coding_platform/cli.py:639 | none |

## Non-FR Traceability

This section tracks global PRD alignment outside FR1-FR10: non-functional requirements, data model coverage, lifecycle semantics, and MVP acceptance closure.

| PRD Section | Status | Spec Coverage | Contract Tests | Implementation Anchors | Remaining Gap |
| --- | --- | --- | --- | --- | --- |
| Section 11 Non-Functional Requirements | partial | docs/specs/quality-mutation-pipeline.md, docs/specs/worktree-runtime.md, docs/specs/mcp-extension-governance.md | tests/test_quality_core_contract.py, tests/test_agent_runtime_contract.py, tests/test_mcp_server.py | src/nemo_coding_platform/core/mutations.py:24, src/nemo_coding_platform/core/worktree_runtime.py:47, src/nemo_coding_platform/mcp_server.py:90 | Performance SLOs and secret-handling policy are not yet represented as explicit contract tests. |
| Section 12 Data Model | partial | docs/specs/task-run-model.md, docs/specs/headless-run-mvp.md | tests/test_agent_runtime_contract.py, tests/test_product_contract.py | src/nemo_coding_platform/core/task_run.py:76, src/nemo_coding_platform/core/headless_runner.py:132 | Some PRD entities (WorkflowRecipe, ModelProfile linkage, explicit NemoMemoryEvent type) are implicit but not fully normalized as first-class contracts. |
| Section 13 NEMO Memory Lifecycle | aligned | docs/specs/nemo-lifecycle.md, docs/specs/nemo-full-tooling.md | tests/test_nemo_full_tool_contract.py, tests/test_nemo_lifecycle.py | src/nemo_coding_platform/core/memory.py:382, src/nemo_coding_platform/core/nemo_lifecycle.py:1, src/nemo_coding_platform/core/headless_runner.py:677 | none |
| Section 17 Global MVP Acceptance | aligned | docs/specs/desktop-product-contract.md, docs/specs/desktop-ui-flows.md, docs/specs/agent-evals.md | tests/test_desktop_product_contract.py, tests/test_desktop_ui_flows_contract.py, tests/test_release_confidence_e2e.py, tests/test_global_mvp_gate.py | src/nemo_coding_platform/mission_control_server.py:3338, src/nemo_coding_platform/core/persistence.py:84, src/nemo_coding_platform/cli.py:969 | none |

## SDD Critical Artifacts (Delta)

Newly added to close identified roadmap gaps:

- docs/specs/desktop-product-contract.md
- docs/specs/autonomy-model.md
- docs/specs/nemo-full-tooling.md
- docs/specs/desktop-ui-flows.md
- docs/specs/agent-evals.md
- docs/specs/mcp-extension-governance.md
- tests/test_quality_core_contract.py
- tests/test_evals_export.py
- tests/test_desktop_ui_flows_contract.py
- tests/test_agent_evals_contract.py

## Definition Of Done By FR

- FR2 ✅ COMPLETE
	- model roles planner/editor/reviewer/summarizer are configurable and validated
	- role-specific timeout/error handling with provider bounds enforcement implemented
	- 29 contract tests validate timeout multipliers, error strategies, and provider-specific bounds
- FR4 ✅ COMPLETE
	- planner/builder/reviewer and specialized subagent permission contracts are executable
	- plan mode write prohibition and review gate invariants are covered by tests
	- `workflow_mode` field enforces phase separation at API dispatch with deterministic `workflow_policy_violation` error codes
	- `workflow_mode` propagation through resumed job payloads verified by contract tests
	- 4 new contract tests in `test_mission_control_server.py`; 3 new contract tests in `test_agent_runtime_contract.py`
	- specs updated: `docs/specs/task-run-model.md`, `docs/specs/headless-run-mvp.md`
- FR7
	- risky MCP actions are review-gated in central dispatcher
	- denied actions return deterministic policy errors and are audit-traceable
	- registry risk metadata is surfaced in MCP tool definitions

## Machine-Readable Traceability

- Source of truth for CI checks: `docs/prd/alignment-matrix.json`

## Verification Commands

- `python -m unittest tests.test_evals tests.test_evals_export tests.test_quality_core_contract -v`
- `python -m unittest tests.test_desktop_ui_flows_contract tests.test_agent_evals_contract -v`
- `python -m unittest tests.test_mcp_server tests.test_mcp_adapter -v`
