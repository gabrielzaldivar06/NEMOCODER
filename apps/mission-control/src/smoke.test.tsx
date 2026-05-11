import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./main";

const statePayload: any = {
  schema_version: 1,
  product: "NEMO CODE Mission Control",
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

describe("mission-control app", () => {
  it("renders the shell without crashing", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: /NEMO CODE/i })).toBeInTheDocument();
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
    expect(body.nemo_mcp_url).toBe("http://127.0.0.1:8765/mcp/sse");
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
