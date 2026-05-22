import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./main";
import { ArtifactWorkbench } from "./components/ArtifactWorkbench";
import { CommandDock } from "./components/CommandDock";
import { MissionTimeline } from "./components/MissionTimeline";
import { NemoMemoryOrbitCopy, type NemoMemoryOrbitEdge, type NemoMemoryOrbitNode } from "./components/NemoMemoryOrbitCopy";
import { TelemetryColumn } from "./components/TelemetryColumn";
import { buildArtifactPromptAttachment } from "./hooks/useGeneratedArtifacts";
import { buildArtifactLineDiff } from "./services/artifactUtils";
import { removeArtifactFromRegistry, toggleArtifactFavorite, type PersistedGeneratedArtifact } from "./services/artifactRegistry";
import { planNemoClient } from "./services/planNemoClient";

const statePayload: any = {
  schema_version: 1,
  product: "Space Code Mission Control",
  repo_path: ".",
  runtimes_path: ".nemo-runtimes",
  repos: [],
  runs: [],
  approval_queue: [],
  settings: {
    model_base_url: "http://localhost:1234/v1",
    default_model: "",
    provider: "subprocess",
    memory_db: ".nemo-runtimes/nemo-memory.sqlite",
    runtime_path: ".nemo-runtimes",
    timeout_seconds: 120,
    max_runtime_minutes: 30,
    heartbeat_minutes: 10,
    max_heartbeats: 1,
    token_budget: 8000,
    context_window_tokens: 131072,
    chat_max_tokens: 16384,
    image_gen_backend: "auto",
    image_gen_url: "",
    validation_policy: "smoke",
    nemo_required: false,
    quality_core: "core",
    recent_repos: [],
  },
};

const queuedRun: any = {
  task_id: "task-1",
  run_id: "run-1",
  objective: "Queued UI patch",
  linked_prd: null,
  linked_specs: [],
  repo_path: "c:/dev/dev4",
  sandbox_path: "c:/dev/dev4/.nemo-runtimes/run-1",
  runtime_id: "runtime-1",
  runtime_state: "reviewing",
  execution_phase: "review",
  validation_profile: "smoke",
  permission_profile: "autonomous_sandbox",
  model_profile: "local-model",
  continuation_state: null,
  grade: "ready",
  score: 100,
  changed_files: ["apps/mission-control/src/main.tsx"],
  event_count: 4,
  artifact_count: 1,
  review_status: "awaiting_review",
  risk_flags: [],
  mergeable: true,
  source_json: "run-1.json",
  timeline: [],
  decision_log: [],
};

let currentStatePayload: any = statePayload;
let gitStatusPayload: any = { repo_path: "c:/dev/dev4", branch: "main", ahead: 0, behind: 0, entries: [] };
let gitBranchesPayload: any = { current: "main", branches: [{ name: "main", current: true }] };
let gitRemotesPayload: any = { remotes: [{ name: "origin", fetch: "x", push: "x" }], default_remote: "origin", default_branch: "main" };
let gitDiffPayload: any = { diff: "", stderr: "" };
let gitSyncShouldFail = false;

afterEach(() => {
  cleanup();
});

const kpiPayload = {
  ok: true,
  kpis: {
    total_runs: 0,
    ready_runs: 0,
    blocked_runs: 0,
    apply_count: 0,
    apply_success_rate: 0,
    blocked_rate: 0,
    auto_apply_rate: 0,
    avg_run_to_apply_minutes: null,
  },
};

const nemoPayload = {
  ok: true,
  health: { enabled: false, status: "disabled", db_path: null },
  selected_run: null,
  context_portfolio: null,
  memory_traces: [],
  used_memories: [],
  corrections: [],
  evidence: [],
  feedback: [],
};

const filePreviewPayload = {
  file_path: "apps/mission-control/src/main.tsx",
  operation: "update",
  source_path: "apps/mission-control/src/main.tsx",
  target_path: "apps/mission-control/src/main.tsx",
  source_content: "export const version = 'new';\n",
  target_content: "export const version = 'old';\n",
  source_hash: "abc",
  target_hash: "def",
  mergeable: true,
  risk_flags: [],
  file_risk_flags: [],
  hunks: [
    {
      id: "hunk-1",
      old_start: 1,
      old_count: 1,
      new_start: 1,
      new_count: 1,
      rows: [{ kind: "context", old_line: 1, new_line: 1, content: "export const version = 'new';" }],
    },
  ],
};

