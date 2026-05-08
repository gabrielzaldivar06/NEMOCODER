# Spec: MCP Extension Governance

## Purpose

Define how MCP tools and extension-like capabilities are exposed, validated, and governed across Mission Control and the MCP server boundary.

## Governance Scope

- Tool surface published by MCP server.
- Dynamic inclusion of NEMO registry tools.
- Mission Control extension settings and update actions.
- Safe tool dispatch and error handling semantics.

## Required Controls

1. Registry-backed discovery
- Tool list must include static platform tools and dynamic NEMO tools from registry.
- Duplicate tool names are forbidden.

2. Namespacing
- MCP tools exposed by platform must use `nemocode.` prefix.
- Unknown tool names return deterministic tool-not-found errors.

3. Phase and policy context
- NEMO tool calls must carry lifecycle/phase context when applicable.
- Disabled memory DB mode must return explicit skipped/disabled semantics.

4. Extension configuration boundary
- Extensions are managed through explicit actions: toggle/register/remove.
- Invalid actions fail with deterministic request errors.
- Extension configuration is normalized and persisted in Mission Control settings.

## Runtime Requirements

- MCP server must expose SSE and message endpoints.
- `tools/list` and `tools/call` must be supported.
- Tool call errors must be structured and non-crashing.

## Acceptance Criteria

- Contract tests prove all NEMO registry tools appear in MCP tool definitions.
- Contract tests prove MCP adapter can call an exposed tool.
- Contract tests prove extension actions are validated and normalized.