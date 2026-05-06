import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { AlertTriangle, ArrowUp, Bell, Bot, CheckCircle2, ChevronDown, Circle, Clock3, Code2, Database, FileCode2, Files, GitBranch, GitCompare, GitPullRequest, HardDrive, Home, MessageSquareText, PanelBottom, Play, Plus, RefreshCw, RotateCcw, Search, Send, Settings, ShieldCheck, TerminalSquare, Wrench } from "lucide-react";
import "./styles.css";

type TimelineEvent = {
  sequence: number | null;
  phase: string | null;
  kind: string | null;
  summary: string | null;
  payload_ref: string | null;
};

type MissionRun = {
  task_id: string;
  run_id: string;
  objective: string;
  repo_path: string;
  sandbox_path: string;
  runtime_id: string;
  runtime_state: string;
  execution_phase: string;
  validation_profile: string;
  permission_profile: string;
  model_profile: string;
  continuation_state: {
    resume_token?: string | null;
    resume_minute?: number | null;
    provider_mode?: string | null;
    validation_policy?: string | null;
    validation_commands?: string[];
    checkpoint_refs?: string[];
    heartbeat_refs?: string[];
    memory_writeback_handles?: string[];
    escalation_flags?: string[];
    budget?: Record<string, unknown>;
    error?: string;
  } | null;
  grade: string;
  score: number;
  changed_files: string[];
  event_count: number;
  artifact_count: number;
  review_status: string;
  risk_flags: string[];
  mergeable: boolean;
  source_json: string;
  timeline: TimelineEvent[];
};

type MissionState = {
  schema_version: number;
  product: string;
  repo_path: string;
  runtimes_path: string;
  repos: string[];
  runs: MissionRun[];
  approval_queue: MissionRun[];
  settings: {
    model_base_url: string;
    default_model: string;
    provider: string;
    memory_db: string;
    runtime_path: string;
    timeout_seconds: number;
    max_runtime_minutes: number;
    heartbeat_minutes: number;
    max_heartbeats: number;
    token_budget: number;
    validation_policy: string;
    nemo_required: boolean;
    quality_core: string;
    recent_repos: string[];
  };
};

type ApiError = { error: string };
type ApplyResult = { apply_json: string; state: MissionState };
type ReviewPlan = { mergeable: boolean; risk_flags: string[]; files: Array<{ path: string; operation: string; target_hash: string | null; source_hash: string | null }> };
type ApplyHistoryItem = { path: string; task_id: string; run_id: string; applied_files: string[]; backup_root: string | null; backup_files: string[]; created_at: string };
type ApplyHistoryResult = { applies: ApplyHistoryItem[] };
type CleanupResult = { dry_run: boolean; candidates: string[]; deleted: string[]; max_age_days: number };
type HandoffJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  run_json: string;
  status: string;
  returncode: number | null;
  error: string | null;
  logs: string[];
};
type HandoffJobResult = { job: HandoffJob; state?: MissionState };
type AgentToolCall = {
  id: string;
  name: string;
  status: string;
  summary: string;
};
type AgentAction = {
  id: string;
  kind: "continue" | "revise" | "apply";
  label: string;
  summary: string;
  payload: Record<string, unknown>;
};
type AgentMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: AgentToolCall[];
  actions?: AgentAction[];
};
type AgentMessageResult = { message: AgentMessage };
type DiffRow = {
  kind: "context" | "insert" | "delete";
  old_line: number | null;
  new_line: number | null;
  content: string;
};
type DiffHunk = {
  id: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  rows: DiffRow[];
};
type FilePreview = {
  file_path: string;
  operation: string;
  source_path: string;
  target_path: string;
  source_content: string | null;
  target_content: string | null;
  source_hash: string | null;
  target_hash: string | null;
  mergeable: boolean;
  risk_flags: string[];
  file_risk_flags: string[];
  hunks: DiffHunk[];
};
type NemoMemory = {
  id: string;
  type: string;
  content: string;
  source_scope: string;
  topic: string;
  tags: string[];
  importance: number;
  evidence_handle: string | null;
  access_count: number;
  useful_count: number;
  not_useful_count: number;
};
type NemoEvidence = {
  handle: string;
  compact_claim: string;
  source_task_id: string | null;
  source_run_id: string | null;
  retrieval_count: number;
};
type NemoFeedback = {
  id: string;
  atom_id: string | null;
  evidence_handle: string | null;
  event_type: string;
  was_useful: number | null;
  token_delta: number;
  created_at: string;
};
type NemoState = {
  ok: boolean;
  health: {
    enabled: boolean;
    status: string;
    db_path: string | null;
    atom_count?: number;
    evidence_count?: number;
    feedback_count?: number;
  };
  selected_run: { task_id?: string; run_id?: string; objective?: string; source_json?: string } | null;
  context_portfolio: {
    context?: string;
    estimated_tokens?: number;
    evidence_handles?: string[];
    memory_atom_ids?: string[];
  } | null;
  memory_traces: Array<{ nemo_tool?: string; summary?: string; payload_ref?: string }>;
  used_memories: NemoMemory[];
  corrections: NemoMemory[];
  evidence: NemoEvidence[];
  feedback: NemoFeedback[];
};

function normalizeFilePreview(payload: FilePreview): FilePreview {
  return {
    ...payload,
    risk_flags: payload.risk_flags ?? [],
    file_risk_flags: payload.file_risk_flags ?? [],
    hunks: payload.hunks ?? [],
    mergeable: payload.mergeable ?? false,
  };
}

function normalizeSettings(settings: Partial<MissionState["settings"]> | undefined): MissionState["settings"] {
  return { ...fallbackState.settings, ...(settings ?? {}) };
}

function normalizeRun(run: MissionRun): MissionRun {
  return {
    ...run,
    runtime_id: run.runtime_id ?? "",
    runtime_state: run.runtime_state ?? "",
    execution_phase: run.execution_phase ?? "",
    validation_profile: run.validation_profile ?? "",
    permission_profile: run.permission_profile ?? "",
    model_profile: run.model_profile ?? "",
    continuation_state: run.continuation_state ?? null,
  };
}

function normalizeState(payload: MissionState): MissionState {
  return { ...payload, runs: (payload.runs ?? []).map(normalizeRun), approval_queue: (payload.approval_queue ?? []).map(normalizeRun), settings: normalizeSettings(payload.settings) };
}

const fallbackState: MissionState = {
  schema_version: 1,
  product: "NEMO Desktop Mission Control",
  repo_path: "c:/dev/dev4",
  runtimes_path: "c:/dev/dev4/.nemo-runtimes",
  repos: ["c:/dev/dev4"],
  runs: [],
  approval_queue: [],
  settings: {
    model_base_url: "http://localhost:1234/v1",
    default_model: "nvidia.agentic.coder-4b",
    provider: "fake",
    memory_db: ".nemo-runtimes/nemo-memory.sqlite",
    runtime_path: "c:/dev/dev4/.nemo-runtimes",
    timeout_seconds: 120,
    max_runtime_minutes: 120,
    heartbeat_minutes: 15,
    max_heartbeats: 4,
    token_budget: 32000,
    validation_policy: "smoke",
    nemo_required: true,
    quality_core: "product/aider",
    recent_repos: ["c:/dev/dev4"],
  },
};

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

