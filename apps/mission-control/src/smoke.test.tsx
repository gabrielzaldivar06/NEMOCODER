import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./main";
import { ArtifactWorkbench } from "./components/ArtifactWorkbench";
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
    default_model: "nvidia.agentic.coder-4b",
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

describe("mission-control app", () => {
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
    expect(await screen.findByText(/Agent Runs/i)).toBeInTheDocument();
  });

  it("starts handoff with NEMO MCP settings and stable local validation", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTitle(/Configurar handoff/i));
    fireEvent.change(await screen.findByPlaceholderText(/Describe the PRD/i), { target: { value: "Add a small local MVP feature" } });
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

    expect(await screen.findByText(/git push failed/i)).toBeInTheDocument();
  });
});
