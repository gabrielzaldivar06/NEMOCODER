# Mission Control Minimal Artifact Redesign

## Direction

Mission Control should feel like a focused agent IDE: one calm mission canvas, one conversation, one artifact surface. The valuable existing parts stay: NEMO MCP evidence, chat sessions, multimodal attachments, runs, review gates, and workspace control. The mature shift is to stop making the user hunt across panels to understand what the agent produced.

## Principles

- Chat is for intent, steering, and narrative.
- Artifact Studio is for generated outputs: HTML, SVG, Markdown, JSON, and code.
- Tool and memory usage should be visible as compact proof, not noisy logs.
- Runs and diffs remain in the IDE workbench for code review and apply decisions.
- The UI should prefer dense clarity over decorative chrome.

## Current Vertical Slice

- Home now uses a two-pane Mission Canvas: conversation plus Artifact Studio.
- Assistant fenced code blocks become first-class artifacts.
- HTML and SVG artifacts render in a sandboxed iframe.
- Markdown artifacts render with the chat rich-text renderer.
- JSON and generic code artifacts render as source.
- NEMO MCP usage appears as a compact verification strip on assistant messages.

## Frontier Cockpit V2 Slice

- Home has been promoted from a dashboard section into a cockpit surface.
- The cockpit includes a mission header, mission timeline, artifact workbench, telemetry column, and command dock.
- The mission timeline renders user and NEMO events on a connected node rail instead of plain chat cards.
- The command dock preserves multimodal attachment, handoff, memory, model/provider, queue, steer, hold, direct, and plan actions.
- The telemetry column presents active run, handoff chain, memory field, approval queue, and system signal as compact trust panels.
- Motion primitives were added for node pulse, signal waves, memory orbit, and command dock breathing, with `prefers-reduced-motion` support.
- Responsive behavior collapses the cockpit from a three-column desktop grid into a single-column tablet/mobile layout while preserving session controls and command actions.
- Browser verification confirmed the V2 cockpit renders on `127.0.0.1:5173`, artifact iframe appears, NEMO MCP proof remains visible, and raw artifact HTML is not shown in the timeline.

## Artifact Workbench V2 Slice

- Artifact Studio now behaves as a workbench rather than a passive preview panel.
- Each selected artifact supports Preview, Source, and Inspect modes.
- Preview renders HTML/SVG in a sandboxed iframe and Markdown through the rich-text renderer.
- Source exposes the exact generated artifact content without leaking it into the conversation timeline.
- Inspect shows artifact kind, language, size, estimated tokens, line count, and originating assistant message.
- Toolbar actions now support copy, browser download, and attaching the selected artifact as context for the next command.
- Browser verification confirmed the workbench modes are interactive, Attach writes a useful draft reference, API 500 errors disappear when the backend is running, and the timeline has no horizontal overflow.

## AAA Implementation Baseline

- The target quality bar is the supplied frontier IDE cockpit captures: rail, causal timeline, central artifact workbench, telemetry column, memory field, and command dock as one coherent operating surface.
- CSS is not treated as the implementation method by itself; it is the presentation layer on top of component boundaries, data contracts, artifact persistence, semantic motion, accessibility, and visual QA.
- Phase 1 starts by shrinking the monolith safely: `ArtifactWorkbench` has been extracted from `main.tsx` into a dedicated component boundary while preserving Preview, Source, Inspect, Copy, Save, Attach, artifact parsing, and raw-code stripping behavior.
- `MissionTimeline` and `CommandDock` have also been extracted as component boundaries. The timeline keeps conversation rendering, artifact stripping, NEMO MCP proof slots, and live status slots; the dock owns local attachment state and radial send actions while preserving send, queue, steer, plan, stop, provider, handoff, and memory controls.
- `TelemetryColumn` is now extracted as a component boundary for active run, handoff chain, memory field, approval queue, and system signal. It owns telemetry presentation helpers while `MissionHome` supplies derived runtime, queue, source, memory, and handler data.
- Post-extraction validation: TypeScript diagnostics are clean, `npm --prefix apps/mission-control run build` passes, and browser validation with a seeded artifact session confirms timeline, CommandDock, Artifact Studio iframe, Preview/Source/Inspect controls, visible NEMO MCP proof, no raw HTML leak, and zero horizontal overflow in the page, timeline, and dock.
- Artifact state is now isolated in `useGeneratedArtifacts`, centralizing message-to-artifact collection, active artifact selection, and attach-to-draft behavior. This keeps `MissionHome` as an orchestrator and creates the seam for backend-persisted artifact IDs, evidence handles, and version history.
- Artifact Studio now has a frontend artifact registry in `services/artifactRegistry.ts`. Generated artifacts are indexed into `localStorage` with stable registry IDs, content hashes, version groups, version numbers, created/updated timestamps, and a capped persisted library. The workbench exposes version chips and Inspect metadata for Stable ID, Hash, and Version.
- Post-registry validation: TypeScript diagnostics are clean, `npm --prefix apps/mission-control run build` passes, and browser validation with two seeded HTML artifact versions plus backend active confirms Artifact Studio iframe, v1/v2 version strip, stable registry records in `mission-control-artifact-registry-v1`, visible NEMO MCP proof, no raw HTML leak, and zero horizontal overflow in the page, timeline, and dock.
- Useful changes from Claude worktree `suspicious-wilson-c89bc0` were integrated selectively without replacing the AAA cockpit: Artifact Studio now recognizes `html_artifact`, `svg_artifact`, `mermaid`, `react_artifact`, and `image_request` fences; artifact titles can be declared with `<!-- ARTIFACT:Title:type -->`; Mermaid diagrams render in-preview; React artifacts render in a sandboxed iframe; image requests render as structured prompt cards. The Mission Control backend prompt now instructs the model to emit these typed artifact fences for visual outputs.
- Post-worktree integration validation: TypeScript diagnostics are clean, `npm --prefix apps/mission-control run test:smoke` passes, `npm --prefix apps/mission-control run build` passes, and browser validation with seeded HTML, Mermaid, React, and image request artifacts confirms all four are indexed in the registry, previews render, NEMO MCP proof remains visible, raw fences are stripped from the timeline, and body/timeline/dock overflow remains zero. The smoke test now expects the current NEMO MCP transport `stdio://vscode/nemo` instead of the legacy SSE endpoint.
- The next target is promoting this registry contract to backend-persisted artifact entities and visual regression tests.

## Next Iterations

- Persist artifacts with stable IDs from the backend instead of deriving them from message content.
- Add deeper artifact actions: save to workspace, open as file, compare versions, and request scoped revision.
- Add multimodal artifact slots for generated images/audio/video once backend payloads include media metadata.
- Link artifacts to NEMO evidence handles and run checkpoints.
- Add inline artifact revision prompts scoped to the selected artifact.