const evalPayload = {
  ok: true,
  grade: "ready",
  score: 1,
  validation_passed: true,
  has_checkpoint: true,
  has_review_package: true,
  memory_writeback_present: true,
  mutation_present: true,
  reasons: [],
};

beforeEach(() => {
  currentStatePayload = statePayload;
  gitStatusPayload = { repo_path: "c:/dev/dev4", branch: "main", ahead: 0, behind: 0, entries: [] };
  gitBranchesPayload = { current: "main", branches: [{ name: "main", current: true }] };
  gitRemotesPayload = { remotes: [{ name: "origin", fetch: "x", push: "x" }], default_remote: "origin", default_branch: "main" };
  gitDiffPayload = { diff: "", stderr: "" };
  gitSyncShouldFail = false;
  window.localStorage.clear();
  const asResponse = (payload: any, ok = true) => ({
    ok,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/state")) {
      return asResponse(currentStatePayload);
    }
    if (url.includes("/api/kpis")) {
      return asResponse(kpiPayload);
    }
    if (url.includes("/api/applies")) {
      return asResponse({ applies: [] });
    }
    if (url.includes("/api/file")) {
      return asResponse(filePreviewPayload);
    }
    if (url.includes("/api/eval")) {
      return asResponse(evalPayload);
    }
    if (url.includes("/api/git/status")) {
      return asResponse(gitStatusPayload);
    }
    if (url.includes("/api/git/branches")) {
      return asResponse(gitBranchesPayload);
    }
    if (url.includes("/api/git/remotes")) {
      return asResponse(gitRemotesPayload);
    }
    if (url.includes("/api/nemo")) {
      return asResponse(nemoPayload);
    }
    if (url.includes("/api/git/diff")) {
      return asResponse(gitDiffPayload);
    }
    if (url.includes("/api/git/sync")) {
      if (gitSyncShouldFail) {
        return asResponse({ error: "git push failed" }, false);
      }
      return asResponse({ ok: true, stdout: "ok", stderr: "" });
    }
    if (url.includes("/api/handoff/start")) {
      return asResponse({ ok: true, job: { job_id: "job-1", status: "running", logs: [], returncode: null } });
    }
    if (url.includes("/api/git/checkout")) {
      return asResponse({ ok: true, stdout: "ok", stderr: "" });
    }
    if (url.includes("/api/git/stage-hunk")) {
      return asResponse({ ok: true });
    }
    if (url.includes("/api/browser/search")) {
      return asResponse({
        ok: true,
        query: "nemo",
        engine: "playwright-chromium",
        search_url: "https://duckduckgo.com/html/?q=nemo",
        results: [{ title: "NEMO Docs", url: "https://example.com/docs", snippet: "Embedded result" }],
        history: ["https://duckduckgo.com/html/?q=nemo"],
        search_history: ["nemo"],
      });
    }
    if (url.includes("/api/browser")) {
      return asResponse({
        homepage: "https://github.com",
        last_url: "",
        history: [],
        search_query: "",
        search_history: [],
      });
    }
    if (url.includes("/api/extensions")) {
      return asResponse({ extensions: [] });
    }
    if (url.includes("/api/self-mod/insights")) {
      return asResponse({ summary: null, trajectory: null, impact: null, similar_runs: { query: "", count: 0, runs: [] } });
    }
    return asResponse({ ok: true });
  }));
});

function buildRegistryArtifact(registryId: string, updatedAt: string): PersistedGeneratedArtifact {
  return {
    id: registryId,
    messageId: "message-1",
    title: registryId,
    kind: "html",
    language: "html_artifact",
    content: `<main>${registryId}</main>`,
    tokenEstimate: 8,
    registryId,
    contentHash: `${registryId}-hash`,
    version: 1,
    versionGroup: "group-1",
    createdAt: updatedAt,
    updatedAt,
    persisted: true,
  };
}

describe('plan job localStorage persistence', () => {
  const KEY = 'sc_active_plan_job'
  beforeEach(() => localStorage.clear())

  it('stores and retrieves a plan job', () => {
    localStorage.setItem(KEY, JSON.stringify({ jobId: 'plan-abc', scores: [5, 8], done: false, startedAt: Date.now() }))
    const raw = localStorage.getItem(KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw!)
    expect(parsed.jobId).toBe('plan-abc')
    expect(parsed.done).toBe(false)
  })

  it('returns null for missing key', () => {
    expect(localStorage.getItem(KEY)).toBeNull()
  })
})