function statusTone(status: string): string {
  if (status === "awaiting_review") return "ready";
  if (status === "blocked") return "blocked";
  if (status === "running") return "running";
  return "quiet";
}

function App() {
  const [state, setState] = useState<MissionState>(fallbackState);
  const [selectedRunSource, setSelectedRunSource] = useState<string>("");
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [nemoState, setNemoState] = useState<NemoState | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<MissionState["settings"]>(fallbackState.settings);
  const [repoDraft, setRepoDraft] = useState<string>(fallbackState.repo_path);
  const [cloneDraft, setCloneDraft] = useState({ url: "", destination: "" });
  const [reviewPlan, setReviewPlan] = useState<ReviewPlan | null>(null);
  const [applyHistory, setApplyHistory] = useState<ApplyHistoryItem[]>([]);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);
  const [hunkDecisions, setHunkDecisions] = useState<Record<string, Record<string, boolean>>>({});
  const [status, setStatus] = useState<string>("Connecting to local bridge");
  const [applyResults, setApplyResults] = useState<Record<string, string>>({});
  const [composerOpen, setComposerOpen] = useState<boolean>(false);
  const [handoffRunning, setHandoffRunning] = useState<boolean>(false);
  const [activeJob, setActiveJob] = useState<HandoffJob | null>(null);
  const [agentMessages, setAgentMessages] = useState<AgentMessage[]>([
    {
      id: "welcome-agent-message",
      role: "assistant",
      content: "Ask for a continuation, revision, or apply decision on the selected run.",
      actions: [],
      tool_calls: [],
    },
  ]);
  const [agentDraft, setAgentDraft] = useState<string>("");
  const [homeAgentDraft, setHomeAgentDraft] = useState<string>("");
  const [agentBusy, setAgentBusy] = useState<boolean>(false);
  const [handoffDraft, setHandoffDraft] = useState({
    objective: "",
    acceptance: "passes validation",
    validation: "python -m unittest",
    validationPolicy: "smoke",
    targetFiles: "",
    provider: "fake",
    timeoutSeconds: "120",
  });

  const selectedRun = useMemo(() => state.runs.find((run) => run.source_json === selectedRunSource) ?? state.runs[0], [state.runs, selectedRunSource]);
  const readyRuns = state.runs.filter((run) => run.review_status === "awaiting_review").length;
  const blockedRuns = state.runs.filter((run) => run.review_status === "blocked").length;
  const activeFile = selectedFile || selectedRun?.changed_files[0] || "";

  const postJson = <T extends object,>(path: string, body: object): Promise<T> => fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (response) => {
    const payload = await response.json() as T | ApiError;
    if (!response.ok) throw new Error("error" in payload ? payload.error : response.statusText);
    return payload as T;
  });

  const refreshState = () => {
    setStatus("Refreshing workspace state");
    fetch("/api/state", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : fetch("/mission-control-state.sample.json", { cache: "no-store" }).then((fallback) => fallback.json()))
      .then((payload: MissionState) => {
        const nextState = normalizeState(payload);
        setState(nextState);
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        const nextRun = nextState.runs.find((run) => run.source_json === selectedRunSource) ?? nextState.runs[0];
        setSelectedRunSource(nextRun?.source_json ?? "");
        setSelectedFile(nextRun?.changed_files[0] ?? "");
        setStatus("Workspace state refreshed");
      })
      .catch(() => {
        setState(fallbackState);
        setStatus("Bridge offline, showing fallback state");
      });
  };

  const refreshApplyHistory = () => {
    fetch("/api/applies", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload: ApplyHistoryResult) => setApplyHistory(payload.applies ?? []))
      .catch(() => setApplyHistory([]));
  };

  const loadFilePreview = (run: MissionRun | undefined, filePath: string) => {
    if (!run || !filePath) {
      setFilePreview(null);
      return;
    }
    setFilePreview(null);
    setStatus(`Opening ${filePath}`);
    postJson<FilePreview>("/api/file", { source_json: run.source_json, file_path: filePath })
      .then((payload) => {
        const preview = normalizeFilePreview(payload);
        setFilePreview(preview);
        setHunkDecisions((current) => {
          if (current[preview.file_path]) return current;
          return { ...current, [preview.file_path]: Object.fromEntries(preview.hunks.map((hunk) => [hunk.id, true])) };
        });
        setStatus(`${preview.operation} preview ready: ${preview.file_path}`);
      })
      .catch((error: Error) => {
        setFilePreview(null);
        setStatus(error.message);
      });
  };

  const loadNemoState = (run: MissionRun | undefined) => {
    postJson<NemoState>("/api/nemo", { source_json: run?.source_json ?? "" })
      .then((payload) => setNemoState(payload))
      .catch(() => setNemoState(null));
  };

  const selectRun = (run: MissionRun) => {
    const nextFile = run.changed_files[0] ?? "";
    setSelectedRunSource(run.source_json);
    setSelectedFile(nextFile);
    loadFilePreview(run, nextFile);
    loadNemoState(run);
  };

  const selectFile = (filePath: string) => {
    setSelectedFile(filePath);
    loadFilePreview(selectedRun, filePath);
  };

  const reviewRun = (run: MissionRun) => {
    setStatus("Building merge plan");
    postJson<{ plan: ReviewPlan }>("/api/review", { source_json: run.source_json })
      .then((payload) => {
        setReviewPlan(payload.plan);
        setStatus(payload.plan.mergeable ? `Merge plan ready: ${payload.plan.files.length} files` : `Review blocked: ${payload.plan.risk_flags.join(", ")}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const applyRun = (run: MissionRun) => {
    setStatus("Preparing apply plan");
    postJson<{ plan: ReviewPlan }>("/api/review", { source_json: run.source_json })
      .then((payload) => {
        setReviewPlan(payload.plan);
        const files = payload.plan.files.map((file) => `${file.operation}: ${file.path}`).join("\n");
        const risks = payload.plan.risk_flags.length ? `\nRisks: ${payload.plan.risk_flags.join(", ")}` : "";
        if (!payload.plan.mergeable) throw new Error(`Review blocked: ${payload.plan.risk_flags.join(", ")}`);
        if (!window.confirm(`Apply plan for ${run.run_id}\n\n${files}${risks}\n\nTarget hashes will be verified before write.`)) return null;
        setStatus("Applying reviewed run");
        return postJson<ApplyResult>("/api/apply", { source_json: run.source_json, approve_review: true });
      })
      .then((payload) => {
        if (!payload) return;
        setApplyResults((current) => ({ ...current, [run.source_json]: payload.apply_json }));
        setState(payload.state);
        refreshApplyHistory();
        setStatus(`Applied run. Rollback JSON: ${payload.apply_json}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const applySelectedDiff = (run: MissionRun, preview: FilePreview) => {
    const accepted = Object.entries(hunkDecisions[preview.file_path] ?? {})
      .filter(([, accepted]) => accepted)
      .map(([hunkId]) => hunkId);
    if (accepted.length === 0) {
      setStatus("No accepted hunks selected for apply");
      return;
    }
    if (!window.confirm(`Apply ${accepted.length} accepted hunk(s) from ${preview.file_path}?`)) return;
    setStatus("Applying selected hunks");
    postJson<ApplyResult>("/api/apply-selection", { source_json: run.source_json, approve_review: true, accepted_hunks: { [preview.file_path]: accepted } })
      .then((payload) => {
        setApplyResults((current) => ({ ...current, [run.source_json]: payload.apply_json }));
        setState(payload.state);
        refreshApplyHistory();
        setStatus(`Applied selected hunks. Rollback JSON: ${payload.apply_json}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const setFileDecision = (preview: FilePreview, accepted: boolean) => {
    setHunkDecisions((current) => ({
      ...current,
      [preview.file_path]: Object.fromEntries(preview.hunks.map((hunk) => [hunk.id, accepted])),
    }));
  };

  const toggleHunkDecision = (filePath: string, hunkId: string) => {
    setHunkDecisions((current) => ({
      ...current,
      [filePath]: { ...(current[filePath] ?? {}), [hunkId]: !(current[filePath]?.[hunkId] ?? true) },
    }));
  };

  const rollbackRun = (run: MissionRun) => {
    const applyJson = applyResults[run.source_json] || window.prompt("Apply result JSON path");
    if (!applyJson) return;
    if (!window.confirm(`Rollback apply result for ${run.run_id}?`)) return;
    setStatus("Rolling back apply result");
    postJson<{ state: MissionState }>("/api/rollback", { apply_json: applyJson, approve_review: true })
      .then((payload) => {
        setState(payload.state);
        refreshApplyHistory();
        setStatus("Rollback completed");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const startHandoff = () => {
    if (!handoffDraft.objective.trim()) {
      setStatus("Objective is required before starting a handoff");
      return;
    }
    if (handoffRunning) return;
    setHandoffRunning(true);
    setStatus("Starting async handoff job");
    postJson<HandoffJobResult>("/api/handoff/start", {
      objective: handoffDraft.objective,
      acceptance_criteria: handoffDraft.acceptance,
      validation_commands: handoffDraft.validation,
      validation_policy: handoffDraft.validationPolicy,
      target_files: handoffDraft.targetFiles,
      provider: handoffDraft.provider || settingsDraft.provider,
      timeout_seconds: handoffDraft.timeoutSeconds || settingsDraft.timeout_seconds,
      model: settingsDraft.default_model,
      base_url: settingsDraft.model_base_url,
      max_runtime_minutes: settingsDraft.max_runtime_minutes,
      heartbeat_minutes: settingsDraft.heartbeat_minutes,
      max_heartbeats: settingsDraft.max_heartbeats,
      token_budget: settingsDraft.token_budget,
    })
      .then((payload) => {
        setActiveJob(payload.job);
        setComposerOpen(false);
        setStatus(`Job started: ${payload.job.job_id}`);
      })
      .catch((error: Error) => setStatus(error.message))
      .finally(() => setHandoffRunning(false));
  };

  const pollJob = (job: HandoffJob) => {
    postJson<HandoffJobResult>("/api/job", { job_id: job.job_id })
      .then((payload) => {
        setActiveJob(payload.job);
        if (payload.state) setState(payload.state);
        if (payload.job.status === "completed") {
          const nextRun = payload.state?.runs.find((run) => run.source_json === payload.job.run_json) ?? payload.state?.runs[0];
          setSelectedRunSource(nextRun?.source_json ?? selectedRunSource);
          setSelectedFile(nextRun?.changed_files[0] ?? selectedFile);
          setStatus(`Job completed: ${payload.job.run_json}`);
        } else {
          setStatus(`Job ${payload.job.status}: ${payload.job.job_id}`);
        }
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const controlJob = (action: "cancel" | "pause" | "resume") => {
    if (!activeJob) return;
    postJson<HandoffJobResult>(`/api/job/${action}`, { job_id: activeJob.job_id })
      .then((payload) => {
        setActiveJob(payload.job);
        setStatus(`Job ${payload.job.status}: ${payload.job.job_id}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const sendAgentMessage = (contentOverride?: string) => {
    const content = (contentOverride ?? agentDraft).trim();
    if (!content || agentBusy) return;
    const userMessage: AgentMessage = { id: `user-${Date.now()}`, role: "user", content };
    setAgentMessages((current) => [...current, userMessage]);
    if (contentOverride === undefined) setAgentDraft("");
    setAgentBusy(true);
    setStatus("Agent is inspecting context");
    postJson<AgentMessageResult>("/api/agent/message", {
      message: content,
      source_json: selectedRun?.source_json,
      provider: settingsDraft.provider,
      model_base_url: settingsDraft.model_base_url,
      default_model: settingsDraft.default_model,
      timeout_seconds: handoffDraft.timeoutSeconds,
    })
      .then((payload) => {
        setAgentMessages((current) => [...current, payload.message]);
        setStatus("Agent proposed next actions");
      })
      .catch((error: Error) => setStatus(error.message))
      .finally(() => setAgentBusy(false));
  };

  const sendHomeAgentMessage = () => {
    const content = homeAgentDraft.trim();
    if (!content || agentBusy) return;
    setHomeAgentDraft("");
    sendAgentMessage(content);
  };

  const runAgentAction = (action: AgentAction) => {
    if (action.kind === "apply") {
      if (!window.confirm("Apply the selected reviewed run to the workspace?")) return;
      setStatus("Applying agent-proposed action");
      postJson<ApplyResult>("/api/apply", action.payload)
        .then((payload) => {
          const sourceJson = String(action.payload.source_json || selectedRun?.source_json || "");
          if (sourceJson) setApplyResults((current) => ({ ...current, [sourceJson]: payload.apply_json }));
          setState(payload.state);
          setStatus(`Applied run. Rollback JSON: ${payload.apply_json}`);
        })
        .catch((error: Error) => setStatus(error.message));
      return;
    }
    setStatus(`Starting agent action: ${action.label}`);
    postJson<HandoffJobResult>("/api/handoff/start", action.payload)
      .then((payload) => {
        setActiveJob(payload.job);
        setStatus(`Job started: ${payload.job.job_id}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const saveSettings = () => {
    setStatus("Saving settings");
    postJson<{ settings: MissionState["settings"]; state: MissionState }>("/api/settings", settingsDraft)
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setSettingsDraft(normalizeSettings(payload.settings));
        setState(nextState);
        setStatus("Settings saved");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const setProviderMode = (provider: string) => {
    const nextSettings = { ...settingsDraft, provider };
    setSettingsDraft(nextSettings);
    setHandoffDraft((current) => ({ ...current, provider }));
    setStatus(provider === "subprocess" ? "Switching chat to LM Studio" : "Switching chat to fake planner");
    postJson<{ settings: MissionState["settings"]; state: MissionState }>("/api/settings", nextSettings)
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setSettingsDraft(normalizeSettings(payload.settings));
        setState(nextState);
        setStatus(provider === "subprocess" ? "Real mode enabled: LM Studio" : "Fake planner mode enabled");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const openRepo = (repoPath = repoDraft) => {
    setStatus("Opening repository");
    postJson<{ state: MissionState }>("/api/repo/open", { repo_path: repoPath })
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        setStatus(`Opened repo: ${nextState.repo_path}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const cloneRepo = () => {
    setStatus("Cloning repository");
    postJson<{ state: MissionState }>("/api/repo/clone", cloneDraft)
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        setCloneDraft({ url: "", destination: "" });
        setStatus(`Cloned and opened repo: ${nextState.repo_path}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const cleanupArtifacts = (dryRun: boolean) => {
    setStatus(dryRun ? "Scanning artifacts" : "Cleaning artifacts");
    postJson<CleanupResult>("/api/artifacts/cleanup", { dry_run: dryRun, max_age_days: 7 })
      .then((payload) => {
        setCleanupResult(payload);
        setStatus(dryRun ? `Cleanup scan found ${payload.candidates.length} file(s)` : `Deleted ${payload.deleted.length} artifact file(s)`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  useEffect(() => {
    refreshState();
    refreshApplyHistory();
  }, []);

  useEffect(() => {
    if (selectedRun && activeFile) loadFilePreview(selectedRun, activeFile);
    loadNemoState(selectedRun);
  }, [selectedRunSource]);

  useEffect(() => {
    if (!activeJob || !["starting", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => pollJob(activeJob), 1500);
    return () => window.clearInterval(timer);
  }, [activeJob?.job_id, activeJob?.status]);

  return (
    <main className="ide-shell">
      <aside className="activity-bar" aria-label="Activity bar">
        <button className="activity active" title="Explorer"><Files size={20} /></button>
        <button className="activity" title="Search"><Search size={20} /></button>
        <button className="activity" title="Runs"><GitBranch size={20} /></button>
        <button className="activity" title="Agent"><MessageSquareText size={20} /></button>
        <button className="activity" title="Settings"><Settings size={20} /></button>
      </aside>

      <aside className="explorer">
        <div className="brand-row">
          <Bot size={20} />
          <div>
            <h1>NEMO Code</h1>
            <p>Aider core + NEMO memory</p>
          </div>
        </div>

        <section className="repo-strip">
          <div className="section-heading"><ChevronDown size={14} /> Workspace</div>
          {state.repos.map((repo) => <button className="repo-item" key={repo} onClick={() => openRepo(repo)} title="Open repo"><HardDrive size={14} /> {repo}</button>)}
        </section>

        <section className="run-tree">
          <div className="section-heading"><ChevronDown size={14} /> Agent Runs</div>
          {state.runs.length === 0 ? <EmptyState /> : state.runs.map((run) => (
            <div className={`tree-run ${selectedRun?.source_json === run.source_json ? "selected" : ""}`} key={run.source_json}>
              <button className="tree-run-button" onClick={() => selectRun(run)}>
                <Circle size={9} className={statusTone(run.review_status)} />
                <span>{run.objective}</span>
              </button>
              {selectedRun?.source_json === run.source_json && <div className="file-tree">
                {run.changed_files.length === 0 ? <span className="tree-empty">No changed files</span> : run.changed_files.map((file) => (
                  <button className={activeFile === file ? "active" : ""} key={file} onClick={() => selectFile(file)}>
                    <FileCode2 size={14} /> {file}
                  </button>
                ))}
              </div>}
            </div>
          ))}
        </section>
      </aside>

      <section className="workbench">
        <header className="command-bar">
          <div className="command-title">
            <Code2 size={18} />
            <span>{selectedRun?.objective ?? "No run selected"}</span>
          </div>
          <div className="command-actions">
            <button onClick={refreshState} title="Refresh state"><RefreshCw size={16} /> Refresh</button>
            <button onClick={() => setComposerOpen((open) => !open)} title="Start handoff"><Play size={16} /> New Handoff</button>
          </div>
        </header>

        {composerOpen && <HandoffComposer draft={handoffDraft} onChange={setHandoffDraft} onSubmit={startHandoff} onClose={() => setComposerOpen(false)} running={handoffRunning} />}

        <MissionHome
          state={state}
          readyRuns={readyRuns}
          blockedRuns={blockedRuns}
          nemoState={nemoState}
          status={status}
          draft={homeAgentDraft}
          provider={settingsDraft.provider}
          messages={agentMessages}
          onDraftChange={setHomeAgentDraft}
          onSubmit={sendHomeAgentMessage}
          onProviderChange={setProviderMode}
          onOpenComposer={() => setComposerOpen(true)}
          running={agentBusy}
        />

        <div className="tab-row">
          <button className="tab active"><FileCode2 size={14} /> {activeFile || "welcome.md"}</button>
          <button className="tab"><GitCompare size={14} /> Review</button>
          <button className="tab"><MessageSquareText size={14} /> Agent</button>
        </div>

        <div className="workspace-main">
          <EditorPane
            run={selectedRun}
            filePreview={filePreview}
            activeFile={activeFile}
            decisions={filePreview ? hunkDecisions[filePreview.file_path] ?? {} : {}}
            onToggleHunk={toggleHunkDecision}
            onSetFileDecision={setFileDecision}
            onApplySelected={applySelectedDiff}
          />
          <AgentPane
            run={selectedRun}
            state={state}
            readyRuns={readyRuns}
            blockedRuns={blockedRuns}
            applyJson={selectedRun ? applyResults[selectedRun.source_json] : undefined}
            onReview={reviewRun}
            onApply={applyRun}
            onRollback={rollbackRun}
            messages={agentMessages}
            draft={agentDraft}
            busy={agentBusy}
            onDraftChange={setAgentDraft}
            onSend={sendAgentMessage}
            onRunAction={runAgentAction}
            nemoState={nemoState}
            settingsDraft={settingsDraft}
            onSettingsChange={setSettingsDraft}
            onSaveSettings={saveSettings}
            repoDraft={repoDraft}
            onRepoDraftChange={setRepoDraft}
            onOpenRepo={openRepo}
            cloneDraft={cloneDraft}
            onCloneDraftChange={setCloneDraft}
            onCloneRepo={cloneRepo}
            reviewPlan={reviewPlan}
            applyHistory={applyHistory}
            cleanupResult={cleanupResult}
            onCleanup={cleanupArtifacts}
          />
        </div>

        <BottomPanel run={selectedRun} status={status} job={activeJob} onControl={controlJob} />
      </section>
    </main>
  );
}

function MissionHome({ state, readyRuns, blockedRuns, nemoState, status, draft, provider, messages, onDraftChange, onSubmit, onProviderChange, onOpenComposer, running }: { state: MissionState; readyRuns: number; blockedRuns: number; nemoState: NemoState | null; status: string; draft: string; provider: string; messages: AgentMessage[]; onDraftChange: (objective: string) => void; onSubmit: () => void; onProviderChange: (provider: string) => void; onOpenComposer: () => void; running: boolean }) {
  const recentRuns = state.runs.slice(0, 4);
  const visibleMessages = messages.slice(-4);
  const atomCount = nemoState?.health.atom_count ?? 0;
  const evidenceCount = nemoState?.health.evidence_count ?? 0;
  const feedbackCount = nemoState?.health.feedback_count ?? 0;
  const contextLabel = nemoState?.context_portfolio?.estimated_tokens ? `${nemoState.context_portfolio.estimated_tokens}t` : "ready";
  return (
    <section className="mission-home">
      <div className="home-main">
        <div className="home-kicker"><Bot size={16} /> NEMO PRIME</div>
        <h2>Hola, Nemo</h2>
        <p>Describe la tarea, deja que los agentes trabajen en sandbox y revisa el diff con memoria operativa al lado.</p>
        <div className="home-prompt">
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter") onSubmit();
            }}
            placeholder="Habla con el agente..."
          />
          <div className="prompt-actions">
            <button onClick={onOpenComposer} title="Configurar handoff"><Plus size={16} /></button>
            <label className={`provider-switch ${provider === "subprocess" ? "real" : "fake"}`} title="Cambiar modo del agente">
              <Bot size={15} />
              <select value={provider} onChange={(event) => onProviderChange(event.target.value)}>
                <option value="fake">Fake planner</option>
                <option value="subprocess">LM Studio real</option>
              </select>
            </label>
            <button title="Memoria NEMO"><Database size={15} /> Memory</button>
            <button className="send-intent" onClick={onSubmit} disabled={running || !draft.trim()} title="Enviar al agente"><ArrowUp size={18} /></button>
          </div>
        </div>
        <div className="home-chat-preview" aria-label="Conversacion con agente">
          {visibleMessages.map((message) => <article className={message.role} key={message.id}>
            <span>{message.role === "assistant" ? "agent" : "you"}</span>
            <p>{message.content}</p>
          </article>)}
        </div>
        <div className="suggestion-row">
          {[
            "Implementar feature desde PRD",
            "Crear tests de regresión",
            "Optimizar flujo de apply/rollback",
          ].map((item) => <button key={item} onClick={() => onDraftChange(item)}>{item}</button>)}
        </div>
      </div>

      <aside className="home-status">
        <div className="status-title">
          <span><Home size={15} /> Sistema</span>
          <strong>{blockedRuns ? `${blockedRuns} bloqueado(s)` : "Todo en orden"}</strong>
        </div>
        <StatusGauge label="Contexto" value={contextLabel} percent={Math.min(100, Math.max(18, atomCount * 2))} tone="blue" />
        <StatusGauge label="Memoria" value={`${atomCount} atoms`} percent={Math.min(100, Math.max(20, atomCount * 3))} tone="gold" />
        <StatusGauge label="Feedback" value={`${feedbackCount} eventos`} percent={Math.min(100, Math.max(18, feedbackCount * 12))} tone="violet" />
        <div className="agent-stack">
          <strong>Agentes activos <span>{readyRuns}</span></strong>
          <AgentPulse name="Planner" detail="Analizando requisitos" tone="green" />
          <AgentPulse name="Coder" detail="Editando sandbox" tone="blue" />
          <AgentPulse name="Reviewer" detail={`${evidenceCount} evidence handle(s)`} tone="violet" />
        </div>
        <div className="recent-card">
          <strong>Actividad reciente</strong>
          {recentRuns.length === 0 ? <span className="empty-inline">Sin runs todavía.</span> : recentRuns.map((run) => (
            <div className="recent-run" key={run.source_json}>
              <span>{run.objective}</span>
              <small>{statusLabel(run.review_status)}</small>
            </div>
          ))}
        </div>
        <div className="working-strip"><span>{status}</span><button title="Notificaciones"><Bell size={14} /></button></div>
      </aside>
    </section>
  );
}

function StatusGauge({ label, value, percent, tone }: { label: string; value: string; percent: number; tone: "blue" | "gold" | "violet" }) {
  return <div className={`status-gauge ${tone}`}><div><span>{label}</span><strong>{value}</strong></div><i><b style={{ width: `${percent}%` }} /></i></div>;
}

function AgentPulse({ name, detail, tone }: { name: string; detail: string; tone: "green" | "blue" | "violet" }) {
  return <div className="agent-pulse"><Circle size={8} className={tone} /><div><span>{name}</span><small>{detail}</small></div></div>;
}

function EditorPane({ run, filePreview, activeFile, decisions, onToggleHunk, onSetFileDecision, onApplySelected }: { run: MissionRun | undefined; filePreview: FilePreview | null; activeFile: string; decisions: Record<string, boolean>; onToggleHunk: (filePath: string, hunkId: string) => void; onSetFileDecision: (preview: FilePreview, accepted: boolean) => void; onApplySelected: (run: MissionRun, preview: FilePreview) => void }) {
  if (!run) {
    return <section className="editor-pane"><EmptyState /></section>;
  }
  const acceptedCount = filePreview?.hunks.filter((hunk) => decisions[hunk.id] ?? true).length ?? 0;
  return (
    <section className="editor-pane">
      <div className="editor-toolbar">
        <div>
          <strong>{activeFile || "No file selected"}</strong>
          <span>{filePreview?.operation ?? "review"} / {acceptedCount} accepted hunk(s)</span>
        </div>
        <span className={`pill ${statusTone(run.review_status)}`}>{statusLabel(run.review_status)}</span>
      </div>

      {filePreview ? <div className="review-surface">
        <div className="review-toolbar">
          <div>
            <strong>{filePreview.file_path}</strong>
            <span>{filePreview.target_path}</span>
          </div>
          <div className="review-toolbar-actions">
            <button onClick={() => onSetFileDecision(filePreview, true)}>Accept file</button>
            <button onClick={() => onSetFileDecision(filePreview, false)}>Reject file</button>
            <button disabled={acceptedCount === 0 || !run.mergeable} onClick={() => onApplySelected(run, filePreview)}><CheckCircle2 size={15} /> Apply selected</button>
          </div>
        </div>
        <RiskChecklist run={run} preview={filePreview} />
        <DiffHunkList preview={filePreview} decisions={decisions} onToggleHunk={onToggleHunk} />
      </div> : <EmptyState />}
    </section>
  );
}

function RiskChecklist({ run, preview }: { run: MissionRun; preview: FilePreview }) {
  const risks = [...run.risk_flags, ...preview.risk_flags, ...preview.file_risk_flags];
  const uniqueRisks = Array.from(new Set(risks));
  const checks = uniqueRisks.length > 0 ? uniqueRisks : ["hashes verified", "sandbox source exists", "review gate mergeable"];
  return (
    <div className={`risk-checklist ${uniqueRisks.length ? "blocked" : "ready"}`}>
      {checks.map((item) => <span key={item}>{uniqueRisks.length ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />} {item}</span>)}
    </div>
  );
}

function DiffHunkList({ preview, decisions, onToggleHunk }: { preview: FilePreview; decisions: Record<string, boolean>; onToggleHunk: (filePath: string, hunkId: string) => void }) {
  if (preview.hunks.length === 0) return <div className="empty">No line changes in this file.</div>;
  return <div className="hunk-list">{preview.hunks.map((hunk) => <DiffHunkView preview={preview} hunk={hunk} accepted={decisions[hunk.id] ?? true} onToggleHunk={onToggleHunk} key={hunk.id} />)}</div>;
}

function DiffHunkView({ preview, hunk, accepted, onToggleHunk }: { preview: FilePreview; hunk: DiffHunk; accepted: boolean; onToggleHunk: (filePath: string, hunkId: string) => void }) {
  return (
    <article className={`diff-hunk ${accepted ? "accepted" : "rejected"}`}>
      <div className="hunk-header">
        <strong>@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@</strong>
        <label>
          <input type="checkbox" checked={accepted} onChange={() => onToggleHunk(preview.file_path, hunk.id)} />
          {accepted ? "Accepted" : "Rejected"}
        </label>
      </div>
      <div className="diff-lines">
        {hunk.rows.map((row, index) => <DiffRowView row={row} key={`${hunk.id}-${index}`} />)}
      </div>
    </article>
  );
}

function DiffRowView({ row }: { row: DiffRow }) {
  return (
    <div className={`diff-line ${row.kind}`}>
      <span>{row.old_line ?? ""}</span>
      <span>{row.new_line ?? ""}</span>
      <code>{row.kind === "insert" ? "+" : row.kind === "delete" ? "-" : " "}{row.content}</code>
    </div>
  );
}

function HandoffComposer({ draft, onChange, onSubmit, onClose, running }: { draft: { objective: string; acceptance: string; validation: string; validationPolicy: string; targetFiles: string; provider: string; timeoutSeconds: string }; onChange: (draft: { objective: string; acceptance: string; validation: string; validationPolicy: string; targetFiles: string; provider: string; timeoutSeconds: string }) => void; onSubmit: () => void; onClose: () => void; running: boolean }) {
  const update = (key: keyof typeof draft, value: string) => onChange({ ...draft, [key]: value });
  return (
    <section className="handoff-composer">
      <div className="composer-header">
        <div>
          <strong>New Full Handoff</strong>
          <span>Runs in an isolated runtime and returns to review.</span>
        </div>
        <button onClick={onClose} disabled={running}>Close</button>
      </div>
      <textarea
        value={draft.objective}
        onChange={(event) => update("objective", event.target.value)}
        placeholder="Describe the PRD or implementation objective..."
      />
      <div className="composer-grid">
        <label>
          Provider
          <select value={draft.provider} onChange={(event) => update("provider", event.target.value)} disabled={running}>
            <option value="fake">Fake smoke runner</option>
            <option value="subprocess">Aider + LM Studio</option>
          </select>
        </label>
        <label>
          Timeout seconds
          <input value={draft.timeoutSeconds} onChange={(event) => update("timeoutSeconds", event.target.value)} disabled={running} />
        </label>
        <label>
          Validation policy
          <select value={draft.validationPolicy} onChange={(event) => update("validationPolicy", event.target.value)} disabled={running}>
            <option value="none">none</option>
            <option value="smoke">smoke</option>
            <option value="targeted">targeted</option>
            <option value="full">full</option>
          </select>
        </label>
        <label>
          Acceptance
          <textarea value={draft.acceptance} onChange={(event) => update("acceptance", event.target.value)} />
        </label>
        <label>
          Validation
          <textarea value={draft.validation} onChange={(event) => update("validation", event.target.value)} />
        </label>
        <label>
          Target files
          <textarea value={draft.targetFiles} onChange={(event) => update("targetFiles", event.target.value)} placeholder="optional, one per line" />
        </label>
      </div>
      <div className="composer-actions">
        <button onClick={onSubmit} disabled={running}><Play size={16} /> {running ? "Starting" : "Start in sandbox"}</button>
      </div>
    </section>
  );
}

type AgentPaneProps = {
  run: MissionRun | undefined;
  state: MissionState;
  readyRuns: number;
  blockedRuns: number;
  applyJson?: string;
  onReview: (run: MissionRun) => void;
  onApply: (run: MissionRun) => void;
  onRollback: (run: MissionRun) => void;
  messages: AgentMessage[];
  draft: string;
  busy: boolean;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onRunAction: (action: AgentAction) => void;
  nemoState: NemoState | null;
  settingsDraft: MissionState["settings"];
  onSettingsChange: (settings: MissionState["settings"]) => void;
  onSaveSettings: () => void;
  repoDraft: string;
  onRepoDraftChange: (value: string) => void;
  onOpenRepo: (repoPath?: string) => void;
  cloneDraft: { url: string; destination: string };
  onCloneDraftChange: (draft: { url: string; destination: string }) => void;
  onCloneRepo: () => void;
  reviewPlan: ReviewPlan | null;
  applyHistory: ApplyHistoryItem[];
  cleanupResult: CleanupResult | null;
  onCleanup: (dryRun: boolean) => void;
};

function AgentPane({ run, state, readyRuns, blockedRuns, applyJson, onReview, onApply, onRollback, messages, draft, busy, onDraftChange, onSend, onRunAction, nemoState, settingsDraft, onSettingsChange, onSaveSettings, repoDraft, onRepoDraftChange, onOpenRepo, cloneDraft, onCloneDraftChange, onCloneRepo, reviewPlan, applyHistory, cleanupResult, onCleanup }: AgentPaneProps) {
  return (
    <aside className="agent-pane">
      <div className="panel-title"><Bot size={16} /> Agent Control</div>
      <section className="agent-chat">
        <div className="chat-thread">
          {messages.map((message) => <AgentChatMessage message={message} onRunAction={onRunAction} key={message.id} />)}
        </div>
        <div className="chat-composer">
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) onSend();
            }}
            placeholder="Ask the agent to continue, revise, inspect, or apply..."
          />
          <button onClick={onSend} disabled={busy || !draft.trim()} title="Send agent prompt"><Send size={15} /></button>
        </div>
      </section>
      <div className="metric-grid">
        <Metric icon={<TerminalSquare size={16} />} label="Runs" value={state.runs.length} />
        <Metric icon={<ShieldCheck size={16} />} label="Ready" value={readyRuns} />
        <Metric icon={<AlertTriangle size={16} />} label="Blocked" value={blockedRuns} />
        <Metric icon={<Database size={16} />} label="NEMO" value={state.settings.nemo_required ? "on" : "off"} />
      </div>

      {run ? <>
        <div className="agent-card">
          <span>Selected run</span>
          <strong>{run.task_id} / {run.run_id}</strong>
          <p>{run.source_json}</p>
        </div>
        <OperationalStatePanel run={run} />
        {run.risk_flags.length > 0 && <div className="risk-box">{run.risk_flags.map((risk) => <span key={risk}>{risk}</span>)}</div>}
        <div className="review-actions">
          <button onClick={() => onReview(run)}><GitPullRequest size={16} /> Build plan</button>
          <button disabled={!run.mergeable} onClick={() => onApply(run)}><CheckCircle2 size={16} /> Apply</button>
          <button onClick={() => onRollback(run)}><RotateCcw size={16} /> Rollback</button>
        </div>
        {applyJson && <p className="muted">Last apply JSON: {applyJson}</p>}
      </> : <EmptyState />}

      <NemoMemoryPanel nemoState={nemoState} />

      <ReviewPlanPanel plan={reviewPlan} />
      <ApplyHistoryPanel applies={applyHistory} />
      <RepoSettingsPanel
        state={state}
        settings={settingsDraft}
        onSettingsChange={onSettingsChange}
        onSaveSettings={onSaveSettings}
        repoDraft={repoDraft}
        onRepoDraftChange={onRepoDraftChange}
        onOpenRepo={onOpenRepo}
        cloneDraft={cloneDraft}
        onCloneDraftChange={onCloneDraftChange}
        onCloneRepo={onCloneRepo}
        cleanupResult={cleanupResult}
        onCleanup={onCleanup}
      />
    </aside>
  );
}

function ReviewPlanPanel({ plan }: { plan: ReviewPlan | null }) {
  return (
    <section className="ops-panel">
      <div className="panel-title"><GitPullRequest size={16} /> Apply Plan</div>
      {!plan ? <span className="empty-inline">Build a plan before applying.</span> : <>
        <div className={`plan-status ${plan.mergeable ? "ready" : "blocked"}`}>{plan.mergeable ? "mergeable" : "blocked"}</div>
        {plan.risk_flags.length > 0 && <div className="risk-box">{plan.risk_flags.map((risk) => <span key={risk}>{risk}</span>)}</div>}
        <div className="mini-list">{plan.files.map((file) => <span key={file.path}>{file.operation}: {file.path}</span>)}</div>
      </>}
    </section>
  );
}

function OperationalStatePanel({ run }: { run: MissionRun }) {
  const state = run.continuation_state;
  const checkpoints = state?.checkpoint_refs ?? [];
  const heartbeats = state?.heartbeat_refs ?? [];
  const handles = state?.memory_writeback_handles ?? [];
  const escalations = state?.escalation_flags ?? [];
  return (
    <section className="ops-panel">
      <div className="panel-title"><TerminalSquare size={16} /> Runtime State</div>
      <div className="mini-list">
        <span>runtime: {run.runtime_id || "unknown"}</span>
        <span>state: {run.runtime_state || "unknown"} / phase: {run.execution_phase || "unknown"}</span>
        <span>validation: {run.validation_profile || state?.validation_policy || "smoke"}</span>
        <span>provider: {state?.provider_mode || run.permission_profile || "unknown"}</span>
        {state?.resume_token && <span>resume: {state.resume_token}</span>}
        {state?.resume_minute !== undefined && state?.resume_minute !== null && <span>resume minute: {state.resume_minute}</span>}
      </div>
      {state?.error && <div className="risk-box"><span>{state.error}</span></div>}
      <div className="mini-list">
        <span>checkpoints: {checkpoints.length ? checkpoints.join(", ") : "none"}</span>
        <span>heartbeats: {heartbeats.length}</span>
        <span>memory handles: {handles.length}</span>
        <span>escalations: {escalations.length ? escalations.join(", ") : "none"}</span>
      </div>
    </section>
  );
}

function ApplyHistoryPanel({ applies }: { applies: ApplyHistoryItem[] }) {
  return (
    <section className="ops-panel">
      <div className="panel-title"><RotateCcw size={16} /> Apply History</div>
      {applies.length === 0 ? <span className="empty-inline">No apply artifacts yet.</span> : <div className="mini-list">
        {applies.slice(0, 6).map((item) => <span key={item.path}>{item.run_id}: {item.applied_files.join(", ") || "none"}{item.backup_root ? ` / backup ${item.backup_files.length}` : ""}</span>)}
      </div>}
    </section>
  );
}

function RepoSettingsPanel({ state, settings, onSettingsChange, onSaveSettings, repoDraft, onRepoDraftChange, onOpenRepo, cloneDraft, onCloneDraftChange, onCloneRepo, cleanupResult, onCleanup }: { state: MissionState; settings: MissionState["settings"]; onSettingsChange: (settings: MissionState["settings"]) => void; onSaveSettings: () => void; repoDraft: string; onRepoDraftChange: (value: string) => void; onOpenRepo: (repoPath?: string) => void; cloneDraft: { url: string; destination: string }; onCloneDraftChange: (draft: { url: string; destination: string }) => void; onCloneRepo: () => void; cleanupResult: CleanupResult | null; onCleanup: (dryRun: boolean) => void }) {
  const update = (key: keyof MissionState["settings"], value: string | number | boolean | string[]) => onSettingsChange({ ...settings, [key]: value });
  return (
    <section className="ops-panel settings-editor">
      <div className="panel-title"><Settings size={16} /> Settings</div>
      <label>LM Studio URL<input value={settings.model_base_url} onChange={(event) => update("model_base_url", event.target.value)} /></label>
      <label>Model<input value={settings.default_model} onChange={(event) => update("default_model", event.target.value)} /></label>
      <label>Provider<select value={settings.provider} onChange={(event) => update("provider", event.target.value)}><option value="fake">fake</option><option value="subprocess">Aider + LM Studio</option></select></label>
      <label>NEMO DB<input value={settings.memory_db} onChange={(event) => update("memory_db", event.target.value)} /></label>
      <label>Runtime path<input value={settings.runtime_path} onChange={(event) => update("runtime_path", event.target.value)} /></label>
      <label>Validation policy<select value={settings.validation_policy} onChange={(event) => update("validation_policy", event.target.value)}><option value="none">none</option><option value="smoke">smoke</option><option value="targeted">targeted</option><option value="full">full</option></select></label>
      <div className="settings-grid">
        <label>Timeout<input type="number" value={settings.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></label>
        <label>Max minutes<input type="number" value={settings.max_runtime_minutes} onChange={(event) => update("max_runtime_minutes", Number(event.target.value))} /></label>
        <label>Heartbeat<input type="number" value={settings.heartbeat_minutes} onChange={(event) => update("heartbeat_minutes", Number(event.target.value))} /></label>
        <label>Token budget<input type="number" value={settings.token_budget} onChange={(event) => update("token_budget", Number(event.target.value))} /></label>
      </div>
      <div className="review-actions"><button onClick={onSaveSettings}><CheckCircle2 size={16} /> Save settings</button></div>
      <div className="repo-picker">
        <strong>Repository</strong>
        <input value={repoDraft} onChange={(event) => onRepoDraftChange(event.target.value)} />
        <button onClick={() => onOpenRepo()}><HardDrive size={15} /> Open folder</button>
        <div className="mini-list">{state.settings.recent_repos?.map((repo) => <button key={repo} onClick={() => onOpenRepo(repo)}>{repo}</button>)}</div>
      </div>
      <div className="repo-picker">
        <strong>Clone from git</strong>
        <input value={cloneDraft.url} onChange={(event) => onCloneDraftChange({ ...cloneDraft, url: event.target.value })} placeholder="https://github.com/org/repo.git" />
        <input value={cloneDraft.destination} onChange={(event) => onCloneDraftChange({ ...cloneDraft, destination: event.target.value })} placeholder="c:/dev/repo" />
        <button onClick={onCloneRepo}><GitBranch size={15} /> Clone and open</button>
      </div>
      <div className="repo-picker">
        <strong>Artifact cleanup</strong>
        <div className="review-actions"><button onClick={() => onCleanup(true)}>Scan</button><button onClick={() => onCleanup(false)}>Delete scan matches</button></div>
        {cleanupResult && <span className="muted">{cleanupResult.dry_run ? cleanupResult.candidates.length : cleanupResult.deleted.length} file(s) / {cleanupResult.max_age_days} days</span>}
      </div>
    </section>
  );
}

function NemoMemoryPanel({ nemoState }: { nemoState: NemoState | null }) {
  const portfolioLines = (nemoState?.context_portfolio?.context ?? "").split("\n").filter(Boolean).slice(0, 8);
  const usedMemories = nemoState?.used_memories.length ? nemoState.used_memories : [];
  const corrections = nemoState?.corrections ?? [];
  const evidence = nemoState?.evidence ?? [];
  const feedback = nemoState?.feedback ?? [];
  return (
    <section className="nemo-panel">
      <div className="panel-title"><Database size={16} /> NEMO Memory</div>
      <div className="nemo-health">
        <span className={nemoState?.health.enabled ? "ready" : "blocked"}>{nemoState?.health.status ?? "loading"}</span>
        <strong>{nemoState?.health.atom_count ?? 0}</strong><small>atoms</small>
        <strong>{nemoState?.health.evidence_count ?? 0}</strong><small>evidence</small>
        <strong>{nemoState?.health.feedback_count ?? 0}</strong><small>feedback</small>
      </div>
      <NemoSection title="Context portfolio" empty="No portfolio compiled yet.">
        {portfolioLines.map((line, index) => <NemoLine key={`${line}-${index}`} tone="portfolio" value={line} />)}
        {nemoState?.context_portfolio?.estimated_tokens !== undefined && <NemoLine tone="meta" value={`${nemoState.context_portfolio.estimated_tokens} estimated tokens`} />}
      </NemoSection>
      <NemoSection title="Memories used" empty="No linked memory atom ids on this run.">
        {usedMemories.map((memory) => <NemoLine key={memory.id} tone={memory.type} value={memory.content} meta={`${memory.type} / importance ${memory.importance}`} />)}
        {nemoState?.memory_traces.map((trace, index) => <NemoLine key={`${trace.nemo_tool}-${index}`} tone="trace" value={trace.summary ?? "trace"} meta={trace.nemo_tool} />)}
      </NemoSection>
      <NemoSection title="Corrections" empty="No corrections stored.">
        {corrections.map((memory) => <NemoLine key={memory.id} tone="correction" value={memory.content} meta={memory.topic} />)}
      </NemoSection>
      <NemoSection title="Evidence" empty="No evidence handles stored.">
        {evidence.map((item) => <NemoLine key={item.handle} tone="evidence" value={item.compact_claim} meta={`${item.handle} / ${item.retrieval_count} reads`} />)}
      </NemoSection>
      <NemoSection title="Feedback" empty="No feedback events yet.">
        {feedback.map((item) => <NemoLine key={item.id} tone="feedback" value={item.event_type} meta={item.evidence_handle ?? item.atom_id ?? "portfolio"} />)}
      </NemoSection>
    </section>
  );
}

function NemoSection({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const items = React.Children.toArray(children).filter(Boolean);
  return (
    <div className="nemo-section">
      <strong>{title}</strong>
      {items.length > 0 ? items : <span className="empty-inline">{empty}</span>}
    </div>
  );
}

function NemoLine({ tone, value, meta }: { tone: string; value: string; meta?: string }) {
  return <div className={`nemo-line ${tone}`}><span>{meta ?? tone}</span><p>{value}</p></div>;
}

function AgentChatMessage({ message, onRunAction }: { message: AgentMessage; onRunAction: (action: AgentAction) => void }) {
  return (
    <article className={`chat-message ${message.role}`}>
      <div className="message-role">{message.role}</div>
      <p>{message.content}</p>
      {message.tool_calls && message.tool_calls.length > 0 && <div className="tool-call-list">
        {message.tool_calls.map((tool) => (
          <div className="tool-call" key={tool.id}>
            <Wrench size={13} />
            <div>
              <strong>{tool.name}</strong>
              <span>{tool.status} / {tool.summary}</span>
            </div>
          </div>
        ))}
      </div>}
      {message.actions && message.actions.length > 0 && <div className="agent-actions">
        {message.actions.map((action) => (
          <button className={action.kind} onClick={() => onRunAction(action)} title={action.summary} key={action.id}>
            {action.kind === "apply" ? <CheckCircle2 size={14} /> : <Play size={14} />}
            {action.label}
          </button>
        ))}
      </div>}
    </article>
  );
}

function BottomPanel({ run, status, job, onControl }: { run: MissionRun | undefined; status: string; job: HandoffJob | null; onControl: (action: "cancel" | "pause" | "resume") => void }) {
  const running = job ? ["starting", "running"].includes(job.status) : false;
  const paused = job?.status === "paused";
  return (
    <section className="bottom-panel">
      <div className="bottom-tabs">
        <button className="active"><PanelBottom size={14} /> Timeline</button>
        <button><TerminalSquare size={14} /> Terminal</button>
        <button><Clock3 size={14} /> Output</button>
      </div>
      <div className="bottom-content">
        <div className="terminal-line"><span>nemo</span> {status}</div>
        {job && <div className="job-console">
          <div className="job-header">
            <strong>{job.job_id}</strong>
            <span>{job.status}{job.returncode !== null ? ` returncode=${job.returncode}` : ""}</span>
            <div>
              <button disabled={!running} onClick={() => onControl("pause")}>Pause</button>
              <button disabled={!paused} onClick={() => onControl("resume")}>Resume</button>
              <button disabled={!running} onClick={() => onControl("cancel")}>Cancel</button>
            </div>
          </div>
          <pre>{job.logs.slice(-80).join("\n") || "No logs yet."}</pre>
        </div>}
        {run?.timeline.map((event, index) => (
          <div className="timeline-row" key={`${event.sequence ?? index}-${event.kind}`}>
            <span>{event.sequence ?? index + 1}</span>
            <strong>{event.kind ?? "event"}</strong>
            <p>{event.summary ?? event.phase ?? "No summary"}{event.payload_ref ? ` / ${event.payload_ref}` : ""}</p>
          </div>
        )) ?? <EmptyState />}
      </div>
    </section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return <div className="metric">{icon}<span>{label}</span><strong>{value}</strong></div>;
}

function Setting({ label, value }: { label: string; value: string }) {
  return <div className="setting"><span>{label}</span><strong>{value}</strong></div>;
}

function EmptyState() {
  return <div className="empty">No live workspace data available.</div>;
}

createRoot(document.getElementById("root")!).render(<App />);