describe("mission-control app", () => {
  it("renders the copied NEMO memory orbit from real dashboard-shaped data", () => {
    const nodes: NemoMemoryOrbitNode[] = [
      { id: "memory-1", mtype: "configuration", importance: 10, color: "#607d8b", title: "Embedding model qwen3-embed-4b", summary: "LM Studio embedding model configuration", tags: ["qwen3"] },
      { id: "memory-2", mtype: "project_decision", importance: 9, color: "#06d6a0", title: "Windows timezone fallback", summary: "Use local timezone fallback on Windows", tags: ["windows"] },
      { id: "memory-3", mtype: "development_checkpoint", importance: 8, color: "#C084FC", title: "Mission Control AAA checkpoint", summary: "Frontend cockpit checkpoint", tags: ["mission-control"] },
    ];
    const edges: NemoMemoryOrbitEdge[] = [
      { source: "memory-1", target: "memory-2", similarity: 0.91, color: "rgba(96,125,139,0.5)", width: 0.1, particles: 1 },
      { source: "memory-2", target: "memory-3", similarity: 0.87, color: "rgba(6,214,160,0.4)", width: 0.08, particles: 1 },
    ];
    const onSelectNode = vi.fn();

    render(<NemoMemoryOrbitCopy nodes={nodes} edges={edges} status="snapshot" onSelectNode={onSelectNode} />);

    expect(screen.getByLabelText("NEMO memory orbit copy")).toBeInTheDocument();
    expect(screen.getByLabelText("3 memories and 2 links")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /configuration memory, importance 10/i }));
    expect(onSelectNode).toHaveBeenCalledWith(nodes[0]);
  });

  it("pins artifact registry entries ahead of ordinary artifacts", () => {
    const registry = [
      buildRegistryArtifact("artifact-a", "2026-05-11T01:00:00Z"),
      buildRegistryArtifact("artifact-b", "2026-05-11T00:00:00Z"),
    ];

    const nextRegistry = toggleArtifactFavorite("artifact-b", registry);

    expect(nextRegistry[0].registryId).toBe("artifact-b");
    expect(nextRegistry[0].favorite).toBe(true);
  });

  it("removes artifact registry entries by stable id", () => {
    const registry = [
      buildRegistryArtifact("artifact-a", "2026-05-11T01:00:00Z"),
      buildRegistryArtifact("artifact-b", "2026-05-11T00:00:00Z"),
    ];

    const nextRegistry = removeArtifactFromRegistry("artifact-a", registry);

    expect(nextRegistry.map((artifact) => artifact.registryId)).toEqual(["artifact-b"]);
  });

  it("renders artifact library search and action controls", () => {
    const onSelect = vi.fn();
    const onAttachToPrompt = vi.fn();
    const onRemoveArtifact = vi.fn();
    const onToggleFavorite = vi.fn();
    const artifacts = [
      buildRegistryArtifact("artifact-html", "2026-05-11T01:00:00Z"),
      { ...buildRegistryArtifact("artifact-json", "2026-05-11T00:00:00Z"), kind: "json" as const, language: "json", title: "Data Packet" },
    ];

    render(<ArtifactWorkbench artifacts={artifacts} activeId="artifact-html" onSelect={onSelect} onAttachToPrompt={onAttachToPrompt} onRemoveArtifact={onRemoveArtifact} onToggleFavorite={onToggleFavorite} renderMarkdown={(content) => <p>{content}</p>} />);
    fireEvent.change(screen.getByPlaceholderText(/Search title/i), { target: { value: "Data" } });
    fireEvent.click(screen.getByRole("button", { name: /Data Packet/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Pin$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/i }));

    expect(onSelect).toHaveBeenCalledWith("artifact-json");
    expect(onToggleFavorite).toHaveBeenCalledWith("artifact-html");
    expect(onRemoveArtifact).toHaveBeenCalledWith("artifact-html");
  });

  it("renders multimedia artifacts in the center stage preview", () => {
    const onSelect = vi.fn();
    const artifacts = [
      { ...buildRegistryArtifact("artifact-game", "2026-05-11T03:00:00Z"), kind: "html" as const, language: "html", title: "Playable Canvas", content: "<main><canvas id='game'></canvas><script>window.ready=true;</script></main>" },
      { ...buildRegistryArtifact("artifact-image", "2026-05-11T02:00:00Z"), kind: "image" as const, language: "image", title: "Concept Frame", content: JSON.stringify({ src: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E", alt: "Concept frame", caption: "Frame preview" }) },
      { ...buildRegistryArtifact("artifact-video", "2026-05-11T01:00:00Z"), kind: "video" as const, language: "video", title: "Gameplay Capture", content: JSON.stringify({ src: "https://example.com/demo.mp4", caption: "Gameplay preview" }) },
      { ...buildRegistryArtifact("artifact-audio", "2026-05-11T00:00:00Z"), kind: "audio" as const, language: "audio", title: "Sound Pass", content: "https://example.com/sound.mp3" },
    ];

    render(<ArtifactWorkbench artifacts={artifacts} activeId="artifact-image" onSelect={onSelect} onAttachToPrompt={vi.fn()} onRemoveArtifact={vi.fn()} onToggleFavorite={vi.fn()} renderMarkdown={(content) => <p>{content}</p>} />);

    expect(screen.getByLabelText(/Live render telemetry/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Artifact runtime deck/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Active artifact source preview/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Artifact performance telemetry/i)).toBeInTheDocument();
    expect(screen.getByAltText("Concept frame")).toBeInTheDocument();
    expect(screen.getByText("Frame preview")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Gameplay Capture/i }));
    expect(onSelect).toHaveBeenCalledWith("artifact-video");
    cleanup();

    render(<ArtifactWorkbench artifacts={artifacts} activeId="artifact-video" onSelect={vi.fn()} onAttachToPrompt={vi.fn()} onRemoveArtifact={vi.fn()} onToggleFavorite={vi.fn()} renderMarkdown={(content) => <p>{content}</p>} />);
    expect(document.querySelector("video")?.getAttribute("src")).toBe("https://example.com/demo.mp4");
    cleanup();

    render(<ArtifactWorkbench artifacts={artifacts} activeId="artifact-audio" onSelect={vi.fn()} onAttachToPrompt={vi.fn()} onRemoveArtifact={vi.fn()} onToggleFavorite={vi.fn()} renderMarkdown={(content) => <p>{content}</p>} />);
    expect(document.querySelector("audio")?.getAttribute("src")).toBe("https://example.com/sound.mp3");
    cleanup();

    render(<ArtifactWorkbench artifacts={artifacts} activeId="artifact-game" onSelect={vi.fn()} onAttachToPrompt={vi.fn()} onRemoveArtifact={vi.fn()} onToggleFavorite={vi.fn()} renderMarkdown={(content) => <p>{content}</p>} />);
    expect(document.querySelector("iframe")?.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("keeps artifact runtime telemetry visible in standby", () => {
    render(<ArtifactWorkbench artifacts={[]} activeId={null} onSelect={vi.fn()} onAttachToPrompt={vi.fn()} onRemoveArtifact={vi.fn()} onToggleFavorite={vi.fn()} renderMarkdown={(content) => <p>{content}</p>} />);

    expect(screen.getByLabelText(/Live render telemetry/i)).toHaveTextContent(/standby/i);
    expect(screen.getByLabelText(/Standby artifact canvas/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/NEMO live render standby scene/i)).toBeInTheDocument();
    expect(screen.getByText(/Mission render armed/i)).toBeInTheDocument();
    expect(screen.getByText(/shader graph/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Artifact runtime deck/i)).toBeInTheDocument();
    expect(screen.getByText(/Awaiting generated artifact/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Artifact performance telemetry/i)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle(/Center target/i));
    expect(screen.getByText(/Target centered/i)).toBeInTheDocument();
  });

  it("exposes command dock controls with accessible labels", () => {
    render(<CommandDock draft="Ship the next sprint" provider="subprocess" endpointLabel="NVIDIA NIM" currentModel="meta/llama-3.1-8b-instruct" running={false} queuedPrompt={null} queuedPrompts={[]} onDraftChange={vi.fn()} onSubmit={vi.fn()} onStop={vi.fn()} onProviderChange={vi.fn()} onModelChange={vi.fn()} onOpenComposer={vi.fn()} onOpenMemory={vi.fn()} />);

    expect(screen.getByLabelText(/Describe the next objective/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Command dock status/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Attach files/i)).toHaveAttribute("type", "file");
    expect(screen.getByText("PLAN")).toBeInTheDocument();
    expect(screen.getByText("STEER")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Enviar objetivo/i })).toBeInTheDocument();
  });

  it("renders mission timeline node rail states", () => {
    render(<MissionTimeline
      messages={[
        { id: "user-1", role: "user", content: "Start the AAA sprint" },
        { id: "assistant-1", role: "assistant", content: "Working with NEMO context", tool_calls: [{ name: "nemo.search_memories", status: "completed" }] },
      ]}
      running
      queuedPrompt={null}
      cleanAssistantContent={(content) => content}
      renderRichText={(content) => <p>{content}</p>}
      renderMcpEvidence={() => null}
      liveStatus={<span>live</span>}
      commandDock={<div />}
    />);

    expect(screen.getByRole("region", { name: /Mission timeline/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/User event 1 of 2/i)).toHaveAttribute("data-state", "settled");
    expect(screen.getByLabelText(/Space Code event 2 of 2/i)).toHaveAttribute("data-state", "active");
    expect(screen.getByLabelText(/Space Code event 2 of 2/i)).toHaveAttribute("data-intent", "evidence");
    expect(screen.getByLabelText(/Evidence event signals/i)).toBeInTheDocument();
    expect(screen.getByText("1 tool calls")).toBeInTheDocument();
    expect(screen.getByLabelText(/Streaming signal/i)).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("renders phase-aware telemetry progress", () => {
    render(<TelemetryColumn
      activeRun={{ objective: "Implement phase progress", review_status: "needs_review", source_json: "run.json", execution_phase: "review", runtime_state: "reviewing", grade: "review" }}
      visibleQueue={[]}
      totalRuns={3}
      readyRuns={1}
      blockedRuns={0}
      queueCount={0}
      running={false}
      contextLabel="portfolio"
      memoryAtomCount={12}
      evidenceCount={4}
      feedbackCount={2}
      sourceReads={8}
      sourceCacheHitRate={75}
      status="Reviewing generated patch"
      onSelectRun={vi.fn()}
      onOpenMemory={vi.fn()}
      onRefreshCognitiveStats={vi.fn()}
      onRefreshMissionStats={vi.fn()}
    />);

    const progress = screen.getByRole("progressbar", { name: /Run phase Review/i });
    expect(progress).toHaveClass("reviewing");
    expect(progress).toHaveAttribute("aria-valuenow", "78");
    expect(screen.getByLabelText(/Active run activity sparkline/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Handoff chain mini map/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Memory field load/i)).toBeInTheDocument();
    expect(screen.getByText("review")).toBeInTheDocument();
    expect(screen.getByText("78%")).toBeInTheDocument();
  });

  it("builds compact artifact version diffs", () => {
    const diff = buildArtifactLineDiff({
      id: "artifact-v1",
      messageId: "message-1",
      title: "Canvas",
      kind: "html",
      language: "html_artifact",
      content: "<main>\n<h1>Old</h1>\n</main>",
      tokenEstimate: 8,
      version: 1,
    }, {
      id: "artifact-v2",
      messageId: "message-2",
      title: "Canvas",
      kind: "html",
      language: "html_artifact",
      content: "<main>\n<h1>New</h1>\n<p>Added</p>\n</main>",
      tokenEstimate: 12,
      version: 2,
    });

    expect(diff.changed).toBe(1);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(0);
    expect(diff.rows.some((row) => row.kind === "changed" && row.left?.includes("Old") && row.right?.includes("New"))).toBe(true);
  });

  it("builds artifact attachments with metadata and exact content", () => {
    const attachment = buildArtifactPromptAttachment({
      id: "artifact-1",
      messageId: "message-1",
      title: "AAA Verification Canvas",
      kind: "html",
      language: "html_artifact",
      content: "<main><h1>Space Code</h1></main>",
      tokenEstimate: 12,
      registryId: "artifact-stable-1",
      contentHash: "abc123ef",
      version: 2,
      versionGroup: "group-1",
      createdAt: "2026-05-11T00:00:00Z",
      updatedAt: "2026-05-11T01:00:00Z",
      persisted: true,
    });

    expect(attachment).toContain("Itera sobre este artifact de Space Code");
    expect(attachment).toContain("Artifact: AAA Verification Canvas");
    expect(attachment).toContain("Version: v2");
    expect(attachment).toContain("Stable ID: artifact-stable-1");
    expect(attachment).toContain("Hash: abc123ef");
    expect(attachment).toContain("Contenido actual del artifact:");
    expect(attachment).toContain("````html_artifact");
    expect(attachment).toContain("<main><h1>Space Code</h1></main>");
  });

  it("renders the shell without crashing", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: /Space Code/i })).toBeInTheDocument();
    expect(await screen.findByLabelText(/Primary navigation/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Mission control status/i)).toBeInTheDocument();
    expect(await screen.findByText(/Agent Runs/i)).toBeInTheDocument();
  });

  it("shows the copied NEMO orbit on the home cockpit without replacing telemetry", async () => {
    render(<App />);

    expect(await screen.findByLabelText("NEMO memory orbit copy")).toBeInTheDocument();
    expect(await screen.findByRole("complementary", { name: /Mission telemetry/i })).toBeInTheDocument();
  });

  it("collapses and restores home cockpit panels from manual controls", async () => {
    render(<App />);

    fireEvent.click(await screen.findByLabelText(/Collapse telemetry/i));
    expect(await screen.findByRole("button", { name: /Telemetry/i })).toBeInTheDocument();
    expect(document.querySelector(".mission-v2-grid")?.className).toContain("collapse-telemetry");

    fireEvent.click(screen.getByRole("button", { name: /Telemetry/i }));
    expect(await screen.findByRole("complementary", { name: /Mission telemetry/i })).toBeInTheDocument();
  });

  it("applies LLM layout actions to focus the artifact stage", async () => {
    window.localStorage.setItem("mission-control-chat-session-v1", JSON.stringify({
      agentMessages: [{
        id: "assistant-layout-1",
        role: "assistant",
        content: "Focusing the artifact stage.",
        actions: [{
          id: "layout-focus-artifact",
          kind: "layout",
          label: "Focus artifact",
          summary: "Collapse side panels and prioritize the artifact canvas",
          payload: { mode: "focus_artifact" },
        }],
      }],
      queuedAgentPrompts: [],
    }));

    render(<App />);

    const layoutControls = await screen.findByLabelText(/Mission layout controls/i);
    await waitFor(() => expect(layoutControls).toHaveAttribute("data-source", "llm"));
    expect(document.querySelector(".mission-v2-grid")?.className).toContain("mode-focus-artifact");
    expect(screen.getAllByRole("button", { name: /Timeline/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Telemetry/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Artifacts generados/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Focus artifact command dock/i)).toBeInTheDocument();
  });

  it("starts handoff with NEMO MCP settings and stable local validation", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Configurar handoff/i));
    expect(await screen.findByRole("region", { name: /New Full Handoff/i })).toBeInTheDocument();
    expect(await screen.findByLabelText(/Provider/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Timeout seconds/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Validation policy/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Acceptance/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Validation$/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Target files/i)).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText(/Handoff objective/i), { target: { value: "Add a small local MVP feature" } });
    fireEvent.click(await screen.findByRole("button", { name: /Start in sandbox/i }));

    const fetchMock = vi.mocked(fetch);
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/api/handoff/start"))).toBe(true);
    });
    const handoffCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/handoff/start"));
    const body = JSON.parse(String((handoffCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.objective).toBe("Add a small local MVP feature");
    expect(body.validation_commands).toBe("npm --prefix apps/mission-control run build");
    expect(body.nemo_mcp_url).toBe("stdio://vscode/nemo");
    expect(body.nemo_mcp_prefix).toBe("nemo.");
    expect(body.require_nemo_mcp_capabilities).toBe(true);
    expect(body.require_nemo_roundtrip).toBe(true);
    expect(body.selected_nemo_tools).toContain("prime_context");
  });

  it("navigates to versioning section", async () => {
    currentStatePayload = {
      ...statePayload,
      runs: [queuedRun],
      approval_queue: [queuedRun],
    };

    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    expect(await screen.findByText(/Repo activo:/i)).toBeInTheDocument();
    expect(await screen.findByText(/^Working tree$/i)).toBeInTheDocument();
  });

  it("opens memory section from home quick action", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Memoria NEMO/i));
    expect(await screen.findByTitle(/Memoria/i)).toHaveClass("active");
  });

  it("exposes settings and repository controls with accessible labels", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Ajustes/i));

    expect(await screen.findByRole("region", { name: /^Ajustes$/i })).toBeInTheDocument();
    expect(await screen.findByLabelText(/URL de LM Studio/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/^Modelo$/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Transporte MCP NEMO/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Repository path/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Git clone URL/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Clone destination path/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/prime_context/i)).toHaveAttribute("type", "checkbox");
  });

  it("performs browser search and shows results", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Browser/i));
    fireEvent.change(await screen.findByPlaceholderText(/Search the web/i), { target: { value: "nemo" } });
    fireEvent.click(await screen.findByRole("button", { name: /Search/i }));
    expect(await screen.findByText(/NEMO Docs/i)).toBeInTheDocument();
  });

  it("executes stage action from versioning working tree", async () => {
    gitStatusPayload = {
      repo_path: "c:/dev/dev4",
      branch: "main",
      ahead: 0,
      behind: 0,
      entries: [{ xy: " M", path: "src/demo.py", original_path: null, staged: false, unstaged: true }],
    };

    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.click(await screen.findByRole("button", { name: /^Stage$/i }));

    const fetchMock = vi.mocked(fetch);
    const stageCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/git/stage"));
    expect(stageCall).toBeTruthy();
    expect((stageCall?.[1] as RequestInit | undefined)?.method).toBe("POST");
  });

  it("routes plan persistence through the Space Code backend NEMO tool bridge", async () => {
    await planNemoClient.syncObjectiveToNemo({
      objective_id: "objective-1",
      title: "Persist safely",
      description: "Use backend MCP bridge",
      acceptance_criteria: ["no direct browser writes"],
      status: "planning",
      created_at: "2026-05-11T00:00:00Z",
      updated_at: "2026-05-11T00:00:00Z",
    });

    const fetchMock = vi.mocked(fetch);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("/api/nemo/tool"))).toBe(true);
    expect(urls.some((url) => url.includes("localhost:8765") || url.includes("/api/memory"))).toBe(false);
  });

  it("executes commit action from versioning panel", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.change(await screen.findByPlaceholderText(/feat: summary/i), { target: { value: "feat: smoke commit" } });
    fireEvent.click(await screen.findByRole("button", { name: /^Commit$/i }));

    const fetchMock = vi.mocked(fetch);
    const commitCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/git/commit"));
    expect(commitCall).toBeTruthy();
    expect((commitCall?.[1] as RequestInit | undefined)?.method).toBe("POST");
  });

  it("executes checkout create action from versioning panel", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.change(await screen.findByPlaceholderText(/feature\/my-branch/i), { target: { value: "feature/smoke" } });
    fireEvent.click(await screen.findByRole("button", { name: /^Create$/i }));

    const fetchMock = vi.mocked(fetch);
    const checkoutCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/git/checkout"));
    expect(checkoutCall).toBeTruthy();
    const body = JSON.parse(String((checkoutCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.branch).toBe("feature/smoke");
    expect(body.create).toBe(true);
  });

  it("executes push sync action from versioning panel", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.click(await screen.findByRole("button", { name: /^Push$/i }));

    const fetchMock = vi.mocked(fetch);
    const syncCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/git/sync"));
    expect(syncCall).toBeTruthy();
    const body = JSON.parse(String((syncCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.direction).toBe("push");
  });

  it("executes stage hunk action from versioning diff panel", async () => {
    gitDiffPayload = {
      diff: "@@ -1,1 +1,1 @@\n-old\n+new\n",
      stderr: "",
    };

    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.change(await screen.findByPlaceholderText(/path opcional/i), { target: { value: "src/demo.py" } });
    fireEvent.click(await screen.findByRole("button", { name: /^Load diff$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^Stage hunk$/i }));

    const fetchMock = vi.mocked(fetch);
    const stageHunkCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/api/git/stage-hunk"));
    expect(stageHunkCall).toBeTruthy();
    const body = JSON.parse(String((stageHunkCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.path).toBe("src/demo.py");
    expect(body.stage).toBe(true);
  });

  it("shows sync error status when git sync API fails", async () => {
    gitSyncShouldFail = true;

    render(<App />);
    fireEvent.click(await screen.findByTitle(/Versionado/i));
    fireEvent.click(await screen.findByRole("button", { name: /^Push$/i }));

    expect((await screen.findAllByText(/git push failed/i)).length).toBeGreaterThan(0);
  });
});
