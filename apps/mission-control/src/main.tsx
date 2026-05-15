import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AlertTriangle, Archive, ArrowUp, Bell, Bot, CheckCircle2, ChevronDown, Circle, Clock3, Code2, Database, FileCode2, Files, GitBranch, GitCompare, GitPullRequest, Globe, HardDrive, Home, MessageSquarePlus, MessageSquareText, PanelBottom, Play, Puzzle, RefreshCw, RotateCcw, Search, Send, Settings, ShieldCheck, Square, TerminalSquare, Trash2, Wrench, CheckSquare } from "lucide-react";
import "./styles.css";
import { ArtifactWorkbench } from "./components/ArtifactWorkbench";
import { CommandDock } from "./components/CommandDock";
import { MissionTimeline } from "./components/MissionTimeline";
import { NemoMemoryOrbitCopy, type NemoMemoryOrbitEdge, type NemoMemoryOrbitNode } from "./components/NemoMemoryOrbitCopy";
import { ObjectiveDefinition } from "./components/ObjectiveDefinition";
import { PlanProgress } from "./components/PlanProgress";
import { TelemetryColumn } from "./components/TelemetryColumn";
import { WorktreeDiffPanel } from "./components/WorktreeDiffPanel";
import { PermissionRequestPanel } from "./components/PermissionRequestPanel";
import { TimelinePanel } from "./components/TimelinePanel";
import { useGeneratedArtifacts } from "./hooks/useGeneratedArtifacts";
import { usePlanState } from "./hooks/usePlanState";
import { usePlanNemoSync } from "./hooks/usePlanNemoSync";
import { ExecutionPlan, ObjectiveState, PlanStep } from "./services/planNemoClient";
import { archiveChatSession } from "./services/persistenceStore";

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
  statusLoaded: boolean;
  schema_version: number;
  product: string;
  repo_path: string;
  runtimes_path: string;
  repos: string[];
  jobs: HandoffJob[];
  runs: MissionRun[];
  approval_queue: MissionRun[];
  settings: {
    model_base_url: string;
    default_model: string;
    provider: string;
    memory_db: string;
    nemo_mcp_url?: string;
    runtime_path: string;
    timeout_seconds: number;
    max_runtime_minutes: number;
    heartbeat_minutes: number;
    max_heartbeats: number;
    token_budget: number;
    context_window_tokens: number;
    chat_max_tokens: number;
    image_gen_backend: string;
    image_gen_url: string;
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
type RunsCleanupResult = {
  ok: boolean;
  mode: "old" | "all";
  removed_count: number;
  removed_source_json: string[];
  state: MissionState;
};
type OrphanCleanupResult = {
  dry_run: boolean;
  max_age_minutes: number;
  orphans: Array<{ kind: string; job_id?: string; path?: string; reason: string; age_seconds?: number }>;
  summary: {
    in_memory: number;
    snapshots: number;
    total: number;
    marked_jobs: string[];
    deleted_snapshots: string[];
  };
};
type HandoffJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  run_json: string;
  status: string;
  returncode: number | null;
  error: string | null;
  logs: string[];
  objective?: string | null;
  permission_request?: {
    job_id: string;
    categories: string[];
    rationale: string;
    auto_approved: string[];
    requires_user_approval: string[];
    decision?: {
      decided_at: string;
      decided_by: string;
      approved: boolean;
      categories: string[];
      note: string;
    };
  } | null;
  timeline?: Array<{
    kind: string;
    summary: string;
    phase: string;
    sequence: number;
    ts: string;
    payload?: Record<string, unknown>;
  }>;
};
type HandoffJobResult = { job: HandoffJob; state?: MissionState };
type AgentToolCall = {
  id: string;
  name: string;
  tool_name?: string;
  alias_name?: string;
  status: string;
  summary: string;
};
type AgentTraceEvent = {
  id: string;
  step: number;
  kind: string;
  label: string;
  status: string;
  detail?: string;
  tool_name?: string;
  source?: string;
  ts?: string;
};
type AgentSourceRef = {
  title: string;
  url: string;
  snippet?: string;
  cached?: boolean;
};
type RenderedToolCall = AgentToolCall & {
  source: "payload" | "inline";
};
type AgentAction = {
  id: string;
  kind: "continue" | "revise" | "apply" | "self_modify" | "review" | "evaluate" | "run" | "handoff" | "pc_control" | "layout" | "plan_generate" | "plan_cancel" | "plan_steer";
  label: string;
  summary: string;
  payload: Record<string, unknown>;
};
type AgentMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: AgentToolCall[];
  sources?: AgentSourceRef[];
  agent_trace?: AgentTraceEvent[];
  actions?: AgentAction[];
};
type HomePanelKey = "timeline" | "artifact" | "telemetry";
type HomeLayoutMode = "full-cockpit" | "focus-artifact" | "focus-chat" | "focus-telemetry";
type HomeLayoutCommand = {
  mode?: HomeLayoutMode;
  collapse?: HomePanelKey[];
  expand?: HomePanelKey[];
};
type HomePanelState = Record<HomePanelKey, boolean>;
type AutonomyMode = "manual" | "trusted" | "aggressive";
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

const NEMO_ORBIT_TYPE_COLORS: Record<string, string> = {
  correction: "#ff4757",
  preference: "#a855f7",
  insight: "#ff9f1c",
  episodic: "#48cae4",
  procedure: "#06d6a0",
  fact: "#00b4d8",
  evidence: "#67E8F9",
  feedback: "#C084FC",
  portfolio: "#00d6e8",
};

function colorForNemoMemory(type: string): string {
  return NEMO_ORBIT_TYPE_COLORS[type] || "#607d8b";
}

function buildNemoOrbitNodes(nemoState: NemoState | null, atomCount: number, evidenceCount: number, feedbackCount: number, contextLabel: string): NemoMemoryOrbitNode[] {
  const memories = [...(nemoState?.corrections ?? []), ...(nemoState?.used_memories ?? [])];
  const mapped = memories.slice(0, 14).map((memory): NemoMemoryOrbitNode => ({
    id: memory.id,
    mtype: memory.type || "fact",
    importance: Math.max(1, Math.min(10, memory.importance || 5)),
    color: colorForNemoMemory(memory.type),
    title: memory.topic || memory.content.slice(0, 72) || "NEMO memory",
    summary: memory.content,
    excerpt: memory.content.slice(0, 110),
    tags: memory.tags,
    content: memory.content,
  }));

  if (mapped.length > 0) return mapped;

  return [
    { id: "nemo-health-atoms", mtype: "fact", importance: Math.max(3, Math.min(10, Math.ceil(atomCount / 8))), color: colorForNemoMemory("fact"), title: `${atomCount} memory atoms`, summary: "NEMO memory atom count" },
    { id: "nemo-health-evidence", mtype: "evidence", importance: Math.max(3, Math.min(10, Math.ceil(evidenceCount / 4))), color: colorForNemoMemory("evidence"), title: `${evidenceCount} evidence handles`, summary: "NEMO evidence handle count" },
    { id: "nemo-health-feedback", mtype: "feedback", importance: Math.max(3, Math.min(10, Math.ceil(feedbackCount / 3))), color: colorForNemoMemory("feedback"), title: `${feedbackCount} feedback events`, summary: "NEMO feedback event count" },
    { id: "nemo-health-context", mtype: "portfolio", importance: contextLabel === "ready" ? 5 : 8, color: colorForNemoMemory("portfolio"), title: `Context ${contextLabel}`, summary: "Current NEMO context portfolio state" },
  ];
}

function buildNemoOrbitEdges(nodes: NemoMemoryOrbitNode[]): NemoMemoryOrbitEdge[] {
  const edges: NemoMemoryOrbitEdge[] = [];
  for (let index = 0; index < nodes.length - 1; index += 1) {
    const current = nodes[index];
    const next = nodes[index + 1];
    const sharedTags = (current.tags ?? []).filter((tag) => (next.tags ?? []).includes(tag)).length;
    const sameType = current.mtype === next.mtype;
    edges.push({
      source: current.id,
      target: next.id,
      similarity: Math.min(0.98, 0.72 + sharedTags * 0.06 + (sameType ? 0.08 : 0)),
      color: current.color,
      width: 0.08,
      particles: sameType || sharedTags > 0 ? 1 : 0,
    });
  }
  if (nodes.length > 3) {
    edges.push({ source: nodes[0].id, target: nodes[nodes.length - 1].id, similarity: 0.76, color: nodes[0].color, width: 0.06, particles: 0 });
  }
  return edges;
}
type NemoMcpWatcherState = {
  ok: boolean;
  configured: boolean;
  active: boolean;
  status: string;
  url: string;
  capabilities?: {
    enabled?: boolean;
    supports_context_bootstrap?: boolean;
    supports_prime_context?: boolean;
    supports_search_memories?: boolean;
    supports_core_context_reads?: boolean;
    supports_write_read_roundtrip?: boolean;
    roundtrip_probe_executed?: boolean;
    errors?: string[];
  };
  available_tools?: string[];
  selected_tools?: string[];
  latency_ms?: number;
  http_status?: number;
  error?: string;
  checked_at?: string;
};
type SelfModInsights = {
  ok: boolean;
  trajectory: {
    task_id: string;
    run_id: string;
    objective: string;
    grade: string;
    score: number;
    validation_passed: boolean;
    mergeable: boolean;
    risk_flags: string[];
    changed_files: string[];
    timeline: TimelineEvent[];
    artifacts: Array<{ type?: string; path?: string; summary?: string }>;
  };
  impact: {
    changed_files: string[];
    impacted_modules: string[];
    suggested_tests: string[];
    validation_commands: string[];
    validation_statuses: string[];
    risk_flags: string[];
    touches_source: boolean;
    touches_tests: boolean;
    touches_ui: boolean;
    touches_mcp: boolean;
    touches_cli: boolean;
  };
  similar_runs: { query: string; count: number; runs: Array<{ id: string; content: string; tags: string[]; importance: number; evidence_handle: string | null; score: number }> };
};

type RiskPattern = { id: string; content: string; importance: number; topic: string; tags: string[]; score: number };
type RiskMapState = { ok: boolean; enabled: boolean; patterns: RiskPattern[]; count: number };

type CognitiveStatsState = {
  ok: boolean;
  enabled: boolean;
  error?: string;
  selected_run?: { source_json: string; task_id: string | null; run_id: string | null; objective: string | null } | null;
  context_portfolio?: { estimated_tokens?: number; token_budget?: number; [key: string]: unknown } | null;
  health: { enabled: boolean; status: string; db_path: string | null; atom_count?: number; evidence_count?: number; feedback_count?: number };
  run_kpis: {
    total_runs: number;
    ready_runs: number;
    blocked_runs: number;
    apply_count: number;
    apply_success_rate: number;
    blocked_rate: number;
    auto_apply_rate: number;
    avg_run_to_apply_minutes: number | null;
  };
  memory_kpis: {
    atom_count: number;
    correction_count: number;
    evidence_count: number;
    feedback_count: number;
    useful_feedback_count: number;
    not_useful_feedback_count: number;
    useful_feedback_rate: number | null;
    portfolio_tokens: number | null;
    portfolio_budget: number | null;
    portfolio_utilization: number | null;
  };
};

type SourceStatItem = {
  url: string;
  title: string;
  reads: number;
  cache_hits: number;
  cache_hit_rate: number;
  snippet_chars_avg: number;
  rank_score: number;
};

type MissionStatsState = {
  timestamp: string;
  jobs: { total: number; active: number; completed: number };
  repair: { total_attempts: number; avg_attempts_per_repair: number; repair_success_rate: number };
  performance: { avg_job_duration_seconds: number; avg_mutation_duration_ms: number; avg_validation_duration_ms: number };
  sources?: {
    total_reads: number;
    unique_urls: number;
    cache_hits: number;
    cache_hit_rate: number;
    by_mode: Record<string, number>;
    top_sources: SourceStatItem[];
  };
};

type TerminalRunResult = {
  ok: boolean;
  command: string;
  cwd: string;
  exit_code: number | null;
  duration_ms: number;
  stdout: string;
  stderr: string;
  error?: string;
};

type BrowserState = {
  homepage: string;
  last_url: string;
  history: string[];
  search_query: string;
  search_history: string[];
};

type BrowserSearchItem = {
  title: string;
  url: string;
  snippet: string;
};

type BrowserSearchState = {
  query: string;
  engine: string;
  results: BrowserSearchItem[];
};

type ExtensionItem = {
  name: string;
  enabled: boolean;
  version: string;
};

type GitStatusEntry = {
  xy: string;
  path: string;
  original_path: string | null;
  staged: boolean;
  unstaged: boolean;
};

type GitStatusState = {
  repo_path: string;
  branch: string;
  ahead: number;
  behind: number;
  entries: GitStatusEntry[];
};

type GitBranchItem = {
  name: string;
  current: boolean;
};

type GitRemote = {
  name: string;
  fetch: string;
  push: string;
};

type GitDiffHunk = {
  id: string;
  header: string;
  preview: string;
};

type AppSection = "home" | "runs" | "versioning" | "terminal" | "browser" | "extensions" | "memory" | "settings";
type RunsWorkbenchTab = "file" | "review" | "agent";

function normalizeFilePreview(payload: FilePreview): FilePreview {
  return {
    ...payload,
    risk_flags: payload.risk_flags ?? [],
    file_risk_flags: payload.file_risk_flags ?? [],
    hunks: payload.hunks ?? [],
    mergeable: payload.mergeable ?? false,
  };
}
const LEGACY_NEMO_SSE_URL = "http://127.0.0.1:8765/mcp/sse";
const DEFAULT_NEMO_MCP_URL = "stdio://vscode/nemo";

function normalizeNemoMcpUrl(value: string | undefined): string {
  const trimmed = (value ?? "").trim();
  return !trimmed || trimmed === LEGACY_NEMO_SSE_URL ? DEFAULT_NEMO_MCP_URL : trimmed;
}

function normalizeSettings(settings: Partial<MissionState["settings"]> | undefined): MissionState["settings"] {
  const merged = { ...initialState.settings, ...(settings ?? {}) };
  return { ...merged, nemo_mcp_url: normalizeNemoMcpUrl(merged.nemo_mcp_url) };
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
  return { ...payload, jobs: payload.jobs ?? [], runs: (payload.runs ?? []).map(normalizeRun), approval_queue: (payload.approval_queue ?? []).map(normalizeRun), settings: normalizeSettings(payload.settings) };
}

function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

function renderInlineRichText(value: string): React.ReactNode[] {
  const parts = value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={`b-${index}`}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={`c-${index}`}>{part.slice(1, -1)}</code>;
    return <React.Fragment key={`t-${index}`}>{part}</React.Fragment>;
  });
}

function extractIterationLines(logs: string[] = []): string[] {
  const iterationPattern = /(iteration|iteracion|iter\s*#|iter\s*\d+|step\s*\d+|paso\s*\d+)/i;
  return Array.isArray(logs) ? logs.filter((line) => iterationPattern.test(line)) : [];
}

const initialState: MissionState = {
  statusLoaded: false,
  schema_version: 1,
  product: "Space Code Mission Control",
  repo_path: "c:/dev/dev4",
  runtimes_path: "c:/dev/dev4/.spacecode-runtimes",
  repos: ["c:/dev/dev4"],
  jobs: [],
  runs: [],
  approval_queue: [],
  settings: {
    model_base_url: "http://127.0.0.1:1234/v1",
    default_model: "",
    provider: "subprocess",
    memory_db: ".nemo-runtimes/nemo-memory.sqlite",
    nemo_mcp_url: DEFAULT_NEMO_MCP_URL,
    runtime_path: "c:/dev/dev4/.spacecode-runtimes",
    timeout_seconds: 120,
    max_runtime_minutes: 120,
    heartbeat_minutes: 15,
    max_heartbeats: 4,
    token_budget: 128000,
    context_window_tokens: 131072,
    chat_max_tokens: 16384,
    image_gen_backend: "auto",
    image_gen_url: "",
    validation_policy: "smoke",
    nemo_required: true,
    quality_core: "product/nemo_code_runtime",
    recent_repos: ["c:/dev/dev4"],
  },
};

const CHAT_SESSION_STORAGE_KEY = "mission-control-chat-session-v1";
const CHAT_ARCHIVE_STORAGE_KEY = "mission-control-chat-archives-v1";
const DEFAULT_SESSION_NEMO_TOOLS = [
  "context_bootstrap",
  "prime_context",
  "build_context_portfolio",
  "get_context_portfolio_stats",
  "search_memories",
  "anticipate",
  "store_conversation",
  "cognitive_ingest",
];
const DEFAULT_HANDOFF_VALIDATION_COMMAND = "npm --prefix apps/mission-control run build";

type ChatSessionSnapshot = {
  version: 1;
  agentMessages: AgentMessage[];
  queuedAgentPrompts: string[];
  missionStats: MissionStatsState | null;
  selectedNemoTools: string[];
};
type ChatArchiveEntry = {
  id: string;
  created_at: string;
  title: string;
  messages: AgentMessage[];
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

function describeRun(run: MissionRun): string {
  const fileCount = run.changed_files.length;
  const riskCount = run.risk_flags.length;
  if (run.review_status === "running") return `Trabajando ahora en ${fileCount} archivo(s)`;
  if (run.review_status === "blocked") return `${riskCount} riesgo(s) bloquean la decision`;
  if (run.review_status === "awaiting_review") return `Listo para revisar cambios en ${fileCount} archivo(s)`;
  return `${fileCount} archivo(s) cambiados y ${riskCount} riesgo(s) detectados`;
}

function summarizePhase(run: MissionRun): string {
  if (run.execution_phase) return run.execution_phase.replaceAll("_", " ");
  if (run.runtime_state) return run.runtime_state.replaceAll("_", " ");
  return "fase sin reportar";
}

function reviewDecisionLabel(run: MissionRun): string {
  if (!run.mergeable) return "requiere cambios";
  if (run.review_status === "awaiting_review") return "listo para decidir";
  if (run.review_status === "running") return "en ejecucion";
  return "seguimiento";
}

function handoffValidationCommand(value: unknown): string {
  const command = String(value || "").trim();
  if (!command || command === "python -m unittest") return DEFAULT_HANDOFF_VALIDATION_COMMAND;
  return command;
}

type QueueRiskNarrative = {
  title: string;
  reason: string;
  impact: string;
  nextStep: string;
};

function humanizeRiskFlag(flag: string): string {
  const normalized = flag.toLowerCase();
  if (normalized.includes("permission") || normalized.includes("approval")) return "Falta validacion humana para continuar";
  if (normalized.includes("validation") || normalized.includes("test")) return "La validacion fallo y necesita correccion";
  if (normalized.includes("merge") || normalized.includes("conflict") || normalized.includes("drift")) return "Hay conflicto de cambios y requiere revision";
  if (normalized.includes("missing") || normalized.includes("path") || normalized.includes("not_found")) return "Falta contexto de archivos o referencias";
  if (normalized.includes("policy") || normalized.includes("guard") || normalized.includes("safety")) return "El gate de seguridad exige decision humana";
  return flag.replaceAll("_", " ");
}

function queueRiskNarrative(flag: string): QueueRiskNarrative {
  const normalized = flag.toLowerCase();
  if (normalized.includes("permission") || normalized.includes("approval")) {
    return {
      title: "Validacion humana",
      reason: "El sistema detecto una accion sensible y espera tu decision.",
      impact: "El run no avanza hasta que revises el cambio.",
      nextStep: "Abre el diff y confirma si se puede continuar.",
    };
  }
  if (normalized.includes("validation") || normalized.includes("test")) {
    return {
      title: "Fallo de validacion",
      reason: "La comprobacion automatica no paso correctamente.",
      impact: "Aplicar ahora podria introducir regresiones.",
      nextStep: "Revisa errores y solicita una nueva iteracion.",
    };
  }
  if (normalized.includes("merge") || normalized.includes("conflict") || normalized.includes("drift")) {
    return {
      title: "Conflicto de cambios",
      reason: "Hay diferencias entre ramas o archivos base.",
      impact: "No es seguro aplicar sin revisar contexto.",
      nextStep: "Abre el diff completo y decide continuar o descartar.",
    };
  }
  if (normalized.includes("missing") || normalized.includes("path") || normalized.includes("not_found")) {
    return {
      title: "Contexto incompleto",
      reason: "Faltan rutas, archivos o referencias necesarias.",
      impact: "La propuesta puede quedar incompleta o romper flujo.",
      nextStep: "Completa el contexto y vuelve a ejecutar.",
    };
  }
  if (normalized.includes("policy") || normalized.includes("guard") || normalized.includes("safety")) {
    return {
      title: "Gate de seguridad",
      reason: "Una politica de seguridad marco este cambio.",
      impact: "El sistema protege la rama hasta tu aprobacion.",
      nextStep: "Evalua riesgo y decide revisar o descartar.",
    };
  }
  return {
    title: "Riesgo detectado",
    reason: humanizeRiskFlag(flag),
    impact: "Puede afectar estabilidad si se aplica sin revision.",
    nextStep: "Revisa el diff antes de continuar.",
  };
}

function queueRunNarratives(run: MissionRun): QueueRiskNarrative[] {
  if (run.risk_flags.length > 0) return run.risk_flags.map(queueRiskNarrative);
  if (run.review_status === "awaiting_review") {
    return [{
      title: "Listo para decision",
      reason: "No se detectaron bloqueos criticos en este punto.",
      impact: "Puedes cerrar este item rapido si el diff esta bien.",
      nextStep: "Haz una revision corta y continua.",
    }];
  }
  if (run.review_status === "running") {
    return [{
      title: "Ejecucion en progreso",
      reason: "El agente sigue procesando cambios en sandbox.",
      impact: "Aun no hay salida final para aprobar.",
      nextStep: "Espera resultado o prioriza otro item.",
    }];
  }
  return [{
    title: "Pendiente de contexto",
    reason: "Quedo en cola sin senales claras de bloqueo.",
    impact: "Puede retrasar runs que si estan listos.",
    nextStep: "Abre el item y decide continuar o descartar.",
  }];
}

function queuePrimaryReason(run: MissionRun): string {
  const primary = queueRunNarratives(run)[0];
  return `${primary.title}: ${primary.reason}`;
}

function shortenObjective(value: string, maxLength = 78): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= maxLength) return compact;
  return `${compact.slice(0, maxLength - 1).trimEnd()}…`;
}

function parseGitDiffHunks(diffText: string): GitDiffHunk[] {
  if (!diffText.trim()) return [];
  const lines = diffText.split("\n");
  const hunks: GitDiffHunk[] = [];
  let currentHeader = "";
  let currentLines: string[] = [];
  const pushCurrent = () => {
    if (!currentHeader) return;
    const preview = currentLines.slice(0, 6).join("\n");
    hunks.push({ id: `${currentHeader}-${hunks.length}`, header: currentHeader, preview });
  };
  for (const line of lines) {
    if (line.startsWith("@@ ")) {
      pushCurrent();
      currentHeader = line.trim();
      currentLines = [];
      continue;
    }
    if (currentHeader) currentLines.push(line);
  }
  pushCurrent();
  return hunks;
}

function parsePlanStepsFromMessage(content: string): { steps: PlanStep[]; reasoning: string } | null {
  const lines = content.split("\n").map((line) => line.trim()).filter(Boolean);
  const stepLines = lines.filter((line) => /^\[?step\s*\d+/i.test(line));
  if (stepLines.length === 0) return null;

  const steps: PlanStep[] = stepLines.map((line, index) => {
    const cleaned = line.replace(/^\[?step\s*\d+\]?\s*[:.-]?\s*/i, "").trim();
    const [titlePart, detailPart] = cleaned.split("->").map((entry) => entry.trim());
    return {
      step_id: `draft-step-${index + 1}`,
      sequence: index + 1,
      title: titlePart || `Paso ${index + 1}`,
      description: detailPart || "",
      expected_outcome: detailPart || titlePart || `Resultado del paso ${index + 1}`,
      status: "pending",
      dependencies: index > 0 ? [`draft-step-${index}`] : [],
      assigned_to: "agent",
      chat_message_ids: [],
      artifacts: [],
      outcome: "",
      success_criteria_met: false,
      learnings: [],
    };
  });

  const reasoning = lines.find((line) => /^razonamiento[:]?/i.test(line))
    ?.replace(/^razonamiento[:]?/i, "").trim() || "Plan generado automaticamente desde respuesta del agente.";

  return { steps, reasoning };
}

// ===== MULTI-STEP PERSISTENT PLANNING SYSTEM (NEMO-BACKED) =====
// Types are imported from planNemoClient.ts

// Persists across Vite HMR module reloads (window survives, module scope does not)
declare global { interface Window { _injectedPublicPaths?: Set<string> } }
if (!window._injectedPublicPaths) window._injectedPublicPaths = new Set<string>();

export function App() {
  const [state, setState] = useState<MissionState>(initialState);
  const [selectedRunSource, setSelectedRunSource] = useState<string>("");
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [nemoState, setNemoState] = useState<NemoState | null>(null);
  const [nemoMcpStatus, setNemoMcpStatus] = useState<NemoMcpWatcherState | null>(null);
  const [selfInsights, setSelfInsights] = useState<SelfModInsights | null>(null);
  const [riskMap, setRiskMap] = useState<RiskMapState | null>(null);
  const [cognitiveStats, setCognitiveStats] = useState<CognitiveStatsState | null>(null);
  const [missionStats, setMissionStats] = useState<MissionStatsState | null>(null);
  const [selectedNemoTools, setSelectedNemoTools] = useState<string[]>(DEFAULT_SESSION_NEMO_TOOLS);
  const [settingsDraft, setSettingsDraft] = useState<MissionState["settings"]>(initialState.settings);
  const [repoDraft, setRepoDraft] = useState<string>(initialState.repo_path);
  const [cloneDraft, setCloneDraft] = useState({ url: "", destination: "" });
  const [reviewPlan, setReviewPlan] = useState<ReviewPlan | null>(null);
  const [applyHistory, setApplyHistory] = useState<ApplyHistoryItem[]>([]);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);
  const [orphanCleanupResult, setOrphanCleanupResult] = useState<OrphanCleanupResult | null>(null);
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
  const [queuedAgentPrompts, setQueuedAgentPrompts] = useState<string[]>([]);
  const [runTreeCollapsed, setRunTreeCollapsed] = useState<boolean>(false);
  const [lmStudioModel, setLmStudioModel] = useState<string | null>(null);
  const [lmStudioOnline, setLmStudioOnline] = useState<boolean | null>(null);
  const [expandedRunSources, setExpandedRunSources] = useState<Record<string, boolean>>({});
  const [autonomyMode, setAutonomyMode] = useState<AutonomyMode>("trusted");
  const [activeSection, setActiveSection] = useState<AppSection>("home");
    const [runsWorkbenchTab, setRunsWorkbenchTab] = useState<RunsWorkbenchTab>("file");
  const [reviewSubTab, setReviewSubTab] = useState<"timeline" | "diff">("diff");
  const [repoBusy, setRepoBusy] = useState<boolean>(false);
  const [repoError, setRepoError] = useState<string>("");
  const [terminalDraft, setTerminalDraft] = useState<string>("git status --short");
  const [terminalRunning, setTerminalRunning] = useState<boolean>(false);
  const [terminalResult, setTerminalResult] = useState<TerminalRunResult | null>(null);
  const [browserDraft, setBrowserDraft] = useState<string>("https://github.com");
  const [browserState, setBrowserState] = useState<BrowserState>({ homepage: "", last_url: "", history: [], search_query: "", search_history: [] });
  const [browserQueryDraft, setBrowserQueryDraft] = useState<string>("");
  const [browserSearchState, setBrowserSearchState] = useState<BrowserSearchState>({ query: "", engine: "", results: [] });
  const [browserSearching, setBrowserSearching] = useState<boolean>(false);
  const [extensions, setExtensions] = useState<ExtensionItem[]>([]);
  const [extensionsBusy, setExtensionsBusy] = useState<boolean>(false);
  const [gitBusy, setGitBusy] = useState<boolean>(false);
  const [gitStatus, setGitStatus] = useState<GitStatusState>({ repo_path: "", branch: "", ahead: 0, behind: 0, entries: [] });
  const [gitBranches, setGitBranches] = useState<GitBranchItem[]>([]);
  const [gitRemotes, setGitRemotes] = useState<GitRemote[]>([]);
  const [gitSyncRemote, setGitSyncRemote] = useState<string>("");
  const [gitSyncBranch, setGitSyncBranch] = useState<string>("");
  const [gitDiffPath, setGitDiffPath] = useState<string>("");
  const [gitDiffStaged, setGitDiffStaged] = useState<boolean>(false);
  const [gitDiffText, setGitDiffText] = useState<string>("");
  const [gitDiffHunks, setGitDiffHunks] = useState<GitDiffHunk[]>([]);
  const [gitSelectedHunks, setGitSelectedHunks] = useState<Record<string, boolean>>({});
  const [gitCommitMessage, setGitCommitMessage] = useState<string>("");
  const [gitBranchDraft, setGitBranchDraft] = useState<string>("");
  const agentRequestControllerRef = useRef<AbortController | null>(null);
  const queuedAgentPromptsRef = useRef<string[]>([]);
  const [handoffDraft, setHandoffDraft] = useState({
    objective: "",
    acceptance: "passes validation",
    validation: DEFAULT_HANDOFF_VALIDATION_COMMAND,
    validationPolicy: "smoke",
    targetFiles: "",
    provider: "subprocess",
    timeoutSeconds: "120",
  });

  // ===== PLANNING SYSTEM HOOKS =====
  const planState = usePlanState();
  const planNemoSync = usePlanNemoSync(
    planState.objective,
    planState.currentPlan,
    planState.activeStepId,
    {
      nemoEnabled: true,
      autoSync: true,
      syncInterval: 30000,
      onSyncError: (error) => console.error("Plan NEMO sync error:", error),
      onSyncSuccess: () => console.log("Plan synced to NEMO"),
    }
  );

  // Load plan from localStorage on mount
  useEffect(() => {
    planState.loadFromLocalStorage();
  }, []);

  // On mount, inject public HTML artifacts created by handoffs (runs once per module lifetime)
  useEffect(() => {
    const PUBLIC_HTML_PATHS = ["/game.html"];
    for (const path of PUBLIC_HTML_PATHS) {
      if (_injectedPublicPaths.has(path)) continue;
      _injectedPublicPaths.add(path);
      fetch(path)
        .then((r) => r.ok ? r.text() : Promise.reject())
        .then((html) => {
          if (!html.trim().startsWith("<!")) return;
          const name = path.split("/").pop()?.replace(".html", "") ?? "app";
          setAgentMessages((prev) => {
            if (prev.some((m) => m.id === `public-artifact-${name}`)) return prev;
            return [
              ...prev,
              {
                id: `public-artifact-${name}`,
                role: "assistant" as const,
                content: `**${name}** listo en el Artifact Studio:\n\n\`\`\`html_artifact\n${html}\n\`\`\``,
                actions: [],
                tool_calls: [],
              },
            ];
          });
          setActiveSection("home");
        })
        .catch(() => { _injectedPublicPaths.delete(path); /* retry allowed on next mount */ });
    }
  }, []);

  // Save plan to localStorage whenever it changes
  useEffect(() => {
    planState.saveToLocalStorage();
  }, [planState.objective, planState.currentPlan]);

  const [objectiveModalOpen, setObjectiveModalOpen] = useState(false);

  const openObjectiveModal = () => setObjectiveModalOpen(true);

  const handleCreateObjective = async (title: string, description: string, criteria: string[]) => {
    const created = planState.createObjective(title, description, criteria);
    await planNemoSync.syncObjective();
    setStatus(`Objetivo creado y sincronizado: ${created.title}`);
  };

  const generatePlanPrompt = () => {
    if (!planState.objective) {
      setStatus("Define un objetivo antes de generar el plan");
      return;
    }
    const criteria = planState.objective.acceptance_criteria.map((item, index) => `${index + 1}. ${item}`).join("\n");
    sendGuidedAgentPrompt(
      `Genera un plan ejecutable en formato [STEP N: titulo -> resultado esperado].\n` +
      `Objetivo: ${planState.objective.title}.\n` +
      `Descripcion: ${planState.objective.description || "sin descripcion"}.\n` +
      `Criterios de aceptacion:\n${criteria}`
    );
  };

  const handlePlanStepSelect = (stepId: string) => {
    planState.setActiveStepId(stepId);
    const selectedStep = planState.currentPlan?.steps.find((step) => step.step_id === stepId);
    if (selectedStep) setStatus(`Paso activo: ${selectedStep.sequence}. ${selectedStep.title}`);
  };

  const selectedRun = useMemo(() => state.runs.find((run) => run.source_json === selectedRunSource) ?? state.runs[0], [state.runs, selectedRunSource]);
  const selectedJob = useMemo(() => state.jobs.find((j) => j.task_id === selectedRun?.task_id && j.run_id === selectedRun?.run_id), [state.jobs, selectedRun]);
  const jobAwaitingPermission = useMemo(() => state.jobs.find((j) => j.status === "awaiting_permission") ?? null, [state.jobs]);
  const jobRunning = useMemo(() => state.jobs.find((j) => j.status === "running") ?? null, [state.jobs]);
  useEffect(() => {
    setReviewSubTab(selectedJob?.status === "running" ? "timeline" : "diff");
  }, [selectedJob?.job_id]);
  const readyRuns = state.runs.filter((run) => run.review_status === "awaiting_review").length;
  const blockedRuns = state.runs.filter((run) => run.review_status === "blocked").length;
  const activeFile = selectedFile || selectedRun?.changed_files[0] || "";
  const queuedAgentPrompt = queuedAgentPrompts[0] ?? null;

  const syncQueuedPrompts = (nextQueue: string[]) => {
    queuedAgentPromptsRef.current = nextQueue;
    setQueuedAgentPrompts(nextQueue);
  };

  const enqueueAgentPrompt = (content: string, options?: { highPriority?: boolean }) => {
    const normalized = content.trim();
    if (!normalized) return;
    const highPriority = Boolean(options?.highPriority);
    const withoutDuplicate = queuedAgentPromptsRef.current.filter((item) => item !== normalized);
    const nextQueue = highPriority ? [normalized, ...withoutDuplicate] : [...withoutDuplicate, normalized];
    const boundedQueue = nextQueue.slice(0, 8);
    syncQueuedPrompts(boundedQueue);
    setStatus(highPriority ? "High-priority steering queued" : `Agent busy: queued message (${boundedQueue.length})`);
  };

  const popQueuedAgentPrompt = () => {
    const [nextPrompt, ...rest] = queuedAgentPromptsRef.current;
    syncQueuedPrompts(rest);
    return nextPrompt;
  };

  const removeQueuedAgentPrompt = (index: number) => {
    if (index < 0 || index >= queuedAgentPromptsRef.current.length) return;
    const nextQueue = queuedAgentPromptsRef.current.filter((_, entryIndex) => entryIndex !== index);
    syncQueuedPrompts(nextQueue);
    setStatus("Removed queued message");
  };

  const prioritizeQueuedAgentPrompt = (index: number) => {
    if (index <= 0 || index >= queuedAgentPromptsRef.current.length) return;
    const picked = queuedAgentPromptsRef.current[index];
    const rest = queuedAgentPromptsRef.current.filter((_, entryIndex) => entryIndex !== index);
    syncQueuedPrompts([picked, ...rest]);
    setStatus("Moved queued message to front");
  };

  const postJson = <T extends object,>(path: string, body: object, init?: RequestInit): Promise<T> => fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    body: JSON.stringify(body),
    ...init,
  }).then(async (response) => {
    const rawText = await response.text();
    let payload: (T | ApiError | null) = null;
    if (rawText.trim()) {
      try {
        payload = JSON.parse(rawText) as T | ApiError;
      } catch {
        if (!response.ok) throw new Error(rawText.trim() || response.statusText || "Request failed");
        throw new Error("Invalid JSON response from server");
      }
    }
    if (!response.ok) {
      if (payload && typeof payload === "object" && "error" in payload) {
        throw new Error((payload as ApiError).error);
      }
      throw new Error(response.statusText || "Request failed");
    }
    if (!payload) throw new Error("Empty response from server");
    return payload as T;
  });

  const grantPermission = async (jobId: string, note: string) => {
    await fetch(`/api/run/${jobId}/permission-grant`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    refreshState();
  };

  const denyPermission = async (jobId: string, note: string) => {
    await fetch(`/api/run/${jobId}/permission-deny`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    refreshState();
  };

  const refreshState = () => {
    setStatus("Refreshing workspace state");
    fetch("/api/state", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`State endpoint failed (${response.status})`);
        return response.json();
      })
      .then((payload: MissionState) => {
        const nextState = normalizeState(payload);
        setState({ ...nextState, statusLoaded: true });
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        const nextRun = nextState.runs.find((run) => run.source_json === selectedRunSource) ?? nextState.runs[0];
        setSelectedRunSource(nextRun?.source_json ?? "");
        setSelectedFile(nextRun?.changed_files[0] ?? "");
        setStatus("Workspace state refreshed");
      })
      .catch((error: Error) => {
        setStatus(`Bridge offline: ${error.message}`);
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

  const loadNemoMcpStatus = () => {
    postJson<NemoMcpWatcherState>("/api/nemo/mcp-status", { nemo_mcp_url: normalizeNemoMcpUrl(settingsDraft.nemo_mcp_url), selected_nemo_tools: selectedNemoTools })
      .then((payload) => {
        setNemoMcpStatus(payload);
        if (Array.isArray(payload.selected_tools) && payload.selected_tools.length > 0) {
          setSelectedNemoTools(payload.selected_tools);
        }
      })
      .catch(() => setNemoMcpStatus(null));
  };

  const loadSelfInsights = (run: MissionRun | undefined) => {
    if (!run) {
      setSelfInsights(null);
      return;
    }
    postJson<SelfModInsights>("/api/self-mod/insights", { source_json: run.source_json })
      .then((payload) => setSelfInsights(payload))
      .catch(() => setSelfInsights(null));
  };

  const loadRiskMap = () => {
    postJson<RiskMapState>("/api/nemo/risk-map", { limit: 20 })
      .then((payload) => setRiskMap(payload))
      .catch(() => setRiskMap(null));
  };

  const loadCognitiveStats = (run?: MissionRun) => {
    postJson<CognitiveStatsState>("/api/nemo/cognitive-stats", { source_json: run?.source_json ?? "" })
      .then((payload) => setCognitiveStats(payload))
      .catch(() => setCognitiveStats({
        ok: false,
        enabled: false,
        error: "unavailable",
        health: { enabled: false, status: "error", db_path: null },
        run_kpis: {
          total_runs: 0,
          ready_runs: 0,
          blocked_runs: 0,
          apply_count: 0,
          apply_success_rate: 0,
          blocked_rate: 0,
          auto_apply_rate: 0,
          avg_run_to_apply_minutes: null,
        },
        memory_kpis: {
          atom_count: 0,
          correction_count: 0,
          evidence_count: 0,
          feedback_count: 0,
          useful_feedback_count: 0,
          not_useful_feedback_count: 0,
          useful_feedback_rate: null,
          portfolio_tokens: null,
          portfolio_budget: null,
          portfolio_utilization: null,
        },
      }));
  };

  const loadMissionStats = () => {
    fetch("/api/stats", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`Stats endpoint failed (${response.status})`);
        return response.json();
      })
      .then((payload: MissionStatsState) => setMissionStats(payload))
      .catch(() => setMissionStats(null));
  };

  const selectRun = (run: MissionRun) => {
    const nextFile = run.changed_files[0] ?? "";
    setSelectedRunSource(run.source_json);
    setSelectedFile(nextFile);
    setExpandedRunSources((current) => ({ ...current, [run.source_json]: true }));
    loadFilePreview(run, nextFile);
    loadNemoState(run);
    loadSelfInsights(run);
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

  const autoApplyRun = (run: MissionRun) => {
    if (autonomyMode === "manual") {
      setStatus("Manual mode requires review + manual apply");
      return;
    }
    setStatus(`Auto-applying run with ${autonomyMode} profile`);
    postJson<ApplyResult>("/api/apply", { source_json: run.source_json, autonomy_profile: autonomyMode })
      .then((payload) => {
        setApplyResults((current) => ({ ...current, [run.source_json]: payload.apply_json }));
        setState(payload.state);
        refreshApplyHistory();
        setStatus(`Auto-applied (${autonomyMode}) run. Rollback JSON: ${payload.apply_json}`);
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
      validation_commands: handoffValidationCommand(handoffDraft.validation),
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
      context_window_tokens: settingsDraft.context_window_tokens,
      chat_max_tokens: settingsDraft.chat_max_tokens,
      nemo_mcp_url: normalizeNemoMcpUrl(settingsDraft.nemo_mcp_url),
      nemo_mcp_prefix: "nemo.",
      require_nemo_mcp_capabilities: Boolean(settingsDraft.nemo_required || settingsDraft.nemo_mcp_url),
      require_nemo_roundtrip: Boolean(settingsDraft.nemo_required || settingsDraft.nemo_mcp_url),
      selected_nemo_tools: selectedNemoTools,
    })
      .then((payload) => {
        setActiveJob(payload.job);
        setComposerOpen(false);
        setStatus(`Job started: ${payload.job.job_id}`);
      })
      .catch((error: Error) => setStatus(error.message))
      .finally(() => setHandoffRunning(false));
  };

  const injectPublicHtmlArtifacts = (changedFiles: string[]) => {
    const PUBLIC_PREFIX = "apps/mission-control/public/";
    const htmlFiles = changedFiles.filter((f) => f.startsWith(PUBLIC_PREFIX) && f.endsWith(".html"));
    for (const filePath of htmlFiles) {
      const urlPath = "/" + filePath.slice(PUBLIC_PREFIX.length);
      fetch(urlPath)
        .then((r) => r.ok ? r.text() : Promise.reject(new Error(`${r.status}`)))
        .then((html) => {
          const title = urlPath.split("/").pop()?.replace(".html", "") ?? "app";
          setAgentMessages((prev) => [
            ...prev,
            {
              id: `handoff-artifact-${Date.now()}`,
              role: "assistant" as const,
              content: `Handoff completado. Aquí está el resultado renderizable:\n\n\`\`\`html_artifact\n${html}\n\`\`\``,
              actions: [],
              tool_calls: [],
            },
          ]);
          setActiveSection("home");
        })
        .catch(() => {/* silently skip if file not available yet */});
    }
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
          // Fetch public HTML files from the completed job and inject as artifacts
          fetch(`/api/run/${job.job_id}/public-html-files`)
            .then((r) => r.ok ? r.json() : Promise.reject())
            .then((data: { files: Array<{ url: string; content: string; path: string }> }) => {
              for (const f of data.files ?? []) {
                const name = f.url.split("/").pop()?.replace(".html", "") ?? "app";
                setAgentMessages((prev) => {
                  if (prev.some((m) => m.id === `handoff-artifact-${name}`)) return prev;
                  return [...prev, {
                    id: `handoff-artifact-${name}`,
                    role: "assistant" as const,
                    content: `**${name}** listo en el Artifact Studio:\n\n\`\`\`html_artifact\n${f.content}\n\`\`\``,
                    actions: [],
                    tool_calls: [],
                  }];
                });
                setActiveSection("home");
              }
            })
            .catch(() => {/* endpoint not yet available — useEffect probe will cover this */});
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

  const sendAgentMessage = (contentOverride?: string, options?: { highPriority?: boolean; forceQueue?: boolean }) => {
    const content = (contentOverride ?? agentDraft).trim();
    if (!content) return;
    if (agentBusy || options?.forceQueue) {
      enqueueAgentPrompt(content, options);
      if (contentOverride === undefined) setAgentDraft("");
      return;
    }
    const userMessage: AgentMessage = { id: `user-${Date.now()}`, role: "user", content };
    setAgentMessages((current) => [...current, userMessage]);
    if (contentOverride === undefined) setAgentDraft("");
    setAgentBusy(true);
    setStatus("Agent is inspecting context");
    const controller = new AbortController();
    agentRequestControllerRef.current = controller;

    const sendRequest = async () => {
      let enrichedMessage = content;
      if (planState.currentPlan && planState.activeStep) {
        const [portfolioContext, continuity] = await Promise.all([
          planNemoSync.generateAgentContext(),
          planNemoSync.getContextContinuity(),
        ]);
        const continuityBlock = continuity.previousPlans.length > 0 || continuity.learnings.length > 0
          ? `\nContinuidad interplanes:\n- Planes previos: ${continuity.previousPlans.join(" | ") || "sin registros"}\n- Learnings: ${continuity.learnings.join(" | ") || "sin learnings previos"}`
          : "";
        enrichedMessage =
          `Contexto de plan activo:\n` +
          `- Objetivo: ${planState.objective?.title || "sin objetivo"}\n` +
          `- Plan: ${planState.currentPlan.plan_id}\n` +
          `- Paso activo: ${planState.activeStep.sequence}. ${planState.activeStep.title}\n` +
          `- Resultado esperado: ${planState.activeStep.expected_outcome}\n` +
          `${portfolioContext ? `\nPortfolio NEMO:\n${portfolioContext}\n` : ""}` +
          `${continuityBlock}\n\nMensaje usuario:\n${content}`;
      }

      const recentHistory = agentMessages
        .slice(-12)
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role, content: m.content }));

      return postJson<AgentMessageResult>("/api/agent/message", {
        message: enrichedMessage,
        history: recentHistory,
        source_json: shouldAttachRunContextToMessage(content) ? selectedRun?.source_json : undefined,
        chat_mode: routeChatMode(content, Boolean(selectedRun?.source_json)),
        selected_nemo_tools: selectedNemoTools,
        nemo_mcp_url: normalizeNemoMcpUrl(settingsDraft.nemo_mcp_url),
        require_nemo_mcp_capabilities: Boolean(settingsDraft.nemo_required || settingsDraft.nemo_mcp_url),
        require_nemo_roundtrip: false,
        provider: settingsDraft.provider,
        model_base_url: settingsDraft.model_base_url,
        default_model: settingsDraft.default_model,
        timeout_seconds: handoffDraft.timeoutSeconds,
        token_budget: settingsDraft.token_budget,
        context_window_tokens: settingsDraft.context_window_tokens,
        chat_max_tokens: settingsDraft.chat_max_tokens,
      }, { signal: controller.signal });
    };

    sendRequest()
      .then((payload) => {
        setAgentMessages((current) => [...current, payload.message]);
        loadMissionStats();
        if (planState.objective && !planState.currentPlan) {
          const parsed = parsePlanStepsFromMessage(payload.message.content || "");
          if (parsed) {
            const createdPlan = planState.createPlan(planState.objective.objective_id, parsed.steps, parsed.reasoning);
            planNemoSync.syncPlan();
            setStatus(`Plan creado con ${createdPlan.steps.length} pasos y sincronizado en NEMO`);
            return;
          }
        }
        setStatus("Agent proposed next actions");
      })
      .catch((error: Error) => {
        if (error.name === "AbortError") {
          setStatus("Agent response stopped");
          return;
        }
        if (error.message.toLowerCase().includes("too many requests") || error.message.toLowerCase().includes("rate_limited") || error.message.includes("429")) {
          setStatus("Backpressure activo (429). Mensaje encolado; reintentando en cuanto se libere la ventana.");
          enqueueAgentPrompt(content, { highPriority: true });
          return;
        }
        setStatus(error.message);
        setAgentMessages((current) => [...current, {
          id: `assistant-error-${Date.now()}`,
          role: "assistant",
          content: `No pude completar la respuesta: ${error.message}`,
        }]);
      })
      .finally(() => {
        setAgentBusy(false);
        agentRequestControllerRef.current = null;
        const queued = popQueuedAgentPrompt();
        if (queued) {
          setTimeout(() => sendAgentMessage(queued), 0);
        }
      });
  };

  const stopAgentMessage = () => {
    if (!agentBusy) return;
    agentRequestControllerRef.current?.abort();
  };

  const sendHomeAgentMessage = (mode: "send" | "queue" | "steer" | "plan" = "send") => {
    const content = homeAgentDraft.trim();
    if (!content && mode !== "plan") return;

    if (mode === "plan") {
      const planRequest = planState.objective
        ? [
          "Genera o refina un plan multi-step para este objetivo activo.",
          `Objetivo: ${planState.objective.title}`,
          `Descripcion: ${planState.objective.description || "sin descripcion"}`,
          `Criterios: ${(planState.objective.acceptance_criteria || []).join(" | ") || "sin criterios"}`,
          content ? `Contexto adicional del chat: ${content}` : "",
          "Formato requerido: [STEP N: titulo -> resultado esperado]",
        ].filter(Boolean).join("\n")
        : [
          "Convierte este pedido en un plan ejecutable multi-step.",
          `Pedido del usuario: ${content || "proponer plan para el run seleccionado"}`,
          "Formato requerido: [STEP N: titulo -> resultado esperado]",
        ].join("\n");
      setHomeAgentDraft("");
      sendAgentMessage(planRequest, { highPriority: true });
      return;
    }

    setHomeAgentDraft("");
    sendAgentMessage(content, {
      highPriority: mode === "steer",
      forceQueue: mode === "queue",
    });
  };

  const openMemorySection = () => {
    setActiveSection("memory");
    setStatus("Opening NEMO memory panel");
  };

  const sendGuidedAgentPrompt = (prompt: string) => {
    if (!prompt.trim()) return;
    sendAgentMessage(prompt, { highPriority: true });
  };

  const runAgentAction = (action: AgentAction) => {
    if (action.kind === "layout") {
      setActiveSection("home");
      setStatus(`Layout command queued: ${action.label}`);
      return;
    }
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
    if (action.kind === "self_modify") {
      setStatus(`Starting self-modification: ${action.label}`);
      postJson<HandoffJobResult>("/api/self-modify/start", action.payload)
        .then((payload) => {
          setActiveJob(payload.job);
          setStatus(`Self-modification job started: ${payload.job.job_id}`);
        })
        .catch((error: Error) => setStatus(error.message));
      return;
    }
    if (action.kind === "review") {
      const sourceJson = String(action.payload.source_json || selectedRun?.source_json || "");
      if (!sourceJson) {
        setStatus("Review action requires a selected run");
        return;
      }
      setStatus("Refreshing merge plan from agent action");
      postJson<{ plan: ReviewPlan }>("/api/review", { source_json: sourceJson })
        .then((payload) => {
          setReviewPlan(payload.plan);
          setStatus(payload.plan.mergeable ? `Merge plan ready: ${payload.plan.files.length} files` : `Review blocked: ${payload.plan.risk_flags.join(", ")}`);
        })
        .catch((error: Error) => setStatus(error.message));
      return;
    }
    if (action.kind === "evaluate") {
      const sourceJson = String(action.payload.source_json || selectedRun?.source_json || "");
      if (!sourceJson) {
        setStatus("Evaluate action requires a selected run");
        return;
      }
      setStatus("Evaluating selected run readiness");
      postJson<{ grade: string; score: number; spec10_score: number; reasons?: string[] }>("/api/eval", { source_json: sourceJson })
        .then((payload) => {
          const reasons = payload.reasons?.length ? ` / ${payload.reasons.join(", ")}` : "";
          setStatus(`Eval ${payload.grade} score ${payload.score} spec10 ${payload.spec10_score}${reasons}`);
        })
        .catch((error: Error) => setStatus(error.message));
      return;
    }
    if (action.kind === "run" || action.kind === "handoff") {
      const objective = String(action.payload.objective || action.label || "Chat objective");
      setStatus(`Starting ${action.kind} from chat: ${objective}`);
      postJson<HandoffJobResult>("/api/handoff/start", {
        objective,
        acceptance_criteria: String(action.payload.acceptance_criteria || "passes requested goal"),
        validation_commands: handoffValidationCommand(action.payload.validation_commands),
        target_files: String(action.payload.target_files || ""),
        provider: String(action.payload.provider || settingsDraft.provider),
        timeout_seconds: String(action.payload.timeout_seconds || handoffDraft.timeoutSeconds || settingsDraft.timeout_seconds),
        model: settingsDraft.default_model,
        base_url: settingsDraft.model_base_url,
        max_runtime_minutes: settingsDraft.max_runtime_minutes,
        heartbeat_minutes: settingsDraft.heartbeat_minutes,
        max_heartbeats: settingsDraft.max_heartbeats,
        token_budget: settingsDraft.token_budget,
        nemo_mcp_url: normalizeNemoMcpUrl(settingsDraft.nemo_mcp_url),
        nemo_mcp_prefix: "nemo.",
        selected_nemo_tools: selectedNemoTools,
        require_nemo_mcp_capabilities: true,
        require_nemo_roundtrip: false,
      })
        .then((payload) => {
          setActiveJob(payload.job);
          setStatus(`${action.kind} started: ${payload.job.job_id}`);
        })
        .catch((error: Error) => setStatus(error.message));
      return;
    }
    if (action.kind === "plan_generate") {
      const planPayload = action.payload as Record<string, unknown>;
      const objective = String(planPayload.objective || action.label || "Generate code");
      setStatus(`Generating: ${objective.slice(0, 60)}`);

      const planMsgId = `plan-${Date.now()}`;
      setAgentMessages((prev) => [
        ...prev,
        {
          id: planMsgId,
          role: "assistant" as const,
          content: `**Generando:** ${objective.slice(0, 80)}\nIteraciones en progreso…`,
          tool_calls: [],
          actions: [],
        },
      ]);

      fetch("/api/agent/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(planPayload),
      })
        .then(async (resp) => {
          const reader = resp.body!.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          const iterations: string[] = [];
          let planJobId = "";

          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (!line.startsWith("data: ")) continue;
              try {
                const evt = JSON.parse(line.slice(6)) as Record<string, unknown>;

                if (evt.type === "start") {
                  planJobId = String(evt.job_id || "");
                  // Inject Stop + Steer controls as actions on the plan message
                  if (planJobId) {
                    setAgentMessages((prev) =>
                      prev.map((m) =>
                        m.id === planMsgId
                          ? {
                              ...m,
                              actions: [
                                {
                                  id: `stop-${planJobId}`,
                                  kind: "plan_cancel",
                                  label: "Stop",
                                  summary: "Cancel after current iteration",
                                  payload: { job_id: planJobId },
                                },
                                {
                                  id: `steer-${planJobId}`,
                                  kind: "plan_steer",
                                  label: "Steer",
                                  summary: "Inject a directive into the next iteration",
                                  payload: { job_id: planJobId },
                                },
                              ],
                            }
                          : m
                      )
                    );
                  }
                }

                if (evt.type === "iteration") {
                  const ok = evt.exec_ok ? "✓" : "✗";
                  const score = Number(evt.score ?? 0);
                  const scoreBar = "█".repeat(Math.round(score)) + "░".repeat(10 - Math.round(score));
                  const outputLines = String(evt.exec_output || "").trim().split("\n").filter(Boolean).slice(0, 4);
                  const thinkRaw = String(evt.think_snippet || "").trim();
                  let iterLine = `**Iter ${evt.iteration}** ${ok}  score ${score}/10  \`${scoreBar}\``;
                  if (outputLines.length > 0) iterLine += `\n> ${outputLines.join("\n> ")}`;
                  if (thinkRaw) iterLine += `\n> 💭 ${thinkRaw.slice(0, 160).replace(/\n/g, " ")}`;
                  iterations.push(iterLine);
                  setAgentMessages((prev) =>
                    prev.map((m) =>
                      m.id === planMsgId
                        ? { ...m, content: `**Generando:** ${objective.slice(0, 70)}\n\n${iterations.join("\n\n")}` }
                        : m
                    )
                  );
                }

                if (evt.type === "cancelled") {
                  setAgentMessages((prev) =>
                    prev.map((m) =>
                      m.id === planMsgId
                        ? {
                            ...m,
                            content: `**Plan cancelado:** ${objective.slice(0, 70)}\n\n${iterations.join("\n\n")}`,
                            actions: [],
                          }
                        : m
                    )
                  );
                  setStatus("Plan cancelled");
                }

                if (evt.type === "done") {
                  const score = Number(evt.final_score ?? 0);
                  const iters = Number(evt.iterations_run ?? 0);
                  const completed = Boolean(evt.completed);
                  const artifactFile = String(evt.artifact_file || "");
                  const scoreBar = "█".repeat(Math.round(score)) + "░".repeat(10 - Math.round(score));
                  let finalContent = [
                    `**Plan completado:** ${objective.slice(0, 70)}`,
                    `Score final: **${score}/10** \`${scoreBar}\`  |  ${iters} iteraciones${!completed ? "  *(no alcanzó threshold)*" : ""}`,
                    iterations.join("\n\n"),
                  ].join("\n\n");

                  if (artifactFile && score > 0) {
                    const imgData = JSON.stringify({ src: `/api/artifacts/image/${artifactFile}`, alt: objective });
                    finalContent += `\n\`\`\`image\n${imgData}\n\`\`\``;
                  }

                  setAgentMessages((prev) =>
                    prev.map((m) =>
                      m.id === planMsgId ? { ...m, content: finalContent, actions: [] } : m
                    )
                  );
                  setStatus(`Plan done: ${score}/10`);
                }
              } catch {
                // ignore malformed SSE lines
              }
            }
          }
        })
        .catch((err: Error) => {
          setStatus(`Plan error: ${err.message}`);
          setAgentMessages((prev) =>
            prev.map((m) =>
              m.id === planMsgId
                ? { ...m, content: `Error en plan_generate: ${err.message}`, actions: [] }
                : m
            )
          );
        });
      return;
    }
    if (action.kind === "plan_cancel") {
      const jobId = String(action.payload.job_id || "");
      if (jobId) {
        fetch("/api/agent/plan/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: jobId }),
        }).catch(() => null);
      }
      return;
    }
    if (action.kind === "plan_steer") {
      const jobId = String(action.payload.job_id || "");
      if (!jobId) return;
      const directive = window.prompt("Directiva para la siguiente iteración:");
      if (!directive?.trim()) return;
      fetch("/api/agent/plan/steer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, directive: directive.trim() }),
      }).catch(() => null);
      return;
    }
    if (action.kind === "pc_control") {
      const mode = String(action.payload.mode || "");
      const command = String(action.payload.command || "");
      const url = String(action.payload.url || "");
      const query = String(action.payload.query || "");
      const timeoutSeconds = Number(action.payload.timeout_seconds || 45);
      const authSummary = mode === "terminal_run"
        ? `Comando: ${command}`
        : mode === "browser_open"
          ? `URL: ${url}`
          : `Busqueda: ${query}`;
      if (!window.confirm(`Autorizar control de PC para esta accion?\n${authSummary}`)) {
        setStatus("PC control action rejected by user");
        return;
      }
      if (mode === "terminal_run") {
        if (!command.trim()) {
          setStatus("PC control terminal action requires command");
          return;
        }
        setActiveSection("terminal");
        setTerminalDraft(command);
        setTerminalRunning(true);
        setStatus(`Running authorized terminal command: ${command}`);
        postJson<TerminalRunResult>("/api/terminal/run", { command, timeout_seconds: timeoutSeconds })
          .then((payload) => {
            setTerminalResult(payload);
            setStatus(payload.ok ? `Terminal command completed (${payload.duration_ms} ms)` : `Terminal command failed (${payload.exit_code ?? "timeout"})`);
          })
          .catch((error: Error) => setStatus(error.message))
          .finally(() => setTerminalRunning(false));
        return;
      }
      if (mode === "browser_open") {
        if (!url.trim()) {
          setStatus("PC control browser action requires URL");
          return;
        }
        setActiveSection("browser");
        setBrowserDraft(url);
        setStatus(`Opening authorized browser URL: ${url}`);
        postJson<{ ok: boolean; url: string; opened: boolean; history: string[] }>("/api/browser/open", { url })
          .then((payload) => {
            setBrowserState((current) => ({ ...current, last_url: payload.url, history: payload.history ?? current.history }));
            setStatus(payload.opened ? `Browser opened: ${payload.url}` : `Browser URL saved: ${payload.url}`);
          })
          .catch((error: Error) => setStatus(error.message));
        return;
      }
      if (mode === "browser_search") {
        if (!query.trim()) {
          setStatus("PC control search action requires query");
          return;
        }
        setActiveSection("browser");
        setBrowserQueryDraft(query);
        setBrowserSearching(true);
        setStatus(`Running authorized web search: ${query}`);
        postJson<{ ok: boolean; query: string; engine: string; search_url: string; results: BrowserSearchItem[]; history?: string[]; search_history?: string[] }>("/api/browser/search", {
          query,
          max_results: 8,
          timeout_seconds: timeoutSeconds,
        })
          .then((payload) => {
            setBrowserSearchState({ query: payload.query, engine: payload.engine, results: payload.results ?? [] });
            if (payload.search_url) setBrowserDraft(payload.search_url);
            setBrowserState((current) => ({
              ...current,
              last_url: payload.search_url || current.last_url,
              history: payload.history ?? current.history,
              search_query: payload.query,
              search_history: payload.search_history ?? current.search_history,
            }));
            setStatus(`Search completed: ${payload.results?.length ?? 0} result(s)`);
          })
          .catch((error: Error) => setStatus(error.message))
          .finally(() => setBrowserSearching(false));
        return;
      }
      setStatus(`Unsupported PC control mode: ${mode || "unknown"}`);
      return;
    }
    setStatus(`Starting agent action: ${action.label}`);
    postJson<HandoffJobResult>("/api/handoff/start", {
      ...action.payload,
      validation_commands: handoffValidationCommand(action.payload.validation_commands),
      nemo_mcp_url: String(action.payload.nemo_mcp_url || settingsDraft.nemo_mcp_url || initialState.settings.nemo_mcp_url),
      nemo_mcp_prefix: String(action.payload.nemo_mcp_prefix || "nemo."),
      selected_nemo_tools: selectedNemoTools,
    })
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
    setStatus("Switching chat to LM Studio");
    postJson<{ settings: MissionState["settings"]; state: MissionState }>("/api/settings", nextSettings)
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setSettingsDraft(normalizeSettings(payload.settings));
        setState(nextState);
        setStatus("Real mode enabled: LM Studio");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const openRepo = (repoPath = repoDraft) => {
    setRepoBusy(true);
    setRepoError("");
    setStatus("Opening repository");
    postJson<{ state: MissionState }>("/api/repo/open", { repo_path: repoPath })
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        setActiveSection(nextState.runs.length > 0 ? "runs" : "home");
        setStatus(`Opened repo: ${nextState.repo_path}`);
      })
      .catch((error: Error) => {
        setRepoError(error.message);
        setStatus(error.message);
      })
      .finally(() => setRepoBusy(false));
  };

  const browseRepoFolder = async (): Promise<string | null> => {
    setStatus("Opening folder picker");
    try {
      const payload = await postJson<{ ok: boolean; path?: string | null }>("/api/repo/pick-folder", {});
      const picked = (payload.path ?? "").trim();
      if (!picked) {
        setStatus("Folder picker canceled");
        return null;
      }
      setStatus(`Folder selected: ${picked}`);
      return picked;
    } catch (error) {
      const message = error instanceof Error ? error.message : "folder picker unavailable";
      setStatus(message);
      return null;
    }
  };

  const cloneRepo = () => {
    setRepoBusy(true);
    setRepoError("");
    setStatus("Cloning repository");
    postJson<{ state: MissionState }>("/api/repo/clone", cloneDraft)
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        setSettingsDraft(nextState.settings);
        setRepoDraft(nextState.repo_path);
        setCloneDraft({ url: "", destination: "" });
        setActiveSection(nextState.runs.length > 0 ? "runs" : "home");
        setStatus(`Cloned and opened repo: ${nextState.repo_path}`);
      })
      .catch((error: Error) => {
        setRepoError(error.message);
        setStatus(error.message);
      })
      .finally(() => setRepoBusy(false));
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

  const cleanupOrphanJobs = (dryRun: boolean) => {
    setStatus(dryRun ? "Buscando jobs huerfanos" : "Limpiando jobs huerfanos");
    postJson<OrphanCleanupResult>("/api/jobs/orphans", { dry_run: dryRun, max_age_minutes: 30 })
      .then((payload) => {
        setOrphanCleanupResult(payload);
        setStatus(dryRun ? `Detectados ${payload.summary.total} job(s) huerfanos` : `Marcados ${payload.summary.marked_jobs.length} y borrados ${payload.summary.deleted_snapshots.length}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const clearAgentChat = () => {
    if (confirm("¿Borrar todo el historial del chat del agente?")) {
      setAgentMessages([]);
      setAgentDraft("");
      setHomeAgentDraft("");
      setQueuedAgentPrompts([]);
      setStatus("Chat limpiado ✓");
    }
  };

  const archiveAgentChat = () => {
    if (agentMessages.length === 0) {
      setStatus("No hay mensajes para archivar");
      return;
    }
    const firstUserMessage = agentMessages.find((m) => m.role === "user")?.content.trim();
    const title = firstUserMessage ? firstUserMessage.slice(0, 80) : "Chat archivado";
    archiveChatSession(agentMessages, title);  // writes archive + clears session key
    setAgentMessages([]);
    setAgentDraft("");
    setHomeAgentDraft("");
    setQueuedAgentPrompts([]);
    setStatus("Chat archivado ✓");
  };

  const startNewChat = () => {
    if (confirm("¿Iniciar un nuevo chat? El historial actual se cerrará sin borrar los archivos del proyecto.")) {
      setAgentMessages([]);
      setAgentDraft("");
      setHomeAgentDraft("");
      setQueuedAgentPrompts([]);
      setStatus("Nuevo chat iniciado ✓");
    }
  };

  const archiveOldRuns = () => {
    if (!confirm("¿Limpiar runs antiguos? (backend persistente, no reaparecen al refrescar)")) return;
    setStatus("Limpiando runs antiguos...");
    postJson<RunsCleanupResult>("/api/runs/cleanup", { mode: "old", older_than_hours: 24, keep_latest: 8 })
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        const nextSelected = nextState.runs.find((run) => run.source_json === selectedRunSource) ?? nextState.runs[0];
        setSelectedRunSource(nextSelected?.source_json ?? "");
        setSelectedFile(nextSelected?.changed_files[0] ?? "");
        setStatus(payload.removed_count > 0 ? `Runs antiguos limpiados: ${payload.removed_count} ✓` : "No había runs antiguos para limpiar");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const clearAllRuns = () => {
    if (!confirm("¿Limpiar TODOS los runs? Esta acción es persistente y no se revierten al refrescar.")) return;
    setStatus("Limpiando todos los runs...");
    postJson<RunsCleanupResult>("/api/runs/cleanup", { mode: "all" })
      .then((payload) => {
        const nextState = normalizeState(payload.state);
        setState(nextState);
        setSelectedRunSource("");
        setSelectedFile("");
        setStatus(`Todos los runs limpiados: ${payload.removed_count} ✓`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const runTerminal = () => {
    const command = terminalDraft.trim();
    if (!command || terminalRunning) return;
    setTerminalRunning(true);
    setStatus(`Running terminal command: ${command}`);
    postJson<TerminalRunResult>("/api/terminal/run", { command, timeout_seconds: 45 })
      .then((payload) => {
        setTerminalResult(payload);
        setStatus(payload.ok ? `Terminal command completed (${payload.duration_ms} ms)` : `Terminal command failed (${payload.exit_code ?? "timeout"})`);
      })
      .catch((error: Error) => setStatus(error.message))
      .finally(() => setTerminalRunning(false));
  };

  const loadBrowserState = () => {
    fetch("/api/browser", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload: { homepage?: string; last_url?: string; history?: string[]; search_query?: string; search_history?: string[] }) => {
        setBrowserState({
          homepage: payload.homepage ?? "",
          last_url: payload.last_url ?? "",
          history: payload.history ?? [],
          search_query: payload.search_query ?? "",
          search_history: payload.search_history ?? [],
        });
        if (payload.last_url) setBrowserDraft(payload.last_url);
        if (payload.search_query) setBrowserQueryDraft(payload.search_query);
      })
      .catch(() => {
        setBrowserState({ homepage: "", last_url: "", history: [], search_query: "", search_history: [] });
      });
  };

  const openBrowserUrl = () => {
    const url = browserDraft.trim();
    if (!url) return;
    setStatus(`Opening browser URL: ${url}`);
    postJson<{ ok: boolean; url: string; opened: boolean; history: string[] }>("/api/browser/open", { url })
      .then((payload) => {
        setBrowserState((current) => ({
          ...current,
          last_url: payload.url,
          history: payload.history,
        }));
        setStatus(payload.opened ? `Browser opened: ${payload.url}` : `Browser URL saved: ${payload.url}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const searchBrowserWeb = () => {
    const query = browserQueryDraft.trim();
    if (!query || browserSearching) return;
    setBrowserSearching(true);
    setStatus(`Searching web with embedded Chromium: ${query}`);
    postJson<{ ok: boolean; query: string; engine: string; search_url: string; results: BrowserSearchItem[]; history?: string[]; search_history?: string[] }>("/api/browser/search", {
      query,
      max_results: 6,
      timeout_seconds: 20,
    })
      .then((payload) => {
        setBrowserSearchState({ query: payload.query, engine: payload.engine, results: payload.results ?? [] });
        if (payload.search_url) setBrowserDraft(payload.search_url);
        setBrowserState((current) => ({
          ...current,
          last_url: payload.search_url || current.last_url,
          history: payload.history ?? current.history,
          search_query: payload.query,
          search_history: payload.search_history ?? current.search_history,
        }));
        setStatus(`Search completed with ${payload.engine}: ${payload.results.length} result(s)`);
      })
      .catch((error: Error) => {
        setBrowserSearchState({ query, engine: "", results: [] });
        setStatus(error.message);
      })
      .finally(() => setBrowserSearching(false));
  };

  const openBrowserSearchResult = (url: string) => {
    setBrowserDraft(url);
    setStatus(`Opening search result: ${url}`);
    postJson<{ ok: boolean; url: string; opened: boolean; history: string[] }>("/api/browser/open", { url })
      .then((payload) => {
        setBrowserState((current) => ({
          ...current,
          last_url: payload.url,
          history: payload.history,
        }));
        setStatus(payload.opened ? `Browser opened: ${payload.url}` : `Browser URL saved: ${payload.url}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const loadExtensions = () => {
    setExtensionsBusy(true);
    fetch("/api/extensions", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload: { extensions?: ExtensionItem[] }) => {
        setExtensions(payload.extensions ?? []);
      })
      .catch(() => setExtensions([]))
      .finally(() => setExtensionsBusy(false));
  };

  const toggleExtension = (name: string, enabled: boolean) => {
    setStatus(`${enabled ? "Enabling" : "Disabling"} extension: ${name}`);
    postJson<{ extensions: ExtensionItem[] }>("/api/extensions", { action: "toggle", name, enabled })
      .then((payload) => {
        setExtensions(payload.extensions ?? []);
        setStatus(`Extension updated: ${name}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const loadGitStatus = () => {
    setGitBusy(true);
    fetch("/api/git/status", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as GitStatusState | ApiError;
        if (!response.ok) throw new Error("error" in payload ? payload.error : "Failed to load git status");
        return payload as GitStatusState;
      })
      .then((payload) => {
        setGitStatus({
          repo_path: payload.repo_path,
          branch: payload.branch,
          ahead: payload.ahead,
          behind: payload.behind,
          entries: payload.entries ?? [],
        });
      })
      .catch((error: Error) => {
        setStatus(error.message);
        setGitStatus({ repo_path: state.repo_path, branch: "", ahead: 0, behind: 0, entries: [] });
      })
      .finally(() => setGitBusy(false));
  };

  const loadGitBranches = () => {
    fetch("/api/git/branches", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { branches?: GitBranchItem[]; error?: string };
        if (!response.ok) throw new Error(payload.error || "Failed to load branches");
        return payload;
      })
      .then((payload) => setGitBranches(payload.branches ?? []))
      .catch(() => setGitBranches([]));
  };

  const loadGitRemotes = () => {
    fetch("/api/git/remotes", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { remotes?: GitRemote[]; default_remote?: string; default_branch?: string; error?: string };
        if (!response.ok) throw new Error(payload.error || "Failed to load remotes");
        return payload;
      })
      .then((payload) => {
        const remotes = payload.remotes ?? [];
        setGitRemotes(remotes);
        if (payload.default_remote) setGitSyncRemote(payload.default_remote);
        if (payload.default_branch) setGitSyncBranch(payload.default_branch);
      })
      .catch(() => {
        setGitRemotes([]);
      });
  };

  const loadGitDiff = (path = gitDiffPath, staged = gitDiffStaged) => {
    postJson<{ diff: string; stderr?: string }>("/api/git/diff", { path, staged })
      .then((payload) => {
        const text = payload.diff || payload.stderr || "";
        setGitDiffStaged(staged);
        setGitDiffText(text);
        const hunks = parseGitDiffHunks(text);
        setGitDiffHunks(hunks);
        setGitSelectedHunks(Object.fromEntries(hunks.map((hunk) => [hunk.id, false])));
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const stageGitPath = (path: string, stage: boolean) => {
    setStatus(`${stage ? "Staging" : "Unstaging"} ${path}`);
    postJson<{ ok: boolean }>("/api/git/stage", { path, stage })
      .then(() => {
        setStatus(`${stage ? "Staged" : "Unstaged"} ${path}`);
        loadGitStatus();
        if (gitDiffPath === path) loadGitDiff(path);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const commitGit = () => {
    const message = gitCommitMessage.trim();
    if (!message) {
      setStatus("Commit message is required");
      return;
    }
    setStatus("Creating git commit");
    postJson<{ ok: boolean; stderr?: string; stdout?: string }>("/api/git/commit", { message })
      .then((payload) => {
        if (!payload.ok) throw new Error(payload.stderr || payload.stdout || "Commit failed");
        setGitCommitMessage("");
        loadGitStatus();
        loadGitBranches();
        setStatus("Commit created");
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const checkoutGitBranch = (branch: string, create: boolean) => {
    const name = branch.trim();
    if (!name) {
      setStatus("Branch name is required");
      return;
    }
    setStatus(`${create ? "Creating" : "Switching to"} branch ${name}`);
    postJson<{ ok: boolean; stderr?: string; stdout?: string }>("/api/git/checkout", { branch: name, create })
      .then((payload) => {
        if (!payload.ok) throw new Error(payload.stderr || payload.stdout || "Checkout failed");
        if (create) setGitBranchDraft("");
        loadGitStatus();
        loadGitBranches();
        setStatus(`${create ? "Created" : "Checked out"} branch ${name}`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const syncGit = (direction: "pull" | "push") => {
    setStatus(`Running git ${direction}`);
    postJson<{ ok: boolean; stderr?: string; stdout?: string }>("/api/git/sync", {
      direction,
      rebase: direction === "pull",
      remote: gitSyncRemote,
      branch: gitSyncBranch,
    })
      .then((payload) => {
        if (!payload.ok) throw new Error(payload.stderr || payload.stdout || `git ${direction} failed`);
        loadGitStatus();
        loadGitBranches();
        setStatus(`git ${direction} completed`);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const stageGitHunk = (path: string, hunkHeader: string, stage: boolean) => {
    setStatus(`${stage ? "Staging" : "Unstaging"} hunk ${hunkHeader}`);
    postJson<{ ok: boolean }>("/api/git/stage-hunk", { path, hunk_header: hunkHeader, stage })
      .then(() => {
        setStatus(`${stage ? "Staged" : "Unstaged"} hunk ${hunkHeader}`);
        loadGitStatus();
        loadGitDiff(path, gitDiffStaged);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  const toggleGitHunkSelection = (hunkId: string) => {
    setGitSelectedHunks((current) => ({ ...current, [hunkId]: !(current[hunkId] ?? false) }));
  };

  const applySelectedGitHunks = (stage: boolean) => {
    if (!gitDiffPath.trim()) {
      setStatus("Select a diff path before applying hunks");
      return;
    }
    const selected = Object.entries(gitSelectedHunks)
      .filter(([, isSelected]) => isSelected)
      .map(([hunkId]) => gitDiffHunks.find((hunk) => hunk.id === hunkId)?.header)
      .filter((header): header is string => Boolean(header));
    if (selected.length === 0) {
      setStatus("No hunks selected");
      return;
    }
    setStatus(`${stage ? "Staging" : "Unstaging"} ${selected.length} selected hunks`);
    const runBatch = async () => {
      for (const header of selected) {
        await postJson<{ ok: boolean }>("/api/git/stage-hunk", {
          path: gitDiffPath,
          hunk_header: header,
          stage,
        });
      }
    };
    runBatch()
      .then(() => {
        setStatus(`${stage ? "Staged" : "Unstaged"} ${selected.length} hunks`);
        loadGitStatus();
        loadGitDiff(gitDiffPath, gitDiffStaged);
      })
      .catch((error: Error) => setStatus(error.message));
  };

  useEffect(() => {
    refreshState();
    refreshApplyHistory();
    loadBrowserState();
    loadExtensions();
    loadNemoMcpStatus();
    loadRiskMap();
    loadMissionStats();

    try {
      const raw = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<ChatSessionSnapshot>;
      if (Array.isArray(parsed.agentMessages) && parsed.agentMessages.length > 0) {
        setAgentMessages(parsed.agentMessages.filter((item): item is AgentMessage => Boolean(item && typeof item === "object" && item.id && item.role && item.content)));
      }
      if (Array.isArray(parsed.queuedAgentPrompts)) {
        const nextQueue = parsed.queuedAgentPrompts.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
        queuedAgentPromptsRef.current = nextQueue;
        setQueuedAgentPrompts(nextQueue);
      }
      if (parsed.missionStats && typeof parsed.missionStats === "object") {
        setMissionStats(parsed.missionStats as MissionStatsState);
      }
      if (Array.isArray(parsed.selectedNemoTools) && parsed.selectedNemoTools.length > 0) {
        setSelectedNemoTools(parsed.selectedNemoTools.filter((item): item is string => typeof item === "string"));
      }
    } catch {
      // Ignore corrupted local session snapshot.
    }
  }, []);

  useEffect(() => {
    const probe = () => {
      fetch("/lm-proxy/v1/models", { signal: AbortSignal.timeout(3000) })
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((data: { data?: Array<{ id: string }> }) => {
          const chatModel = data?.data?.find((m) => !/embed|rerank|bge|nomic/i.test(m.id))?.id ?? data?.data?.[0]?.id ?? null;
          setLmStudioModel(chatModel);
          setLmStudioOnline(true);
        })
        .catch(() => {
          setLmStudioModel(null);
          setLmStudioOnline(false);
        });
    };
    probe();
    const id = setInterval(probe, 10_000);
    return () => clearInterval(id);
  }, [settingsDraft.model_base_url]);

  useEffect(() => {
    const snapshot: ChatSessionSnapshot = {
      version: 1,
      agentMessages,
      queuedAgentPrompts,
      missionStats,
      selectedNemoTools,
    };
    try {
      window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      // Ignore quota issues and keep app running.
    }
  }, [agentMessages, queuedAgentPrompts, missionStats, selectedNemoTools]);

  useEffect(() => {
    if (activeSection !== "versioning") return;
    loadGitStatus();
    loadGitBranches();
    loadGitRemotes();
  }, [activeSection]);

  useEffect(() => {
    if (selectedRun && activeFile) loadFilePreview(selectedRun, activeFile);
    loadNemoState(selectedRun);
    loadSelfInsights(selectedRun);
    loadCognitiveStats(selectedRun);
  }, [selectedRunSource]);

  useEffect(() => {
    // Avoid landing on an empty runs canvas when the workspace has no runs yet.
    if (activeSection === "runs" && state.statusLoaded && state.runs.length === 0) {
      setActiveSection("home");
    }
  }, [activeSection, state.statusLoaded, state.runs.length]);

  useEffect(() => {
    if (!activeJob || !["starting", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => pollJob(activeJob), 2000);
    return () => window.clearInterval(timer);
  }, [activeJob?.job_id, activeJob?.status]);

  useEffect(() => {
    loadNemoMcpStatus();
    const intervalMs = settingsDraft.nemo_mcp_url?.trim() ? 6000 : 15000;
    const timer = window.setInterval(() => loadNemoMcpStatus(), intervalMs);
    return () => window.clearInterval(timer);
  }, [settingsDraft.nemo_mcp_url, selectedNemoTools.join("|")]);

  useEffect(() => {
    loadMissionStats();
    const timer = window.setInterval(() => loadMissionStats(), 8000);
    return () => window.clearInterval(timer);
  }, []);

  const shellActiveRun = selectedRun ?? state.approval_queue[0] ?? state.runs[0];
  const shellMemoryLabel = nemoState?.context_portfolio?.estimated_tokens ? `${nemoState.context_portfolio.estimated_tokens}t` : (nemoState?.health.status ?? "ready");
  const shellRuntimeLabel = shellActiveRun?.run_id ? `Run ${shellActiveRun.run_id.slice(0, 8)}` : "Standing by";
  const shellPhaseLabel = shellActiveRun?.execution_phase || shellActiveRun?.runtime_state || status;
  const shellNavItems: Array<{ section: AppSection; label: string; title: string; icon: React.ReactNode }> = [
    { section: "home", label: "Home", title: "Home", icon: <Home size={19} /> },
    { section: "runs", label: "Runs", title: "Runs", icon: <Files size={19} /> },
    { section: "versioning", label: "Git", title: "Versionado", icon: <GitBranch size={19} /> },
    { section: "terminal", label: "Term", title: "Terminal", icon: <TerminalSquare size={19} /> },
    { section: "browser", label: "Web", title: "Browser", icon: <Globe size={19} /> },
    { section: "extensions", label: "Tools", title: "Extensions", icon: <Puzzle size={19} /> },
    { section: "memory", label: "Memory", title: "Memoria", icon: <Database size={19} /> },
    { section: "settings", label: "Core", title: "Ajustes", icon: <Settings size={19} /> },
  ];

  return (
    <main className={`ide-shell section-${activeSection}`}>
      <aside className="activity-bar" aria-label="Primary navigation">
        <div className="activity-brand" aria-hidden="true"><Bot size={24} /></div>
        <nav className="activity-nav" aria-label="Primary sections">
          {shellNavItems.map((item) => (
            <button key={item.section} className={`activity ${activeSection === item.section ? "active" : ""}`} title={item.title} onClick={() => setActiveSection(item.section)}>
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="activity-system" aria-label="System status">
          <span>NEMO</span>
          <i />
          <small>{shellMemoryLabel}</small>
        </div>
      </aside>

      <aside className="explorer">
        <div className="brand-row">
          <Bot size={20} />
          <div>
            <h1>Space Code</h1>
            <p>Space Code engine + external NEMO memory</p>
          </div>
        </div>

        <RepoWorkspaceSwitcher
          repos={state.repos}
          repoDraft={repoDraft}
          onRepoDraftChange={setRepoDraft}
          onOpenRepo={openRepo}
          onBrowseFolder={browseRepoFolder}
          busy={repoBusy}
          error={repoError}
          activeRepo={state.repo_path}
        />

        <section className={`run-tree ${runTreeCollapsed ? "collapsed" : ""}`}>
          <button className={`section-heading section-toggle ${runTreeCollapsed ? "collapsed" : ""}`} onClick={() => setRunTreeCollapsed((value) => !value)} title={runTreeCollapsed ? "Expandir agent runs" : "Colapsar agent runs"}>
            <ChevronDown size={14} /> Agent Runs
          </button>
          <div className={`run-tree-body ${runTreeCollapsed ? "collapsed" : "expanded"}`}>
            {state.runs.length === 0 ? <EmptyState /> : <div className="run-tree-list">{state.runs.map((run) => {
              const isSelected = selectedRun?.source_json === run.source_json;
              const isExpanded = expandedRunSources[run.source_json] ?? isSelected;
              return (
                <article className={`tree-run-card ${isSelected ? "selected" : ""}`} key={run.source_json}>
                  <button className="tree-run-button" onClick={() => { selectRun(run); setActiveSection("runs"); }}>
                    <span className={`tree-run-dot ${statusTone(run.review_status)}`} />
                    <div className="tree-run-copy">
                      <strong>{run.objective}</strong>
                      <small>{describeRun(run)}</small>
                    </div>
                    <span className={`pill ${statusTone(run.review_status)}`}>{statusLabel(run.review_status)}</span>
                  </button>
                  <div className="tree-run-meta">
                    <span>Decision: {reviewDecisionLabel(run)}</span>
                    <span>Fase: {summarizePhase(run)}</span>
                    <span>{run.changed_files.length} archivo(s)</span>
                    <span>{run.risk_flags.length} riesgo(s)</span>
                    <button className={`tree-expand-toggle ${isExpanded ? "open" : ""}`} onClick={() => setExpandedRunSources((current) => ({ ...current, [run.source_json]: !isExpanded }))}>
                      <ChevronDown size={12} /> {isExpanded ? "Ocultar archivos" : "Abrir archivos"}
                    </button>
                  </div>
                  <div className={`file-tree ${isExpanded ? "expanded" : "collapsed"}`}>
                    <div className="file-tree-inner">
                      {run.changed_files.length === 0 ? <span className="tree-empty">No changed files</span> : run.changed_files.map((file) => (
                        <button className={activeFile === file ? "active" : ""} key={file} onClick={() => { setRunsWorkbenchTab("file"); setSelectedRunSource(run.source_json); setSelectedFile(file); setExpandedRunSources((current) => ({ ...current, [run.source_json]: true })); loadFilePreview(run, file); loadNemoState(run); loadSelfInsights(run); }}>
                          <FileCode2 size={14} />
                          <span>{file}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </article>
              );
            })}</div>}
          </div>
        </section>
      </aside>

      <section className="workbench">
        <header className="cockpit-topbar" aria-label="Mission control status">
          <div className="cockpit-title">
            <strong>Space Code</strong>
            <span>Autonomous coding with external NEMO memory</span>
          </div>
          <div className={`cockpit-live ${agentBusy ? "live" : ""}`}><i /> {agentBusy ? "Running" : shellActiveRun ? "Standby" : "Ready"}</div>
          <div className="cockpit-run-readout">
            <span>{shellRuntimeLabel}</span>
            <b>{shellPhaseLabel}</b>
          </div>
          <div className="cockpit-chain" aria-label="Handoff chain progress">
            {[0, 1, 2, 3, 4, 5].map((step) => <i key={step} className={step <= Math.min(5, state.approval_queue.length + readyRuns) ? "active" : ""} />)}
          </div>
          <button className="cockpit-steer" onClick={() => setActiveSection("memory")}><Database size={13} /> Memory {shellMemoryLabel}</button>
        </header>
        {composerOpen && <HandoffComposer draft={handoffDraft} onChange={setHandoffDraft} onSubmit={startHandoff} onClose={() => setComposerOpen(false)} running={handoffRunning} />}

        {activeSection === "home" && <MissionHome
          state={state}
          readyRuns={readyRuns}
          blockedRuns={blockedRuns}
          nemoState={nemoState}
          cognitiveStats={cognitiveStats}
          onRefreshCognitiveStats={() => loadCognitiveStats(selectedRun)}
          missionStats={missionStats}
          onRefreshMissionStats={loadMissionStats}
          status={status}
          draft={homeAgentDraft}
          provider={settingsDraft.provider}
          modelName={lmStudioOnline === false ? "sin conexión" : (lmStudioModel ?? settingsDraft.default_model)}
          messages={agentMessages}
          onDraftChange={setHomeAgentDraft}
          onSubmit={sendHomeAgentMessage}
          onStop={stopAgentMessage}
          onProviderChange={setProviderMode}
          onOpenComposer={() => setComposerOpen(true)}
          onOpenMemory={openMemorySection}
          running={agentBusy}
          queuedPrompt={queuedAgentPrompt}
          queuedPrompts={queuedAgentPrompts}
          onStartNewChat={startNewChat}
          onArchiveChat={archiveAgentChat}
          onClearChat={clearAgentChat}
          onSelectRun={(run) => { selectRun(run); setActiveSection("runs"); }}
          onRunAction={runAgentAction}
        />}

        {activeSection === "runs" && <>
          {!state.statusLoaded && <RunsSkeleton />}
          {state.statusLoaded && <>
          <div className="tab-row">
            <button type="button" className={`tab ${runsWorkbenchTab === "file" ? "active" : ""}`} onClick={() => setRunsWorkbenchTab("file")}>
              <FileCode2 size={14} /> {activeFile || "welcome.md"}
            </button>
            <button type="button" className={`tab ${runsWorkbenchTab === "review" ? "active" : ""}`} onClick={() => setRunsWorkbenchTab("review")}>
              <GitCompare size={14} /> Review
            </button>
            <button type="button" className={`tab ${runsWorkbenchTab === "agent" ? "active" : ""}`} onClick={() => setRunsWorkbenchTab("agent")}>
              <MessageSquareText size={14} /> Agent
            </button>
          </div>

          <div className="workspace-main" style={runsWorkbenchTab === "file" ? undefined : { gridTemplateColumns: "minmax(0, 1fr)" }}>
            {runsWorkbenchTab === "file" && <EditorPane
              run={selectedRun}
              filePreview={filePreview}
              activeFile={activeFile}
              decisions={filePreview ? hunkDecisions[filePreview.file_path] ?? {} : {}}
              onToggleHunk={toggleHunkDecision}
              onSetFileDecision={setFileDecision}
              onApplySelected={applySelectedDiff}
            />}
            {runsWorkbenchTab === "review" && (
              <div style={{ padding: "12px 16px" }}>
                <div className="review-seg-tabs">
                  <button
                    type="button"
                    className={`review-seg-tab ${reviewSubTab === "timeline" ? "active" : ""}`}
                    onClick={() => setReviewSubTab("timeline")}
                  >
                    ⚡ Timeline
                    {selectedJob?.status === "running" && (
                      <span className="review-live-badge">LIVE</span>
                    )}
                  </button>
                  <button
                    type="button"
                    className={`review-seg-tab ${reviewSubTab === "diff" ? "active" : ""}`}
                    onClick={() => setReviewSubTab("diff")}
                  >
                    ⎇ Diff / Merge
                  </button>
                </div>
                <div className="review-subtab-content">
                  {reviewSubTab === "timeline" && selectedJob && (
                    <TimelinePanel
                      jobId={selectedJob.job_id}
                      isLive={selectedJob.status === "running"}
                    />
                  )}
                  {reviewSubTab === "timeline" && !selectedJob && (
                    <div style={{ color: "#8b949e", fontSize: 12, padding: "16px 0" }}>
                      Select a run to view its timeline.
                    </div>
                  )}
                  {reviewSubTab === "diff" && (
                    <WorktreeDiffPanel
                      jobId={selectedJob?.job_id ?? ""}
                      onMerged={() => { /* state will refresh via polling */ }}
                      onRejected={() => { /* state will refresh via polling */ }}
                    />
                  )}
                </div>
              </div>
            )}
            {runsWorkbenchTab !== "review" && <AgentPane
              run={selectedRun}
              state={state}
              readyRuns={readyRuns}
              blockedRuns={blockedRuns}
              autonomyMode={autonomyMode}
              onAutonomyModeChange={setAutonomyMode}
              applyJson={selectedRun ? applyResults[selectedRun.source_json] : undefined}
              onReview={reviewRun}
              onApply={applyRun}
              onAutoApply={autoApplyRun}
              onRollback={rollbackRun}
              messages={agentMessages}
              draft={agentDraft}
              busy={agentBusy}
              queuedPrompt={queuedAgentPrompt}
              queuedPrompts={queuedAgentPrompts}
              onDraftChange={setAgentDraft}
              onSend={sendAgentMessage}
              onStop={stopAgentMessage}
              onRemoveQueued={removeQueuedAgentPrompt}
              onPrioritizeQueued={prioritizeQueuedAgentPrompt}
              onRunAction={runAgentAction}
              nemoState={nemoState}
              mcpWatcher={nemoMcpStatus}
              selfInsights={selfInsights}
              reviewPlan={reviewPlan}
              applyHistory={applyHistory}
              riskMap={riskMap}
              onRefreshRiskMap={loadRiskMap}
              onSendGuidedPrompt={sendGuidedAgentPrompt}
              onClearChat={clearAgentChat}
              onStartNewChat={startNewChat}
              onArchiveOldRuns={archiveOldRuns}
              onClearAllRuns={clearAllRuns}
              planObjective={planState.objective}
              currentPlan={planState.currentPlan}
              activeStepId={planState.activeStepId}
              planProgress={planState.planProgress}
              onOpenObjectiveModal={openObjectiveModal}
              onGeneratePlan={generatePlanPrompt}
              onSelectPlanStep={handlePlanStepSelect}
              permissionJob={jobAwaitingPermission}
              onGrantPermission={grantPermission}
              onDenyPermission={denyPermission}
              runningJob={jobRunning}
            />}
          </div>
          </>}
        </>}

        {activeSection === "versioning" && <VersioningPanel
          repoPath={state.repo_path}
          status={gitStatus}
          branches={gitBranches}
          remotes={gitRemotes}
          syncRemote={gitSyncRemote}
          syncBranch={gitSyncBranch}
          busy={gitBusy}
          diffPath={gitDiffPath}
          diffStaged={gitDiffStaged}
          diffText={gitDiffText}
          diffHunks={gitDiffHunks}
          commitMessage={gitCommitMessage}
          branchDraft={gitBranchDraft}
          onRefresh={() => {
            loadGitStatus();
            loadGitBranches();
            loadGitRemotes();
          }}
          onSyncRemoteChange={setGitSyncRemote}
          onSyncBranchChange={setGitSyncBranch}
          onDiffPathChange={setGitDiffPath}
          onLoadDiff={loadGitDiff}
          onStage={stageGitPath}
          onStageHunk={stageGitHunk}
          selectedHunks={gitSelectedHunks}
          onToggleHunkSelection={toggleGitHunkSelection}
          onApplySelectedHunks={applySelectedGitHunks}
          onCommitMessageChange={setGitCommitMessage}
          onCommit={commitGit}
          onBranchDraftChange={setGitBranchDraft}
          onCheckout={checkoutGitBranch}
          onSync={syncGit}
        />}

        {activeSection === "terminal" && <TerminalPanel
          command={terminalDraft}
          running={terminalRunning}
          result={terminalResult}
          onCommandChange={setTerminalDraft}
          onRun={runTerminal}
        />}

        {activeSection === "browser" && <BrowserPanel
          draft={browserDraft}
          state={browserState}
          queryDraft={browserQueryDraft}
          searchState={browserSearchState}
          searching={browserSearching}
          onDraftChange={setBrowserDraft}
          onOpen={openBrowserUrl}
          onQueryDraftChange={setBrowserQueryDraft}
          onSearch={searchBrowserWeb}
          onOpenSearchResult={openBrowserSearchResult}
        />}

        {activeSection === "extensions" && <ExtensionsPanel
          items={extensions}
          busy={extensionsBusy}
          onToggle={toggleExtension}
          onReload={loadExtensions}
        />}

        {activeSection === "memory" && <div className="section-surface"><NemoMemoryPanel nemoState={nemoState} mcpWatcher={nemoMcpStatus} /></div>}

        {activeSection === "settings" && <div className="section-surface">
          <RepoSettingsPanel
            state={state}
            settings={settingsDraft}
            onSettingsChange={setSettingsDraft}
            onSaveSettings={saveSettings}
            repoDraft={repoDraft}
            onRepoDraftChange={setRepoDraft}
            onOpenRepo={openRepo}
            cloneDraft={cloneDraft}
            onCloneDraftChange={setCloneDraft}
            onCloneRepo={cloneRepo}
            cleanupResult={cleanupResult}
            onCleanup={cleanupArtifacts}
            orphanCleanupResult={orphanCleanupResult}
            onCleanupOrphans={cleanupOrphanJobs}
            mcpWatcher={nemoMcpStatus}
            onRefreshMcpWatcher={loadNemoMcpStatus}
            selectedNemoTools={selectedNemoTools}
            onToggleNemoTool={(toolName) => {
              setSelectedNemoTools((current) => {
                if (current.includes(toolName)) {
                  const next = current.filter((item) => item !== toolName);
                  return next.length > 0 ? next : current;
                }
                return [...current, toolName];
              });
            }}
          />
        </div>}

        <ObjectiveDefinition
          isOpen={objectiveModalOpen}
          onClose={() => setObjectiveModalOpen(false)}
          onCreate={handleCreateObjective}
        />

        <BottomPanel
          run={selectedRun}
          status={status}
          job={activeJob}
          onControl={controlJob}
          terminalCommand={terminalDraft}
          terminalRunning={terminalRunning}
          terminalResult={terminalResult}
          onTerminalCommandChange={setTerminalDraft}
          onTerminalRun={runTerminal}
        />
      </section>
    </main>
  );
}

function normalizeHomeLayoutMode(value: unknown): HomeLayoutMode | undefined {
  const normalized = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  if (["full-cockpit", "focus-artifact", "focus-chat", "focus-telemetry"].includes(normalized)) return normalized as HomeLayoutMode;
  return undefined;
}

function normalizeHomePanel(value: unknown): HomePanelKey | undefined {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "timeline" || normalized === "chat" || normalized === "conversation") return "timeline";
  if (normalized === "artifact" || normalized === "canvas" || normalized === "stage") return "artifact";
  if (normalized === "telemetry" || normalized === "memory" || normalized === "right") return "telemetry";
  return undefined;
}

function normalizeHomePanels(value: unknown): HomePanelKey[] {
  const values = Array.isArray(value) ? value : typeof value === "string" ? value.split(/[ ,|]+/) : [];
  return Array.from(new Set(values.map(normalizeHomePanel).filter(Boolean) as HomePanelKey[]));
}

function homeLayoutCommandFromPayload(payload: Record<string, unknown>): HomeLayoutCommand | null {
  const mode = normalizeHomeLayoutMode(payload.layout_mode ?? payload.mode ?? payload.view);
  const collapse = normalizeHomePanels(payload.collapse ?? payload.collapsed_panels ?? payload.hide);
  const expand = normalizeHomePanels(payload.expand ?? payload.expanded_panels ?? payload.show);
  if (!mode && collapse.length === 0 && expand.length === 0) return null;
  return { mode, collapse, expand };
}

function latestHomeLayoutCommand(messages: AgentMessage[]): { command: HomeLayoutCommand; signature: string } | null {
  for (const message of [...messages].reverse()) {
    if (message.role !== "assistant") continue;
    const action = [...(message.actions ?? [])].reverse().find((candidate) => candidate.kind === "layout" || (candidate.kind === "pc_control" && String(candidate.payload.mode || "").toLowerCase() === "layout"));
    const command = action ? homeLayoutCommandFromPayload(action.payload) : null;
    if (command) return { command, signature: `${message.id}:${action?.id}:${JSON.stringify(command)}` };
  }
  return null;
}

function MissionHome({ state, readyRuns, blockedRuns, nemoState, cognitiveStats, onRefreshCognitiveStats, missionStats, onRefreshMissionStats, status, draft, provider, modelName, messages, onDraftChange, onSubmit, onStop, onProviderChange, onOpenComposer, onOpenMemory, running, queuedPrompt, queuedPrompts, onStartNewChat, onArchiveChat, onClearChat, onSelectRun, onRunAction }: { state: MissionState; readyRuns: number; blockedRuns: number; nemoState: NemoState | null; cognitiveStats: CognitiveStatsState | null; onRefreshCognitiveStats: () => void; missionStats: MissionStatsState | null; onRefreshMissionStats: () => void; status: string; draft: string; provider: string; modelName: string; messages: AgentMessage[]; onDraftChange: (objective: string) => void; onSubmit: (mode?: "send" | "queue" | "steer" | "plan") => void; onStop: () => void; onProviderChange: (provider: string) => void; onOpenComposer: () => void; onOpenMemory: () => void; running: boolean; queuedPrompt: string | null; queuedPrompts: string[]; onStartNewChat: () => void; onArchiveChat: () => void; onClearChat: () => void; onSelectRun: (run: MissionRun) => void; onRunAction?: (action: AgentAction) => void }) {
  const [layoutMode, setLayoutMode] = useState<HomeLayoutMode>("full-cockpit");
  const [collapsedPanels, setCollapsedPanels] = useState<HomePanelState>({ timeline: false, artifact: false, telemetry: false });
  const [layoutSource, setLayoutSource] = useState<string>("manual");
  const appliedLayoutCommandRef = useRef<string>("");
  const blockedReviewRuns = state.runs.filter((run) => run.review_status === "blocked");
  const { artifacts, activeArtifactId, setActiveArtifactId, attachArtifactToDraft, removeArtifact, toggleFavorite } = useGeneratedArtifacts({ messages, draft, onDraftChange });
  const prevArtifactsLenRef = useRef(artifacts.length);
  useEffect(() => {
    if (artifacts.length > prevArtifactsLenRef.current) {
      setCollapsedPanels((prev) => ({ ...prev, artifact: false }));
    }
    prevArtifactsLenRef.current = artifacts.length;
  }, [artifacts.length]);
  const atomCount = nemoState?.health.atom_count ?? 0;
  const evidenceCount = nemoState?.health.evidence_count ?? 0;
  const feedbackCount = nemoState?.health.feedback_count ?? 0;
  const contextLabel = nemoState?.context_portfolio?.estimated_tokens ? `${nemoState.context_portfolio.estimated_tokens}t` : "ready";
  const statsJobTotal = missionStats?.jobs?.total ?? 0;
  const totalRuns = Math.max(state.runs.length, statsJobTotal);
  const queueCount = state.approval_queue.length;
  const memoryKpis = cognitiveStats?.memory_kpis;
  const sourceStats = missionStats?.sources;
  const sourceReads = sourceStats?.total_reads ?? 0;
  const sourceCacheHitRate = sourceStats && sourceStats.total_reads > 0 ? Math.round((sourceStats.cache_hit_rate ?? 0) * 100) : 0;
  const providerLabel = modelName.trim() ? `LM Studio: ${modelName}` : "LM Studio real";
  const canSendDraft = draft.trim().length > 0;
  const activeRun = blockedReviewRuns[0] ?? state.approval_queue[0] ?? state.runs[0];
  const orbitNodes = buildNemoOrbitNodes(nemoState, memoryKpis?.atom_count ?? atomCount, evidenceCount, feedbackCount, contextLabel);
  const orbitEdges = buildNemoOrbitEdges(orbitNodes);
  const panelLabels: Record<HomePanelKey, string> = { timeline: "Timeline", artifact: "Artifact", telemetry: "Telemetry" };
  const shouldShowFocusDock = layoutMode === "focus-artifact" && collapsedPanels.timeline;
  const gridClassName = [
    "mission-v2-grid",
    `mode-${layoutMode}`,
    shouldShowFocusDock ? "has-focus-dock" : "",
    collapsedPanels.timeline ? "collapse-timeline" : "",
    collapsedPanels.artifact ? "collapse-artifact" : "",
    collapsedPanels.telemetry ? "collapse-telemetry" : "",
  ].filter(Boolean).join(" ");

  const applyLayoutCommand = (command: HomeLayoutCommand, source: string) => {
    if (command.mode) setLayoutMode(command.mode);
    setCollapsedPanels((current) => {
      const next = { ...current };
      for (const panel of command.collapse ?? []) next[panel] = true;
      for (const panel of command.expand ?? []) next[panel] = false;
      if (command.mode === "full-cockpit") {
        next.timeline = false;
        next.artifact = false;
        next.telemetry = false;
      }
      if (command.mode === "focus-artifact") {
        next.timeline = true;
        next.artifact = false;
        next.telemetry = true;
      }
      if (command.mode === "focus-chat") {
        next.timeline = false;
        next.artifact = true;
        next.telemetry = true;
      }
      if (command.mode === "focus-telemetry") {
        next.timeline = true;
        next.artifact = true;
        next.telemetry = false;
      }
      return next;
    });
    setLayoutSource(source);
  };

  const togglePanel = (panel: HomePanelKey) => {
    setCollapsedPanels((current) => ({ ...current, [panel]: !current[panel] }));
    setLayoutMode("full-cockpit");
    setLayoutSource("manual");
  };

  useEffect(() => {
    const nextCommand = latestHomeLayoutCommand(messages);
    if (!nextCommand || nextCommand.signature === appliedLayoutCommandRef.current) return;
    appliedLayoutCommandRef.current = nextCommand.signature;
    applyLayoutCommand(nextCommand.command, "llm");
  }, [messages]);

  const commandDockElement = <CommandDock
    draft={draft}
    provider={provider}
    providerLabel={providerLabel}
    running={running}
    queuedPrompt={queuedPrompt}
    queuedPrompts={queuedPrompts}
    onDraftChange={onDraftChange}
    onSubmit={onSubmit}
    onStop={onStop}
    onProviderChange={onProviderChange}
    onOpenComposer={onOpenComposer}
    onOpenMemory={onOpenMemory}
  />;

  return (
    <section className="mission-home mission-home-v2">
      <div className="mission-v2-header">
        <div className="mission-brand-mark"><Bot size={18} /><span /></div>
        <div>
          <strong>Space Code</strong>
          <small>Autonomous coding system</small>
        </div>
        <div className={`mission-v2-status ${running ? "live" : ""}`}><i /> {running ? status.slice(0, 40) || "Running" : "Ready"}</div>
        <div className="mission-v2-ops" aria-label="Estado operacional compacto">
          <span><b>NEMO</b>{contextLabel}</span>
          <span><b>Artifacts</b>{artifacts.length}</span>
          <span><b>Queue</b>{queueCount}</span>
        </div>
        <div className="mission-layout-controls" aria-label="Mission layout controls" data-source={layoutSource}>
          <button className={layoutMode === "full-cockpit" ? "active" : ""} onClick={() => applyLayoutCommand({ mode: "full-cockpit" }, "manual")} title="Full cockpit"><PanelBottom size={12} /><span>All</span></button>
          <button className={layoutMode === "focus-artifact" ? "active" : ""} onClick={() => applyLayoutCommand({ mode: "focus-artifact" }, "manual")} title="Focus artifact"><Puzzle size={12} /><span>Stage</span></button>
          <button className={layoutMode === "focus-chat" ? "active" : ""} onClick={() => applyLayoutCommand({ mode: "focus-chat" }, "manual")} title="Focus conversation"><MessageSquareText size={12} /><span>Chat</span></button>
        </div>
        <div className="mission-session-actions" aria-label="Controles de sesión">
          <button onClick={onStartNewChat} title="Nuevo chat"><MessageSquarePlus size={13} /></button>
          <button onClick={onArchiveChat} title="Archivar chat"><Archive size={13} /></button>
          <button className="danger" onClick={onClearChat} title="Eliminar chat"><Trash2 size={13} /></button>
        </div>
        <button className="mission-v2-steer" onClick={() => onSubmit("steer")} disabled={!canSendDraft}><Wrench size={13} /> Steer</button>
      </div>
      <div className={gridClassName} data-layout-source={layoutSource} data-layout-mode={layoutMode}>
        <div className={`mission-panel-slot timeline ${collapsedPanels.timeline ? "collapsed" : "expanded"}`} data-panel="timeline">
          {collapsedPanels.timeline ? <button className="mission-panel-restore" onClick={() => togglePanel("timeline")} title="Expand timeline"><MessageSquareText size={16} /><span>{panelLabels.timeline}</span></button> : <>
            <button className="mission-panel-collapse" onClick={() => togglePanel("timeline")} title="Collapse timeline" aria-label="Collapse timeline"><MessageSquareText size={13} /></button>
            <MissionTimeline
              messages={messages}
              running={running}
              queuedPrompt={queuedPrompt}
              cleanAssistantContent={(content) => parseAgentMessageDecorations(content).cleanedContent}
              renderRichText={(content) => <MessageRichText content={content} animate={false} compact />}
              renderMcpEvidence={(tools) => <HomeMcpEvidence tools={tools as AgentToolCall[]} />}
              liveStatus={<AgentLiveStatus busy={running} queuedPrompt={queuedPrompt} messages={messages} compact />}
              commandDock={commandDockElement}
              onRunAction={onRunAction ? (action) => onRunAction(action as AgentAction) : undefined}
            />
          </>}
        </div>

        <div className={`mission-panel-slot artifact ${collapsedPanels.artifact ? "collapsed" : "expanded"}`} data-panel="artifact">
          {collapsedPanels.artifact ? <button className="mission-panel-restore" onClick={() => togglePanel("artifact")} title="Expand artifact studio"><Puzzle size={16} /><span>{panelLabels.artifact}</span></button> : <>
            <button className="mission-panel-collapse" onClick={() => togglePanel("artifact")} title="Collapse artifact studio" aria-label="Collapse artifact studio"><Puzzle size={13} /></button>
            <ArtifactWorkbench
              artifacts={artifacts}
              activeId={activeArtifactId}
              onSelect={setActiveArtifactId}
              onAttachToPrompt={attachArtifactToDraft}
              onRemoveArtifact={removeArtifact}
              onToggleFavorite={toggleFavorite}
              renderMarkdown={(content) => <MessageRichText content={content} animate={false} compact />}
            />
          </>}
        </div>

        <div className={`mission-panel-slot telemetry ${collapsedPanels.telemetry ? "collapsed" : "expanded"}`} data-panel="telemetry">
          {collapsedPanels.telemetry ? <button className="mission-panel-restore" onClick={() => togglePanel("telemetry")} title="Expand telemetry"><Database size={16} /><span>{panelLabels.telemetry}</span></button> : <>
            <button className="mission-panel-collapse" onClick={() => togglePanel("telemetry")} title="Collapse telemetry" aria-label="Collapse telemetry"><Database size={13} /></button>
            <div className="mission-right-rail">
              <NemoMemoryOrbitCopy nodes={orbitNodes} edges={orbitEdges} status={nemoState?.health.status ?? "snapshot"} onSelectNode={onOpenMemory} />
              <TelemetryColumn
                activeRun={activeRun}
                visibleQueue={state.approval_queue}
                totalRuns={totalRuns}
                readyRuns={readyRuns}
                blockedRuns={blockedRuns}
                queueCount={queueCount}
                running={running}
                contextLabel={contextLabel}
                memoryAtomCount={memoryKpis?.atom_count ?? atomCount}
                evidenceCount={evidenceCount}
                feedbackCount={feedbackCount}
                sourceReads={sourceReads}
                sourceCacheHitRate={sourceCacheHitRate}
                status={status}
                onSelectRun={onSelectRun}
                onOpenMemory={onOpenMemory}
                onRefreshCognitiveStats={onRefreshCognitiveStats}
                onRefreshMissionStats={onRefreshMissionStats}
              />
            </div>
          </>}
        </div>
        {shouldShowFocusDock && <div className="mission-focus-dock" aria-label="Focus artifact command dock">{commandDockElement}</div>}
      </div>
    </section>
  );
}

function StatusGauge({ label, value, percent, tone }: { label: string; value: string; percent: number; tone: "blue" | "green" | "violet" }) {
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
  const fieldIds = {
    objective: "handoff-objective",
    timeoutSeconds: "handoff-timeout-seconds",
    validationPolicy: "handoff-validation-policy",
    acceptance: "handoff-acceptance",
    validation: "handoff-validation",
    targetFiles: "handoff-target-files",
  };
  return (
    <section className="handoff-composer" aria-labelledby="handoff-composer-title">
      <div className="composer-header">
        <div>
          <strong id="handoff-composer-title">New Full Handoff</strong>
          <span>Runs in an isolated runtime and returns to review.</span>
        </div>
        <button onClick={onClose} disabled={running}>Close</button>
      </div>
      <label className="composer-objective" htmlFor={fieldIds.objective}>
        Handoff objective
        <textarea
          id={fieldIds.objective}
          value={draft.objective}
          onChange={(event) => update("objective", event.target.value)}
          placeholder="Describe the PRD or implementation objective..."
        />
      </label>
      <div className="composer-grid">
        <div className="composer-static-field">
          <span>Provider</span>
          <code>Space Code + LM Studio</code>
        </div>
        <label htmlFor={fieldIds.timeoutSeconds}>
          Timeout seconds
          <input id={fieldIds.timeoutSeconds} value={draft.timeoutSeconds} onChange={(event) => update("timeoutSeconds", event.target.value)} disabled={running} />
        </label>
        <label htmlFor={fieldIds.validationPolicy}>
          Validation policy
          <select id={fieldIds.validationPolicy} value={draft.validationPolicy} onChange={(event) => update("validationPolicy", event.target.value)} disabled={running}>
            <option value="none">none</option>
            <option value="smoke">smoke</option>
            <option value="targeted">targeted</option>
            <option value="full">full</option>
          </select>
        </label>
        <label htmlFor={fieldIds.acceptance}>
          Acceptance
          <textarea id={fieldIds.acceptance} value={draft.acceptance} onChange={(event) => update("acceptance", event.target.value)} />
        </label>
        <label htmlFor={fieldIds.validation}>
          Validation
          <textarea id={fieldIds.validation} value={draft.validation} onChange={(event) => update("validation", event.target.value)} />
        </label>
        <label htmlFor={fieldIds.targetFiles}>
          Target files
          <textarea id={fieldIds.targetFiles} value={draft.targetFiles} onChange={(event) => update("targetFiles", event.target.value)} placeholder="optional, one per line" />
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
  autonomyMode: AutonomyMode;
  onAutonomyModeChange: (mode: AutonomyMode) => void;
  applyJson?: string;
  onReview: (run: MissionRun) => void;
  onApply: (run: MissionRun) => void;
  onAutoApply: (run: MissionRun) => void;
  onRollback: (run: MissionRun) => void;
  messages: AgentMessage[];
  draft: string;
  busy: boolean;
  queuedPrompt: string | null;
  queuedPrompts: string[];
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onRemoveQueued: (index: number) => void;
  onPrioritizeQueued: (index: number) => void;
  onRunAction: (action: AgentAction) => void;
  nemoState: NemoState | null;
  mcpWatcher: NemoMcpWatcherState | null;
  selfInsights: SelfModInsights | null;
  reviewPlan: ReviewPlan | null;
  applyHistory: ApplyHistoryItem[];
  riskMap: RiskMapState | null;
  onRefreshRiskMap: () => void;
  onSendGuidedPrompt: (prompt: string) => void;
  onClearChat: () => void;
  onStartNewChat: () => void;
  onArchiveOldRuns: () => void;
  onClearAllRuns: () => void;
  planObjective: ObjectiveState | null;
  currentPlan: ExecutionPlan | null;
  activeStepId: string | null;
  planProgress: number;
  onOpenObjectiveModal: () => void;
  onGeneratePlan: () => void;
  onSelectPlanStep: (stepId: string) => void;
  permissionJob?: HandoffJob | null;
  onGrantPermission?: (jobId: string, note: string) => void;
  onDenyPermission?: (jobId: string, note: string) => void;
  runningJob?: HandoffJob | null;
};

type FlowStep = {
  id: string;
  title: string;
  detail: string;
  status: "done" | "active" | "pending";
};

function buildAutonomyFlowSteps(run: MissionRun | undefined): FlowStep[] {
  if (!run) {
    return [
      { id: "intent", title: "Intencion", detail: "Esperando objetivo", status: "active" },
      { id: "plan", title: "Plan", detail: "Aun no hay run seleccionada", status: "pending" },
      { id: "execute", title: "Ejecucion", detail: "Sin cambios en curso", status: "pending" },
      { id: "gate", title: "Gate", detail: "Sin evaluacion de riesgo", status: "pending" },
      { id: "apply", title: "Apply", detail: "Sin salida aplicada", status: "pending" },
    ];
  }

  const hasTimeline = run.timeline.length > 0;
  const hasChanges = run.changed_files.length > 0;
  const isApplied = run.review_status === "applied";
  const hasRisk = run.risk_flags.length > 0;
  const gateReady = run.mergeable && !hasRisk;

  const statuses: Array<FlowStep["status"]> = [
    hasTimeline ? "done" : "active",
    hasTimeline ? "done" : "pending",
    hasChanges ? "done" : "pending",
    gateReady ? "done" : hasChanges ? "active" : "pending",
    isApplied ? "done" : gateReady ? "active" : "pending",
  ];

  const firstActive = statuses.indexOf("active");
  if (firstActive === -1 && !isApplied) {
    const firstPending = statuses.indexOf("pending");
    if (firstPending >= 0) statuses[firstPending] = "active";
  }

  return [
    { id: "intent", title: "Intencion", detail: run.objective || "Objetivo no detectado", status: statuses[0] },
    { id: "plan", title: "Plan", detail: `${run.timeline.length} evento(s) en timeline`, status: statuses[1] },
    { id: "execute", title: "Ejecucion", detail: `${run.changed_files.length} archivo(s) tocado(s)`, status: statuses[2] },
    { id: "gate", title: "Gate", detail: hasRisk ? `${run.risk_flags.length} riesgo(s) activo(s)` : "Sin riesgos criticos", status: statuses[3] },
    { id: "apply", title: "Apply", detail: statusLabel(run.review_status), status: statuses[4] },
  ];
}

function autonomyModeLabel(mode: AutonomyMode): string {
  if (mode === "manual") return "Manual";
  if (mode === "trusted") return "Supervisado";
  return "Automatico";
}

function autonomyModeHelp(mode: AutonomyMode): string {
  if (mode === "manual") return "El agente propone; tu confirmas cada paso sensible.";
  if (mode === "trusted") return "El agente ejecuta y te pide confirmacion solo en gates de riesgo.";
  return "El agente avanza automaticamente y solo se frena en bloqueos criticos.";
}

function AutopilotFlowPanel({
  run,
  mode,
  onModeChange,
  busy,
  onReview,
  onApply,
  onAutoApply,
  onSendGuidedPrompt,
}: {
  run: MissionRun | undefined;
  mode: AutonomyMode;
  onModeChange: (mode: AutonomyMode) => void;
  busy: boolean;
  onReview: (run: MissionRun) => void;
  onApply: (run: MissionRun) => void;
  onAutoApply: (run: MissionRun) => void;
  onSendGuidedPrompt: (prompt: string) => void;
}) {
  const steps = buildAutonomyFlowSteps(run);
  const completedCount = steps.filter((step) => step.status === "done").length;
  const progressPercent = run ? Math.round((completedCount / steps.length) * 100) : 0;
  const riskCount = run?.risk_flags.length ?? 0;
  const confidence: "green" | "yellow" | "red" = !run
    ? "yellow"
    : run.mergeable && riskCount === 0
      ? "green"
      : run.mergeable
        ? "yellow"
        : "red";

  const confidenceLabel = confidence === "green"
    ? "Listo para aplicar"
    : confidence === "yellow"
      ? "Requiere revision rapida"
      : "Requiere intervencion";

  return (
    <section className="autopilot-panel">
      <div className="panel-title"><Bot size={16} /> Piloto Automatico</div>
      <div className="autonomy-mode-row" role="radiogroup" aria-label="Modo de autonomia">
        {(["manual", "trusted", "aggressive"] as AutonomyMode[]).map((entry) => (
          <button
            key={entry}
            role="radio"
            aria-checked={mode === entry}
            className={mode === entry ? "active" : ""}
            onClick={() => onModeChange(entry)}
          >
            {autonomyModeLabel(entry)}
          </button>
        ))}
      </div>
      <p className="autonomy-help">{autonomyModeHelp(mode)}</p>

      <div className="flow-progress-track" aria-label="Progreso del flujo">
        <b style={{ width: `${progressPercent}%` }} />
      </div>
      <span className="autonomy-progress-meta">{progressPercent}% completado</span>

      <div className="flow-steps">
        {steps.map((step) => (
          <button key={step.id} className={`flow-step ${step.status}`} onClick={() => onSendGuidedPrompt(`Ayudame con la etapa ${step.title.toLowerCase()} para este run.`)}>
            <i>{step.status === "done" ? <CheckCircle2 size={13} /> : step.status === "active" ? <Play size={13} /> : <Circle size={13} />}</i>
            <div>
              <strong>{step.title}</strong>
              <span>{step.detail}</span>
            </div>
          </button>
        ))}
      </div>

      <div className={`confidence-gate ${confidence}`}>
        <strong>{confidenceLabel}</strong>
        <span>{run ? `${riskCount} riesgo(s) / mergeable ${run.mergeable ? "si" : "no"}` : "Selecciona un run para evaluar confianza"}</span>
        <div className="review-actions">
          <button onClick={() => run && onReview(run)} disabled={!run || busy}>Revisar</button>
          <button onClick={() => run && onApply(run)} disabled={!run || busy || !run.mergeable}>Apply manual</button>
          <button onClick={() => run && onAutoApply(run)} disabled={!run || busy || confidence === "red" || mode === "manual"}>Apply auto</button>
        </div>
      </div>
    </section>
  );
}

function InsightSection({
  title,
  summary,
  open,
  children,
}: {
  title: string;
  summary: string;
  open: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="ops-panel" open={open}>
      <summary style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <strong>{title}</strong>
        <span className="muted">{summary}</span>
      </summary>
      <div style={{ marginTop: 10 }}>
        {children}
      </div>
    </details>
  );
}

function AgentPane({ run, state, readyRuns, blockedRuns, autonomyMode, onAutonomyModeChange, applyJson, onReview, onApply, onAutoApply, onRollback, messages, draft, busy, queuedPrompt, queuedPrompts, onDraftChange, onSend, onStop, onRemoveQueued, onPrioritizeQueued, onRunAction, nemoState, mcpWatcher, selfInsights, reviewPlan, applyHistory, riskMap, onRefreshRiskMap, onSendGuidedPrompt, onClearChat, onStartNewChat, onArchiveOldRuns, onClearAllRuns, planObjective, currentPlan, activeStepId, planProgress, onOpenObjectiveModal, onGeneratePlan, onSelectPlanStep, permissionJob, onGrantPermission, onDenyPermission, runningJob }: AgentPaneProps) {
  const [tab, setTab] = useState<"chat" | "run" | "insights">("chat");
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const chatMenuRef = React.useRef<HTMLDivElement>(null);
  const riskCount = run?.risk_flags.length ?? 0;

  useEffect(() => {
    if (!chatMenuOpen) return;
    const handle = (e: MouseEvent) => {
      if (chatMenuRef.current && !chatMenuRef.current.contains(e.target as Node)) {
        setChatMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [chatMenuOpen]);

  return (
    <aside className="agent-pane">

      {/* ── HEADER (always visible) ── */}
      <div className="agent-pane-header">
        {permissionJob && permissionJob.permission_request && onGrantPermission && onDenyPermission && (
          <PermissionRequestPanel
            jobId={permissionJob.job_id}
            objective={String(permissionJob.objective || permissionJob.permission_request.rationale || "")}
            permissionRequest={permissionJob.permission_request as { job_id: string; categories: string[]; rationale: string; auto_approved: string[]; requires_user_approval: string[] }}
            onGranted={() => onGrantPermission(permissionJob.job_id, "")}
            onDenied={() => onDenyPermission(permissionJob.job_id, "")}
          />
        )}
        {runningJob && !permissionJob && (
          <Tooltip text="Job en ejecución — click para ver el timeline en la tab Run">
            <div className="agent-live-badge" onClick={() => setTab("run")} role="button" tabIndex={0}>
              <span className="agent-live-badge-dot" aria-hidden="true" /> LIVE — {runningJob.job_id.slice(-8)}
            </div>
          </Tooltip>
        )}
        <div className="agent-metrics-strip">
          <Tooltip text="Total de runs en el workspace actual">
            <span className="agent-metric"><span>{state.runs.length}</span> Runs</span>
          </Tooltip>
          <Tooltip text="Runs listos para aplicar (sin risk flags bloqueantes)">
            <span className="agent-metric agent-metric-ready"><span>{readyRuns}</span> Ready</span>
          </Tooltip>
          <Tooltip text="Runs con risk flags que requieren revisión manual antes de aplicar">
            <span className="agent-metric agent-metric-blocked"><span>{blockedRuns}</span> Blocked</span>
          </Tooltip>
          <Tooltip text="Memoria NEMO: activa cuando nemo_required está habilitado en Settings">
            <span className="agent-metric"><span>{state.settings.nemo_required ? "on" : "off"}</span> NEMO</span>
          </Tooltip>
        </div>
        <div className="agent-tab-bar" role="tablist">
          <Tooltip text="Chat con el agente — envía instrucciones y recibe respuestas">
            <button role="tab" className={`agent-tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>Chat</button>
          </Tooltip>
          <Tooltip text="Controla el run seleccionado: revisa el diff, aplica o deshace cambios">
            <button role="tab" className={`agent-tab ${tab === "run" ? "active" : ""}`} onClick={() => setTab("run")}>Run</button>
          </Tooltip>
          <Tooltip text="Análisis: Self-Improvement, Risk Map, NEMO Memory, Apply Plan">
            <button role="tab" className={`agent-tab ${tab === "insights" ? "active" : ""}`} onClick={() => setTab("insights")}>Insights</button>
          </Tooltip>
        </div>
      </div>

      {/* ── CHAT TAB ── */}
      {tab === "chat" && (
        <>
          <div className="agent-chat-body">
            <AgentLiveStatus busy={busy} queuedPrompt={queuedPrompt} messages={messages} />
            {planObjective && (
              <div className="agent-card" style={{ marginBottom: 4 }}>
                <span>Objetivo activo</span>
                <strong>{planObjective.title}</strong>
                <p>{planObjective.description || "Sin descripcion"}</p>
              </div>
            )}
            {currentPlan && (
              <PlanProgress
                objective={planObjective}
                currentPlan={currentPlan}
                activeStepId={activeStepId}
                planProgress={planProgress}
                onStepClick={onSelectPlanStep}
              />
            )}
            <div className="chat-thread">
              {messages.map((message) => (
                <AgentChatMessage message={message} onRunAction={onRunAction} key={message.id} />
              ))}
            </div>
          </div>

          <div className="agent-chat-footer">
            <div className="chat-steering">
              <Tooltip text="Continua desde el ultimo paso y explicame el avance en 3 bullets.">
                <button onClick={() => onSendGuidedPrompt("Continua desde el ultimo paso y explicame el avance en 3 bullets.")}>Continuar</button>
              </Tooltip>
              <Tooltip text="Activa modo plan: genera o refina pasos concretos en formato [STEP N: titulo -> resultado esperado].">
                <button onClick={() => onSendGuidedPrompt("Activa modo plan: genera o refina pasos concretos en formato [STEP N: titulo -> resultado esperado].")}>Plan</button>
              </Tooltip>
              <Tooltip text="Reformula la respuesta con opciones accionables y pasos concretos.">
                <button onClick={() => onSendGuidedPrompt("Reformula la respuesta con opciones accionables y pasos concretos.")}>Reformular</button>
              </Tooltip>
              <Tooltip text="Detener la respuesta del agente en curso">
                <button className="stop" onClick={onStop} disabled={!busy}><Square size={13} /> Detener</button>
              </Tooltip>
            </div>
            <div className="chat-composer">
              <textarea
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onSend(); }}
                placeholder="Pide al agente continuar, corregir o aplicar cambios..."
              />
              <div style={{ display: "flex", gap: 4 }}>
                <Tooltip text="Enviar mensaje (también Ctrl+Enter)">
                  <button onClick={onSend} disabled={!draft.trim()} title="Enviar"><Send size={15} /></button>
                </Tooltip>
                <div className="chat-menu-wrap" ref={chatMenuRef}>
                  <Tooltip text="Más acciones: nuevo chat, limpiar, objetivo, plan">
                    <button className="chat-menu-toggle" onClick={() => setChatMenuOpen((o) => !o)} aria-label="Más opciones">···</button>
                  </Tooltip>
                  {chatMenuOpen && (
                    <div className="chat-menu-dropdown">
                      <button onClick={() => { onStartNewChat(); setChatMenuOpen(false); }}>💬 Nuevo chat</button>
                      <button onClick={() => { onClearChat(); setChatMenuOpen(false); }}>🗑️ Limpiar chat</button>
                      <hr />
                      <button onClick={() => { onOpenObjectiveModal(); setChatMenuOpen(false); }}>🎯 Definir objetivo</button>
                      <button onClick={() => { onGeneratePlan(); setChatMenuOpen(false); }} disabled={!planObjective}>🧭 Generar plan</button>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {(busy || queuedPrompt) && (
              <div className="chat-queue-status">
                <span>{busy ? "El agente esta respondiendo..." : ""}</span>
                {queuedPrompt && <strong>Siguiente: {queuedPrompt}</strong>}
                {queuedPrompts.length > 1 && <span>{queuedPrompts.length - 1} mensaje(s) adicionales en cola</span>}
                {queuedPrompts.length > 0 && (
                  <div className="queued-list">
                    {queuedPrompts.map((item, index) => (
                      <div className="queued-item" key={`${item}-${index}`}>
                        <span>{index + 1}. {item}</span>
                        <div>
                          <button onClick={() => onPrioritizeQueued(index)} disabled={index === 0}>Priorizar</button>
                          <button onClick={() => onRemoveQueued(index)}>Quitar</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── RUN TAB ── */}
      {tab === "run" && (
        <div className="agent-run-body">
          {runningJob && (
            <TimelinePanel jobId={runningJob.job_id} isLive={true} />
          )}
          {!run && <EmptyState />}
          {run && (
            <>
              <div className="agent-card">
                <span>Run seleccionado</span>
                <strong>{run.task_id} / {run.run_id}</strong>
                <p>{run.source_json}</p>
              </div>
              <div className="agent-card">
                <span>Estado</span>
                <strong>{statusLabel(run.review_status)}</strong>
                <p>
                  {run.mergeable ? "Listo para aplicar" : "Requiere revisión"} · {riskCount} riesgo(s) · {run.changed_files.length} archivo(s)
                </p>
                {run.risk_flags.length > 0 && (
                  <div className="risk-box">{run.risk_flags.map((r) => <span key={r}>{r}</span>)}</div>
                )}
              </div>
              <AutopilotFlowPanel
                run={run}
                mode={autonomyMode}
                onModeChange={onAutonomyModeChange}
                busy={busy}
                onReview={onReview}
                onApply={onApply}
                onAutoApply={onAutoApply}
                onSendGuidedPrompt={onSendGuidedPrompt}
              />
              <div className="run-action-row">
                <Tooltip text="Abre el diff completo para revisar los cambios antes de aplicar">
                  <button onClick={() => onReview(run)}><GitPullRequest size={16} /> Revisar</button>
                </Tooltip>
                <Tooltip text="Escribe los cambios al repo. Solo disponible si el run es mergeable">
                  <button disabled={!run.mergeable} onClick={() => onApply(run)}><CheckCircle2 size={16} /> Aplicar</button>
                </Tooltip>
                <Tooltip text="Aplica directamente si pasa controles automáticos, sin revisión manual">
                  <button disabled={!run.mergeable} onClick={() => onAutoApply(run)}><ShieldCheck size={16} /> Rápido</button>
                </Tooltip>
                <Tooltip text="Revierte el último apply. Requiere que el run tenga snapshot previo">
                  <button onClick={() => onRollback(run)}><RotateCcw size={16} /> Deshacer</button>
                </Tooltip>
              </div>
              {applyJson && <p className="muted">Last apply: {applyJson}</p>}
              <InsightSection title="Apply History" summary={`${applyHistory.length} evento(s)`} open={false}>
                <ApplyHistoryPanel applies={applyHistory} />
              </InsightSection>
            </>
          )}
          <div className="run-destructive-zone">
            <Tooltip text="Limpia runs completados con más de 24h. No afecta runs activos.">
              <button onClick={onArchiveOldRuns}>📦 Limpiar antiguos</button>
            </Tooltip>
            <Tooltip text="Elimina TODOS los runs del workspace. Acción irreversible.">
              <button onClick={onClearAllRuns}>🧨 Limpiar todo</button>
            </Tooltip>
          </div>
        </div>
      )}

      {/* ── INSIGHTS TAB ── */}
      {tab === "insights" && (
        <div className="agent-insights-body">
          <InsightSection
            title="Self-Improvement"
            summary={`${selfInsights?.trajectory?.grade ?? "sin run"} / ${selfInsights?.impact?.risk_flags.length ?? 0} riesgo(s)`}
            open={false}
          >
            <SelfImprovementPanel insights={selfInsights} />
          </InsightSection>
          <InsightSection
            title="Risk Map"
            summary={`${riskMap?.count ?? 0} patron(es)`}
            open={false}
          >
            <RiskMapPanel riskMap={riskMap} onRefresh={onRefreshRiskMap} />
          </InsightSection>
          <InsightSection
            title="NEMO Memory"
            summary={`${nemoState?.health.atom_count ?? 0} atoms / ${nemoState?.health.evidence_count ?? 0} evidence`}
            open={false}
          >
            <NemoMemoryPanel nemoState={nemoState} mcpWatcher={mcpWatcher} />
          </InsightSection>
          <InsightSection
            title="Apply Plan"
            summary={reviewPlan ? `${reviewPlan.mergeable ? "mergeable" : "blocked"} / ${reviewPlan.risk_flags.length} riesgo(s)` : "sin plan"}
            open={false}
          >
            <ReviewPlanPanel plan={reviewPlan} />
          </InsightSection>
        </div>
      )}

    </aside>
  );
}

function RepoWorkspaceSwitcher({ repos, repoDraft, onRepoDraftChange, onOpenRepo, onBrowseFolder, busy, error, activeRepo }: { repos: string[]; repoDraft: string; onRepoDraftChange: (value: string) => void; onOpenRepo: (repoPath?: string) => void; onBrowseFolder: () => Promise<string | null>; busy: boolean; error: string; activeRepo?: string }) {
  const [collapsed, setCollapsed] = React.useState<boolean>(false);

  const confirmAndOpen = React.useCallback((path: string) => {
    const folderName = path.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? path;
    const confirmed = window.confirm(
      `⚠️ Cambiar workspace del agente\n\n` +
      `Carpeta seleccionada:\n${path}\n\n` +
      `El agente tendrá acceso completo para MODIFICAR, CREAR y ELIMINAR archivos en esta carpeta.\n\n` +
      `¿Confirmas que quieres establecer "${folderName}" como workspace activo?`
    );
    if (confirmed) {
      onRepoDraftChange(path);
      onOpenRepo(path);
    }
  }, [onRepoDraftChange, onOpenRepo]);

  const pickFolder = React.useCallback(async () => {
    if (window.parent !== window) {
      const handler = (event: MessageEvent) => {
        if (event.data?.type !== 'pick-folder-result') return;
        window.removeEventListener('message', handler);
        if (event.data.path) confirmAndOpen(event.data.path);
      };
      window.addEventListener('message', handler);
      window.parent.postMessage({ type: 'pick-folder' }, '*');
      return;
    }
    const picked = await onBrowseFolder();
    if (picked) confirmAndOpen(picked);
  }, [confirmAndOpen, onBrowseFolder]);

  const handleOpenCurrent = React.useCallback(() => {
    if (repoDraft) confirmAndOpen(repoDraft);
  }, [repoDraft, confirmAndOpen]);

  const activeFolder = activeRepo ? (activeRepo.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? activeRepo) : null;

  return (
    <section className="repo-strip">
      <button className={`section-heading section-toggle ${collapsed ? "collapsed" : ""}`} onClick={() => setCollapsed((value) => !value)} title={collapsed ? "Expandir workspace" : "Colapsar workspace"}>
        <ChevronDown size={14} /> Workspace
      </button>
      {!collapsed && <>
      {activeRepo && (
        <div className="workspace-active-badge" title={activeRepo}>
          <HardDrive size={12} />
          <span className="workspace-active-name">{activeFolder}</span>
          <span className="workspace-active-access">acceso completo</span>
        </div>
      )}
      <div className="repo-open-row">
        <input value={repoDraft} onChange={(event) => onRepoDraftChange(event.target.value)} placeholder="c:/dev/repo" />
        <button className="repo-item repo-item-folder" onClick={pickFolder} disabled={busy} title="Seleccionar carpeta con explorador"><HardDrive size={14} /> Explorar…</button>
        <button className="repo-item" onClick={handleOpenCurrent} disabled={busy || !repoDraft} title="Establecer como workspace del agente">{busy ? "Abriendo…" : "Usar"}</button>
      </div>
      {error && <div className="repo-error">{error}</div>}
      {repos.length > 0 && <div className="repo-recents-label">Recientes</div>}
      {repos.map((repo) => (
        <button className={`repo-item repo-recent ${repo === activeRepo ? 'repo-active' : ''}`} key={repo} onClick={() => confirmAndOpen(repo)} disabled={busy} title={repo}>
          <HardDrive size={12} /> {repo.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? repo}
          {repo === activeRepo && <span className="repo-active-dot" />}
        </button>
      ))}
      </>}
    </section>
  );
}

function VersioningPanel({
  repoPath,
  status,
  branches,
  remotes,
  syncRemote,
  syncBranch,
  busy,
  diffPath,
  diffStaged,
  diffText,
  diffHunks,
  commitMessage,
  branchDraft,
  onRefresh,
  onSyncRemoteChange,
  onSyncBranchChange,
  onDiffPathChange,
  onLoadDiff,
  onStage,
  onStageHunk,
  selectedHunks,
  onToggleHunkSelection,
  onApplySelectedHunks,
  onCommitMessageChange,
  onCommit,
  onBranchDraftChange,
  onCheckout,
  onSync,
}: {
  repoPath: string;
  status: GitStatusState;
  branches: GitBranchItem[];
  remotes: GitRemote[];
  syncRemote: string;
  syncBranch: string;
  busy: boolean;
  diffPath: string;
  diffStaged: boolean;
  diffText: string;
  diffHunks: GitDiffHunk[];
  commitMessage: string;
  branchDraft: string;
  onRefresh: () => void;
  onSyncRemoteChange: (value: string) => void;
  onSyncBranchChange: (value: string) => void;
  onDiffPathChange: (value: string) => void;
  onLoadDiff: (path?: string, staged?: boolean) => void;
  onStage: (path: string, stage: boolean) => void;
  onStageHunk: (path: string, hunkHeader: string, stage: boolean) => void;
  selectedHunks: Record<string, boolean>;
  onToggleHunkSelection: (hunkHeader: string) => void;
  onApplySelectedHunks: (stage: boolean) => void;
  onCommitMessageChange: (value: string) => void;
  onCommit: () => void;
  onBranchDraftChange: (value: string) => void;
  onCheckout: (branch: string, create: boolean) => void;
  onSync: (direction: "pull" | "push") => void;
}) {
  return (
    <section className="section-surface versioning-panel">
      <div className="panel-title"><GitBranch size={16} /> Versionado</div>
      <div className="ops-panel">
        <div className="review-actions">
          <button onClick={onRefresh} disabled={busy}><RefreshCw size={14} /> Refresh</button>
          <button onClick={() => onSync("pull")}>Pull --rebase</button>
          <button onClick={() => onSync("push")}>Push</button>
        </div>
        <div className="repo-open-row">
          <select value={syncRemote} onChange={(event) => onSyncRemoteChange(event.target.value)}>
            <option value="">(default remote)</option>
            {remotes.map((remote) => <option key={remote.name} value={remote.name}>{remote.name}</option>)}
          </select>
          <input value={syncBranch} onChange={(event) => onSyncBranchChange(event.target.value)} placeholder="branch opcional" />
        </div>
        <div className="mini-list">
          <span>Repo activo: {repoPath}</span>
          <span>Branch: {status.branch || "-"} / ahead {status.ahead} / behind {status.behind}</span>
          <span>Cambios: {status.entries.length}</span>
        </div>
      </div>

      <div className="ops-panel">
        <div className="panel-title"><GitCompare size={16} /> Working tree</div>
        {status.entries.length === 0 ? <span className="empty-inline">Working tree limpio.</span> : <div className="extension-list">{status.entries.map((entry) => (
          <div className="extension-row" key={`${entry.xy}-${entry.path}`}>
            <div>
              <strong>{entry.path}</strong>
              <span>{entry.xy}{entry.original_path ? ` / from ${entry.original_path}` : ""}</span>
            </div>
            <div className="review-actions">
              <button onClick={() => {
                onDiffPathChange(entry.path);
                onLoadDiff(entry.path, false);
              }}>Diff</button>
              <button onClick={() => onStage(entry.path, !entry.staged)}>{entry.staged ? "Unstage" : "Stage"}</button>
            </div>
          </div>
        ))}</div>}
      </div>

      <div className="ops-panel">
        <div className="panel-title"><Code2 size={16} /> Diff</div>
        <div className="review-actions">
          <button onClick={() => onLoadDiff(diffPath, false)} className={!diffStaged ? "active" : ""}>Unstaged</button>
          <button onClick={() => onLoadDiff(diffPath, true)} className={diffStaged ? "active" : ""}>Staged</button>
        </div>
        <div className="repo-open-row">
          <input value={diffPath} onChange={(event) => onDiffPathChange(event.target.value)} placeholder="path opcional" />
          <button className="repo-item" onClick={() => onLoadDiff(diffPath, diffStaged)}>Load diff</button>
        </div>
        {diffPath && diffHunks.length > 0 && <div className="mini-list">
          <div className="review-actions">
            <button onClick={() => onApplySelectedHunks(!diffStaged)}>{diffStaged ? "Unstage selected" : "Stage selected"}</button>
          </div>
          {diffHunks.map((hunk) => (
            <div className="hunk-row" key={hunk.id}>
              <label className="hunk-select">
                <input type="checkbox" checked={selectedHunks[hunk.id] ?? false} onChange={() => onToggleHunkSelection(hunk.id)} />
                <span>{hunk.header}</span>
              </label>
              <button onClick={() => onStageHunk(diffPath, hunk.header, !diffStaged)}>{diffStaged ? "Unstage hunk" : "Stage hunk"}</button>
            </div>
          ))}
        </div>}
        <pre className="git-diff-output">{diffText || "Selecciona un archivo o carga diff para ver cambios."}</pre>
      </div>

      <div className="ops-panel">
        <div className="panel-title"><CheckCircle2 size={16} /> Commit</div>
        <div className="repo-open-row">
          <input value={commitMessage} onChange={(event) => onCommitMessageChange(event.target.value)} placeholder="feat: summary" />
          <button className="repo-item" onClick={onCommit}>Commit</button>
        </div>
      </div>

      <div className="ops-panel">
        <div className="panel-title"><GitBranch size={16} /> Branches</div>
        <div className="repo-open-row">
          <input value={branchDraft} onChange={(event) => onBranchDraftChange(event.target.value)} placeholder="feature/my-branch" />
          <button className="repo-item" onClick={() => onCheckout(branchDraft, true)}>Create</button>
        </div>
        {branches.length === 0 ? <span className="empty-inline">No se pudieron cargar ramas.</span> : <div className="mini-list">{branches.map((branch) => (
          <button className="repo-item" key={branch.name} onClick={() => onCheckout(branch.name, false)}>{branch.current ? "* " : ""}{branch.name}</button>
        ))}</div>}
      </div>

    </section>
  );
}

function TerminalPanel({ command, running, result, onCommandChange, onRun }: { command: string; running: boolean; result: TerminalRunResult | null; onCommandChange: (value: string) => void; onRun: () => void }) {
  return (
    <section className="section-surface terminal-panel">
      <div className="panel-title"><TerminalSquare size={16} /> Terminal</div>
      <div className="ops-panel">
        <div className="repo-open-row">
          <input value={command} onChange={(event) => onCommandChange(event.target.value)} placeholder="git status --short" />
          <button className="repo-item" onClick={onRun} disabled={running}>{running ? "Running" : "Run"}</button>
        </div>
        {result && <div className="mini-list">
          <span>exit: {result.exit_code ?? "timeout"} / duration: {result.duration_ms} ms</span>
          <span>cwd: {result.cwd}</span>
        </div>}
      </div>
      <div className="ops-panel terminal-output">
        <div className="panel-title"><Code2 size={16} /> Output</div>
        <pre>{result ? `${result.stdout || ""}${result.stderr ? `\n${result.stderr}` : ""}`.trim() || "(no output)" : "Run a command to view output."}</pre>
      </div>
    </section>
  );
}

function BrowserPanel({ draft, state, queryDraft, searchState, searching, onDraftChange, onOpen, onQueryDraftChange, onSearch, onOpenSearchResult }: { draft: string; state: BrowserState; queryDraft: string; searchState: BrowserSearchState; searching: boolean; onDraftChange: (value: string) => void; onOpen: () => void; onQueryDraftChange: (value: string) => void; onSearch: () => void; onOpenSearchResult: (url: string) => void }) {
  return (
    <section className="section-surface browser-panel">
      <div className="panel-title"><Globe size={16} /> Browser</div>
      <div className="ops-panel">
        <div className="panel-title"><Search size={16} /> Web Search (Playwright Chromium)</div>
        <div className="repo-open-row">
          <input value={queryDraft} onChange={(event) => onQueryDraftChange(event.target.value)} placeholder="Search the web..." />
          <button className="repo-item" onClick={onSearch} disabled={searching}>{searching ? "Searching" : "Search"}</button>
        </div>
        <div className="mini-list">
          <span>query: {searchState.query || state.search_query || "-"}</span>
          <span>engine: {searchState.engine || "playwright-chromium"}</span>
        </div>
        {searchState.results.length === 0 ? <span className="empty-inline">No search results yet.</span> : <div className="mini-list">{searchState.results.map((item) => (
          <button key={item.url} onClick={() => onOpenSearchResult(item.url)} title={item.url}>
            <strong>{item.title}</strong>
            <br />
            {item.snippet || item.url}
          </button>
        ))}</div>}
      </div>
      <div className="ops-panel">
        <div className="repo-open-row">
          <input value={draft} onChange={(event) => onDraftChange(event.target.value)} placeholder="https://github.com" />
          <button className="repo-item" onClick={onOpen}>Open</button>
        </div>
        <div className="mini-list">
          <span>last: {state.last_url || "-"}</span>
          <span>homepage: {state.homepage || "-"}</span>
        </div>
      </div>
      <div className="ops-panel">
        <div className="panel-title"><Clock3 size={16} /> History</div>
        {state.history.length === 0 ? <span className="empty-inline">No browser history yet.</span> : <div className="mini-list">{state.history.map((item) => <span key={item}>{item}</span>)}</div>}
      </div>
      <div className="ops-panel">
        <div className="panel-title"><Clock3 size={16} /> Search History</div>
        {state.search_history.length === 0 ? <span className="empty-inline">No search history yet.</span> : <div className="mini-list">{state.search_history.map((item) => <button key={item} onClick={() => onQueryDraftChange(item)}>{item}</button>)}</div>}
      </div>
    </section>
  );
}

function ExtensionsPanel({ items, busy, onToggle, onReload }: { items: ExtensionItem[]; busy: boolean; onToggle: (name: string, enabled: boolean) => void; onReload: () => void }) {
  return (
    <section className="section-surface extensions-panel">
      <div className="panel-title"><Puzzle size={16} /> Extensions</div>
      <div className="ops-panel">
        <div className="review-actions"><button onClick={onReload} disabled={busy}><RefreshCw size={14} /> Reload</button></div>
        {items.length === 0 ? <span className="empty-inline">No extensions configured.</span> : <div className="extension-list">{items.map((item) => (
          <div className="extension-row" key={item.name}>
            <div>
              <strong>{item.name}</strong>
              <span>{item.version}</span>
            </div>
            <button className={item.enabled ? "enabled" : "disabled"} onClick={() => onToggle(item.name, !item.enabled)}>{item.enabled ? "Enabled" : "Disabled"}</button>
          </div>
        ))}</div>}
      </div>
    </section>
  );
}

function RiskMapPanel({ riskMap, onRefresh }: { riskMap: RiskMapState | null; onRefresh: () => void }) {
  const patterns = riskMap?.patterns ?? [];
  return (
    <section className="self-improve-panel">
      <div className="panel-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}><AlertTriangle size={16} /> Risk Map</span>
        <button onClick={onRefresh} title="Reload risk patterns" style={{ background: "none", border: "none", cursor: "pointer", padding: 2 }}><RefreshCw size={13} /></button>
      </div>
      {!riskMap ? (
        <span className="empty-inline">Loading risk patterns…</span>
      ) : !riskMap.enabled ? (
        <span className="empty-inline">NEMO memory not configured.</span>
      ) : patterns.length === 0 ? (
        <span className="empty-inline">No risk patterns yet. They accumulate after repair failures.</span>
      ) : (
        <NemoSection title={`${riskMap.count} known risk pattern(s)`} empty="">
          {patterns.map((p) => (
            <NemoLine key={p.id} tone="correction" value={p.content} meta={`importance ${p.importance}${p.topic ? ` / ${p.topic}` : ""}`} />
          ))}
        </NemoSection>
      )}
    </section>
  );
}

function SelfImprovementPanel({ insights }: { insights: SelfModInsights | null }) {
  const trajectory = insights?.trajectory;
  const impact = insights?.impact;
  const similarRuns = insights?.similar_runs.runs ?? [];
  return (
    <section className="self-improve-panel">
      <div className="panel-title"><ShieldCheck size={16} /> Self-Improvement</div>
      {!trajectory || !impact ? <span className="empty-inline">Select a persisted run to inspect trajectory and impact.</span> : <>
        <div className="trajectory-grid">
          <div><span>Grade</span><strong>{trajectory.grade}</strong></div>
          <div><span>Merge</span><strong>{trajectory.mergeable ? "ready" : "blocked"}</strong></div>
          <div><span>Events</span><strong>{trajectory.timeline.length}</strong></div>
          <div><span>Similar</span><strong>{insights?.similar_runs.count ?? 0}</strong></div>
        </div>
        <div className="impact-flags">
          {[
            impact.touches_source && "source",
            impact.touches_tests && "tests",
            impact.touches_ui && "ui",
            impact.touches_mcp && "mcp",
            impact.touches_cli && "cli",
          ].filter(Boolean).map((item) => <span key={String(item)}>{String(item)}</span>)}
        </div>
        <div className="mini-list">
          {(impact.suggested_tests.length ? impact.suggested_tests : ["No extra validation suggested"]).slice(0, 5).map((item) => <span key={item}>{item}</span>)}
        </div>
        {impact.risk_flags.length > 0 && <div className="risk-box">{impact.risk_flags.map((risk) => <span key={risk}>{risk}</span>)}</div>}
        <NemoSection title="Similar runs" empty="No prior trajectory matches yet.">
          {similarRuns.slice(0, 4).map((item) => <NemoLine key={item.id} tone="trajectory" value={item.content} meta={`importance ${item.importance}`} />)}
        </NemoSection>
      </>}
    </section>
  );
}

function CognitiveStatsPanel({ cognitiveStats, onRefresh }: { cognitiveStats: CognitiveStatsState | null; onRefresh: () => void }) {
  const memory = cognitiveStats?.memory_kpis;
  const run = cognitiveStats?.run_kpis;
  return (
    <section className="self-improve-panel">
      <div className="panel-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Database size={16} /> Cognitive KPIs</span>
        <button onClick={onRefresh} title="Reload cognitive KPIs" style={{ background: "none", border: "none", cursor: "pointer", padding: 2 }}><RefreshCw size={13} /></button>
      </div>
      {!cognitiveStats ? (
        <span className="empty-inline">Loading cognitive KPIs…</span>
      ) : !cognitiveStats.enabled ? (
        <span className="empty-inline">NEMO memory not configured.</span>
      ) : memory && run ? <>
        <div className="metric-grid">
          <Metric icon={<Database size={16} />} label="Atoms" value={memory.atom_count} />
          <Metric icon={<ShieldCheck size={16} />} label="Corrections" value={memory.correction_count} />
          <Metric icon={<TerminalSquare size={16} />} label="Evidence" value={memory.evidence_count} />
          <Metric icon={<Bot size={16} />} label="Feedback" value={memory.feedback_count} />
        </div>
        <div className="metric-grid">
          <Metric icon={<CheckCircle2 size={16} />} label="Useful" value={memory.useful_feedback_rate == null ? "-" : `${Math.round(memory.useful_feedback_rate * 100)}%`} />
          <Metric icon={<ArrowUp size={16} />} label="Apply succ." value={`${Math.round(run.apply_success_rate * 100)}%`} />
          <Metric icon={<AlertTriangle size={16} />} label="Blocked" value={`${Math.round(run.blocked_rate * 100)}%`} />
          <Metric icon={<Database size={16} />} label="Portfolio" value={memory.portfolio_utilization == null ? "-" : `${Math.round(memory.portfolio_utilization * 100)}%`} />
        </div>
        <div className="mini-list">
          <span>useful {memory.useful_feedback_count} / {memory.feedback_count} feedback events</span>
          <span>runs {run.total_runs} total, {run.ready_runs} ready, {run.blocked_runs} blocked</span>
          <span>portfolio tokens {memory.portfolio_tokens == null ? "-" : memory.portfolio_tokens} / {memory.portfolio_budget == null ? "-" : memory.portfolio_budget}</span>
        </div>
      </> : null}
    </section>
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

function RepoSettingsPanel({ state, settings, onSettingsChange, onSaveSettings, repoDraft, onRepoDraftChange, onOpenRepo, cloneDraft, onCloneDraftChange, onCloneRepo, cleanupResult, onCleanup, orphanCleanupResult, onCleanupOrphans, mcpWatcher, onRefreshMcpWatcher, selectedNemoTools = DEFAULT_SESSION_NEMO_TOOLS, onToggleNemoTool }: { state: MissionState; settings: MissionState["settings"]; onSettingsChange: (settings: MissionState["settings"]) => void; onSaveSettings: () => void; repoDraft: string; onRepoDraftChange: (value: string) => void; onOpenRepo: (repoPath?: string) => void; cloneDraft: { url: string; destination: string }; onCloneDraftChange: (draft: { url: string; destination: string }) => void; onCloneRepo: () => void; cleanupResult: CleanupResult | null; onCleanup: (dryRun: boolean) => void; orphanCleanupResult: OrphanCleanupResult | null; onCleanupOrphans: (dryRun: boolean) => void; mcpWatcher: NemoMcpWatcherState | null; onRefreshMcpWatcher: () => void; selectedNemoTools?: string[]; onToggleNemoTool?: (toolName: string) => void }) {
  const update = (key: keyof MissionState["settings"], value: string | number | boolean | string[]) => onSettingsChange({ ...settings, [key]: value });
  const watcherTone = mcpWatcher?.active ? "ready" : "blocked";
  const watcherLabel = mcpWatcher?.status ?? "loading";
  const capabilities = mcpWatcher?.capabilities;
  const capabilityRows = [
    ["context_bootstrap", capabilities?.supports_context_bootstrap],
    ["prime_context", capabilities?.supports_prime_context],
    ["search_memories", capabilities?.supports_search_memories],
    ["core reads", capabilities?.supports_core_context_reads],
    ["write/read", capabilities?.supports_write_read_roundtrip],
  ] as const;
  const fieldIds = {
    modelBaseUrl: "settings-model-base-url",
    defaultModel: "settings-default-model",
    provider: "settings-provider",
    memoryDb: "settings-memory-db",
    nemoMcpUrl: "settings-nemo-mcp-url",
    runtimePath: "settings-runtime-path",
    validationPolicy: "settings-validation-policy",
    timeoutSeconds: "settings-timeout-seconds",
    maxRuntimeMinutes: "settings-max-runtime-minutes",
    heartbeatMinutes: "settings-heartbeat-minutes",
    tokenBudget: "settings-token-budget",
    contextWindowTokens: "settings-context-window-tokens",
    chatMaxTokens: "settings-chat-max-tokens",
    imageGenBackend: "settings-image-gen-backend",
    imageGenUrl: "settings-image-gen-url",
    repoPath: "settings-repo-path",
    cloneUrl: "settings-clone-url",
    cloneDestination: "settings-clone-destination",
  };
  return (
    <section className="ops-panel settings-editor" aria-labelledby="settings-panel-title">
      <div className="panel-title"><Settings size={16} /> <span id="settings-panel-title">Ajustes</span></div>
      <label htmlFor={fieldIds.modelBaseUrl}>URL de LM Studio<input id={fieldIds.modelBaseUrl} value={settings.model_base_url} onChange={(event) => update("model_base_url", event.target.value)} /></label>
      <label htmlFor={fieldIds.defaultModel}>Modelo<input id={fieldIds.defaultModel} value={settings.default_model} onChange={(event) => update("default_model", event.target.value)} /></label>
      <label htmlFor={fieldIds.provider}>Modo del agente<select id={fieldIds.provider} value={settings.provider} onChange={(event) => update("provider", event.target.value)}><option value="subprocess">Real con LM Studio</option></select></label>
      <label htmlFor={fieldIds.memoryDb}>Base de memoria NEMO<input id={fieldIds.memoryDb} value={settings.memory_db} onChange={(event) => update("memory_db", event.target.value)} /></label>
      <label htmlFor={fieldIds.nemoMcpUrl}>Transporte MCP NEMO<input id={fieldIds.nemoMcpUrl} value={settings.nemo_mcp_url || ""} onChange={(event) => update("nemo_mcp_url", event.target.value)} placeholder="stdio://vscode/nemo" /></label>
      <div className={`mcp-watcher ${watcherTone}`}>
        <div>
          <strong>MCP watcher</strong>
          <span>{watcherLabel}{mcpWatcher?.latency_ms !== undefined ? ` / ${mcpWatcher.latency_ms} ms` : ""}</span>
          {mcpWatcher?.error && <small>{mcpWatcher.error}</small>}
          {capabilities?.errors && capabilities.errors.length > 0 && <small>{capabilities.errors.join(" / ")}</small>}
        </div>
        <button onClick={onRefreshMcpWatcher}><RefreshCw size={14} /> Comprobar</button>
      </div>
      <div className="repo-picker" aria-labelledby="nemo-capabilities-title">
        <strong id="nemo-capabilities-title">Capabilities NEMO MCP</strong>
        <div className="mini-list">
          {capabilityRows.map(([label, ok]) => (
            <span key={label} className={ok ? "ready" : "blocked"}>{label}: {ok ? "ok" : "blocked"}</span>
          ))}
        </div>
      </div>
      <div className="repo-picker" aria-labelledby="nemo-tool-selector-title">
        <strong id="nemo-tool-selector-title">Selector de tools MCP por sesion</strong>
        <div className="mini-list">
          {(mcpWatcher?.available_tools && mcpWatcher.available_tools.length > 0 ? mcpWatcher.available_tools : DEFAULT_SESSION_NEMO_TOOLS).map((toolName) => {
            const toolInputId = `nemo-tool-${toolName.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
            return <label key={toolName} htmlFor={toolInputId} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                id={toolInputId}
                type="checkbox"
                checked={selectedNemoTools.includes(toolName)}
                onChange={() => onToggleNemoTool?.(toolName)}
              />
              <span>{toolName}</span>
            </label>;
          })}
        </div>
      </div>
      <label htmlFor={fieldIds.runtimePath}>Carpeta runtime<input id={fieldIds.runtimePath} value={settings.runtime_path} onChange={(event) => update("runtime_path", event.target.value)} /></label>
      <label htmlFor={fieldIds.validationPolicy}>Nivel de validacion<select id={fieldIds.validationPolicy} value={settings.validation_policy} onChange={(event) => update("validation_policy", event.target.value)}><option value="none">ninguna</option><option value="smoke">rapida</option><option value="targeted">dirigida</option><option value="full">completa</option></select></label>
      <div className="settings-grid">
        <label htmlFor={fieldIds.timeoutSeconds}>Timeout (s)<input id={fieldIds.timeoutSeconds} type="number" value={settings.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></label>
        <label htmlFor={fieldIds.maxRuntimeMinutes}>Max minutos<input id={fieldIds.maxRuntimeMinutes} type="number" value={settings.max_runtime_minutes} onChange={(event) => update("max_runtime_minutes", Number(event.target.value))} /></label>
        <label htmlFor={fieldIds.heartbeatMinutes}>Heartbeat (min)<input id={fieldIds.heartbeatMinutes} type="number" value={settings.heartbeat_minutes} onChange={(event) => update("heartbeat_minutes", Number(event.target.value))} /></label>
        <label htmlFor={fieldIds.tokenBudget}>Presupuesto tokens<input id={fieldIds.tokenBudget} type="number" value={settings.token_budget} onChange={(event) => update("token_budget", Number(event.target.value))} /></label>
        <label htmlFor={fieldIds.contextWindowTokens}>Ventana contexto<input id={fieldIds.contextWindowTokens} type="number" value={settings.context_window_tokens} onChange={(event) => update("context_window_tokens", Number(event.target.value))} /></label>
        <label htmlFor={fieldIds.chatMaxTokens}>Salida chat max<input id={fieldIds.chatMaxTokens} type="number" value={settings.chat_max_tokens} onChange={(event) => update("chat_max_tokens", Number(event.target.value))} /></label>
      </div>
      <div className="settings-grid">
        <label htmlFor={fieldIds.imageGenBackend}>Backend imagen<select id={fieldIds.imageGenBackend} value={settings.image_gen_backend} onChange={(event) => update("image_gen_backend", event.target.value)}><option value="auto">auto</option><option value="automatic1111">AUTOMATIC1111</option><option value="comfyui">ComfyUI</option></select></label>
        <label htmlFor={fieldIds.imageGenUrl}>URL imagen<input id={fieldIds.imageGenUrl} value={settings.image_gen_url} onChange={(event) => update("image_gen_url", event.target.value)} placeholder="http://localhost:8188" /></label>
      </div>
      <div className="review-actions"><button onClick={onSaveSettings}><CheckCircle2 size={16} /> Guardar ajustes</button></div>
      <div className="repo-picker" aria-labelledby="repo-picker-title">
        <strong id="repo-picker-title">Repository</strong>
        <label className="sr-only" htmlFor={fieldIds.repoPath}>Repository path</label>
        <input id={fieldIds.repoPath} value={repoDraft} onChange={(event) => onRepoDraftChange(event.target.value)} />
        <button onClick={() => onOpenRepo()}><HardDrive size={15} /> Open folder</button>
        <div className="mini-list">{state.settings.recent_repos?.map((repo) => <button key={repo} onClick={() => onOpenRepo(repo)}>{repo}</button>)}</div>
      </div>
      <div className="repo-picker" aria-labelledby="clone-picker-title">
        <strong id="clone-picker-title">Clone from git</strong>
        <label className="sr-only" htmlFor={fieldIds.cloneUrl}>Git clone URL</label>
        <input id={fieldIds.cloneUrl} value={cloneDraft.url} onChange={(event) => onCloneDraftChange({ ...cloneDraft, url: event.target.value })} placeholder="https://github.com/org/repo.git" />
        <label className="sr-only" htmlFor={fieldIds.cloneDestination}>Clone destination path</label>
        <input id={fieldIds.cloneDestination} value={cloneDraft.destination} onChange={(event) => onCloneDraftChange({ ...cloneDraft, destination: event.target.value })} placeholder="c:/dev/repo" />
        <button onClick={onCloneRepo}><GitBranch size={15} /> Clone and open</button>
      </div>
      <div className="repo-picker" aria-labelledby="artifact-cleanup-title">
        <strong id="artifact-cleanup-title">Limpieza de artefactos</strong>
        <div className="review-actions"><button onClick={() => onCleanup(true)}>Escanear</button><button onClick={() => onCleanup(false)}>Borrar encontrados</button></div>
        {cleanupResult && <span className="muted">{cleanupResult.dry_run ? cleanupResult.candidates.length : cleanupResult.deleted.length} file(s) / {cleanupResult.max_age_days} days</span>}
      </div>
      <div className="repo-picker" aria-labelledby="orphan-cleanup-title">
        <strong id="orphan-cleanup-title">Jobs huerfanos</strong>
        <div className="review-actions"><button onClick={() => onCleanupOrphans(true)}>Detectar</button><button onClick={() => onCleanupOrphans(false)}>Marcar y limpiar</button></div>
        {orphanCleanupResult && <span className="muted">{orphanCleanupResult.summary.total} huerfano(s) / memoria {orphanCleanupResult.summary.in_memory} / snapshots {orphanCleanupResult.summary.snapshots}</span>}
      </div>
    </section>
  );
}

function NemoMemoryPanel({ nemoState, mcpWatcher }: { nemoState: NemoState | null; mcpWatcher: NemoMcpWatcherState | null }) {
  const portfolioLines = (nemoState?.context_portfolio?.context ?? "").split("\n").filter(Boolean).slice(0, 8);
  const usedMemories = nemoState?.used_memories.length ? nemoState.used_memories : [];
  const corrections = nemoState?.corrections ?? [];
  const evidence = nemoState?.evidence ?? [];
  const feedback = nemoState?.feedback ?? [];
  const capabilities = mcpWatcher?.capabilities;
  return (
    <section className="nemo-panel">
      <div className="panel-title"><Database size={16} /> NEMO Memory</div>
      <div className="nemo-health">
        <span className={nemoState?.health.enabled ? "ready" : "blocked"}>{nemoState?.health.status ?? "loading"}</span>
        <strong>{nemoState?.health.atom_count ?? 0}</strong><small>atoms</small>
        <strong>{nemoState?.health.evidence_count ?? 0}</strong><small>evidence</small>
        <strong>{nemoState?.health.feedback_count ?? 0}</strong><small>feedback</small>
      </div>
      <div className="nemo-health">
        <span className={mcpWatcher?.active ? "ready" : "blocked"}>mcp {mcpWatcher?.status ?? "loading"}</span>
        <strong>{mcpWatcher?.configured ? "configured" : "not set"}</strong><small>remote</small>
        <strong>{mcpWatcher?.latency_ms ?? 0}</strong><small>ms</small>
      </div>
      <NemoSection title="MCP capabilities" empty="No capability probe yet.">
        <NemoLine tone={capabilities?.supports_context_bootstrap ? "meta" : "correction"} value={`context_bootstrap: ${capabilities?.supports_context_bootstrap ? "ok" : "blocked"}`} />
        <NemoLine tone={capabilities?.supports_prime_context ? "meta" : "correction"} value={`prime_context: ${capabilities?.supports_prime_context ? "ok" : "blocked"}`} />
        <NemoLine tone={capabilities?.supports_search_memories ? "meta" : "correction"} value={`search_memories: ${capabilities?.supports_search_memories ? "ok" : "blocked"}`} />
        <NemoLine tone={capabilities?.supports_write_read_roundtrip ? "meta" : "correction"} value={`write/read roundtrip: ${capabilities?.supports_write_read_roundtrip ? "ok" : "blocked"}`} meta={capabilities?.errors?.join(" / ") || undefined} />
      </NemoSection>
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
  // Extract type label from value if it starts with [type]
  let displayValue = value;
  let type = tone;
  
  const typeMatch = value.match(/^\[(\w+)\]\s+(.*)$/);
  if (typeMatch) {
    type = typeMatch[1].toLowerCase();
    displayValue = typeMatch[2];
  }
  
  const typeLabels: Record<string, { icon: string; label: string; color: string }> = {
    correction: { icon: '⚡', label: 'Aprendizaje', color: 'learning' },
    preference: { icon: '✨', label: 'Preferencia', color: 'preference' },
    project_fact: { icon: '📌', label: 'Contexto', color: 'fact' },
    decision: { icon: '🎯', label: 'Decisión', color: 'decision' },
    evidence: { icon: '📎', label: 'Evidencia', color: 'evidence' },
    feedback: { icon: '💬', label: 'Retroalimentación', color: 'feedback' },
    trace: { icon: '🔍', label: 'Trace', color: 'trace' },
    portfolio: { icon: '📦', label: 'Portfolio', color: 'portfolio' },
    meta: { icon: '📊', label: 'Metadatos', color: 'meta' },
  };
  
  const typeInfo = typeLabels[type] || { icon: '•', label: type, color: type };
  const displayMeta = meta ? `${typeInfo.icon} ${typeInfo.label}` : `${typeInfo.icon} ${typeInfo.label}`;
  
  return <div className={`nemo-line ${typeInfo.color}`} data-type={type}><span>{displayMeta}</span><p>{displayValue}</p></div>;
}

function parseInlineToolCall(content: string): Array<{ name: string; summary: string; status: string }> {
  const parsed: Array<{ name: string; summary: string; status: string }> = [];
  const tryParse = (candidate: string) => {
    try {
      const payload = JSON.parse(candidate) as { tool?: unknown; parameters?: unknown; args?: unknown; action?: unknown };
      const toolName = typeof payload.tool === "string"
        ? payload.tool
        : typeof payload.action === "string"
          ? payload.action
          : "";
      if (!toolName) return;
      const params = payload.parameters ?? payload.args ?? {};
      let summary = "executed";
      if (params && typeof params === "object") {
        const keys = Object.keys(params as Record<string, unknown>);
        summary = keys.length ? `params: ${keys.join(", ")}` : "without params";
      }
      parsed.push({ name: toolName, summary, status: "completed" });
    } catch {
      // ignore non-tool JSON blocks
    }
  };

  const trimmed = content.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) tryParse(trimmed);
  const codeBlocks = content.match(/```(?:json)?\s*[\s\S]*?```/g) ?? [];
  codeBlocks.forEach((block) => {
    const raw = block.replace(/^```(?:json)?\s*/i, "").replace(/```$/, "").trim();
    if (raw) tryParse(raw);
  });
  return parsed;
}

function parseAgentMessageDecorations(content: string): { cleanedContent: string; risks: string[]; inlineTools: Array<{ name: string; summary: string; status: string }> } {
  const inlineTools = parseInlineToolCall(content);
  const risks: string[] = [];
  const lines = content.split("\n");
  const keptLines = lines.filter((line) => {
    const match = line.match(/^\s*Risks:\s*(.+)$/i);
    if (!match) return true;
    const items = match[1].split(",").map((item) => item.trim()).filter(Boolean);
    risks.push(...items);
    return false;
  });

  let cleaned = keptLines.join("\n").trim();
  if (inlineTools.length > 0) {
    cleaned = cleaned
      .replace(/```(?:json)?\s*[\s\S]*?```/g, "")
      .replace(/^\s*\{[\s\S]*\}\s*$/g, "")
      .trim();
  }
  return { cleanedContent: cleaned || (inlineTools.length ? "Tool executed." : ""), risks, inlineTools };
}

const _LEGACY_MEMORY_TOOL_NAMES = new Set([
  "prime_context",
  "build_context_portfolio",
  "search_memories",
  "anticipate",
  "store_conversation",
  "cognitive_ingest",
  "create_correction",
  "record_context_feedback",
  "expand_context_evidence",
  "get_context_portfolio_stats",
  "refresh_context_portfolio",
  "compare_context_strategies",
  "compress_context_artifact",
]);

function _canonicalizeToolName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "tool";
  if (trimmed.startsWith("spacecode.")) return trimmed.slice("spacecode.".length);
  return trimmed;
}

function _normalizeToolDisplayName(name: string): string {
  const trimmed = _canonicalizeToolName(name);
  if (!trimmed) return "tool";
  const parts = trimmed.split(".").filter(Boolean);
  if (parts.length >= 2) return `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
  return parts[0];
}

function _isNemoMemoryToolName(name: string): boolean {
  const canonical = _canonicalizeToolName(name).toLowerCase();
  return canonical.startsWith("nemo_memory.") || canonical.startsWith("nemo.");
}

function HomeMcpEvidence({ tools }: { tools: AgentToolCall[] }) {
  const memoryTools = tools.filter((tool) => _isNemoMemoryToolName(tool.name));
  if (memoryTools.length === 0) return null;
  const completed = memoryTools.filter((tool) => tool.status === "completed");
  const skippedTools = memoryTools.filter((tool) => tool.status === "skipped");
  const failedTools = memoryTools.filter((tool) => tool.status === "failed");
  const names = Array.from(new Set(completed.map((tool) => _normalizeToolDisplayName(tool.name)))).slice(0, 4);
  const skipped = skippedTools.length;
  const failed = failedTools.length;
  const label = completed.length > 0 ? "NEMO MCP verificado" : "NEMO MCP consultado";
  const detail = names.length > 0 ? names.join(" · ") : `${memoryTools.length} llamadas`;

  return (
    <div className={`home-chat-mcp-proof ${failed ? "has-failures" : ""}`} title={`${memoryTools.length} llamadas NEMO MCP${skipped ? `, ${skipped} optimizadas` : ""}${failed ? `, ${failed} fallidas` : ""}`}>
      <div className="mcp-proof-head">
        <Database size={13} aria-hidden="true" />
        <strong>{label}</strong>
        <small>{memoryTools.length} llamadas</small>
      </div>
      <div className="mcp-proof-body">
        <span className="mcp-proof-completed"><CheckCircle2 size={11} />{detail}</span>
        {skipped > 0 && <span className="mcp-proof-skipped"><Clock3 size={11} />{skipped} optimizadas</span>}
        {failed > 0 && <span className="mcp-proof-failed"><AlertTriangle size={11} />{failed} fallidas</span>}
      </div>
    </div>
  );
}

function _collectRecentToolNames(messages: AgentMessage[]): string[] {
  const collected: string[] = [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant") continue;
    const payloadTools = message.tool_calls ?? [];
    for (const tool of payloadTools) {
      if (tool?.name) collected.push(_canonicalizeToolName(String(tool.name)));
    }
    const inlineTools = parseInlineToolCall(message.content);
    for (const tool of inlineTools) {
      if (tool?.name) collected.push(_canonicalizeToolName(String(tool.name)));
    }
    if (collected.length >= 8) break;
  }
  const seen = new Set<string>();
  return collected.filter((name) => {
    const key = name.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function AgentLiveStatus({
  busy,
  queuedPrompt,
  messages,
  compact = false,
}: {
  busy: boolean;
  queuedPrompt: string | null;
  messages: AgentMessage[];
  compact?: boolean;
}) {
  const [tick, setTick] = useState<number>(0);
  const recentTools = useMemo(() => _collectRecentToolNames(messages), [messages]);

  useEffect(() => {
    if (!busy) {
      setTick(0);
      return;
    }
    const timer = window.setInterval(() => setTick((value) => value + 1), 1200);
    return () => window.clearInterval(timer);
  }, [busy]);

  if (!busy && !queuedPrompt) return null;

  const toolName = recentTools.length > 0 ? _normalizeToolDisplayName(recentTools[tick % recentTools.length]) : null;
  const phases: Array<{ label: string; detail: string }> = [
    { label: "pensando", detail: "Analizando tu instruccion y el estado actual." },
    { label: "cargando contexto", detail: "Recopilando memoria y continuidad de la tarea." },
    { label: "investigando", detail: "Contrastando riesgos, decisiones y siguientes pasos." },
    { label: toolName ? `usando tool: ${toolName}` : "usando tools", detail: toolName ? `Ejecutando ${toolName} para obtener evidencia.` : "Ejecutando herramientas de Mission Control y NEMO." },
    { label: "escribiendo", detail: "Preparando una respuesta operativa y accionable." },
  ];
  const current = phases[tick % phases.length];

  return (
    <div className={`agent-live-status ${busy ? "live" : "queued"} ${compact ? "compact" : ""}`} aria-live="polite">
      <span className="agent-live-dot" aria-hidden="true" />
      <div className="agent-live-copy">
        <strong>{busy ? `Agente ${current.label}` : "Mensaje en cola"}</strong>
        <small>{busy ? current.detail : `Siguiente: ${queuedPrompt}`}</small>
      </div>
      {busy && <div className="agent-live-steps" aria-hidden="true">
        {phases.map((phase, index) => (
          <span key={`${phase.label}-${index}`} className={index === (tick % phases.length) ? "active" : ""} />
        ))}
      </div>}
    </div>
  );
}

function shouldAttachRunContextToMessage(content: string): boolean {
  const normalized = content.toLowerCase();
  const runIntentPattern = /\b(run|runs|diff|patch|hunk|review|revisar|aplicar|apply|rollback|riesgo|risk|aprob|approval|queue|cola|timeline|merge|conflict|conflicto|archivo|files?)\b/;
  return runIntentPattern.test(normalized);
}

function routeChatMode(content: string, hasSelectedRun: boolean): "chat" | "deep-research" | "execution" {
  if (hasSelectedRun) return "execution";
  const normalized = content.toLowerCase();
  const executionPattern = /\b(run|runs|diff|patch|hunk|review|revisar|aplicar|apply|rollback|merge|sandbox|fix|corrige|arregla|commit)\b/;
  const researchPattern = /\b(investiga|investigar|research|analiza|analizar|analyze|buscar|busca|web|fuentes|sources|benchmark|comparar|compare|deep)\b/;
  if (executionPattern.test(normalized)) return "execution";
  if (researchPattern.test(normalized)) return "deep-research";
  return "chat";
}

function AgentChatMessage({ message, onRunAction }: { message: AgentMessage; onRunAction: (action: AgentAction) => void }) {
  const parsed = parseAgentMessageDecorations(message.content);
  const payloadTools = message.tool_calls ?? [];
  const sources = message.sources ?? [];
  const traceEvents = message.agent_trace ?? [];
  const mergedTools: RenderedToolCall[] = [
    ...payloadTools.map((tool) => ({ ...tool, source: "payload" as const })),
    ...parsed.inlineTools.map((tool, index) => ({
      id: `inline-${message.id}-${index}`,
      name: tool.name,
      tool_name: tool.name,
      alias_name: undefined,
      status: tool.status,
      summary: tool.summary,
      source: "inline" as const,
    })),
  ];

  return (
    <article className={`chat-message ${message.role}`}>
      <div className="message-role">{message.role}</div>
      <MessageRichText content={parsed.cleanedContent} animate={message.role === "assistant"} />
      <div className="message-meta-row">
        <span className="message-meta-pill">~{estimateTokens(message.content)} tok</span>
        {mergedTools.length > 0 && <span className="message-meta-pill">tools {mergedTools.length}</span>}
        {sources.length > 0 && <span className="message-meta-pill">sources {sources.length}</span>}
        {traceEvents.length > 0 && <span className="message-meta-pill">trace {traceEvents.length}</span>}
      </div>
      {sources.length > 0 && <div className="message-sources">
        {sources.map((source, index) => (
          <a key={`${source.url}-${index}`} className="message-source-item" href={source.url} target="_blank" rel="noreferrer">
            <div className="message-source-head">
              <strong>{source.title || source.url}</strong>
              {source.cached && <em className="message-source-badge">cache</em>}
            </div>
            <span>{source.url}</span>
            {source.snippet && <small>{source.snippet}</small>}
          </a>
        ))}
      </div>}
      {traceEvents.length > 0 && <div className="agent-trace-list">
        {traceEvents.map((event) => (
          <div className={`agent-trace-item status-${event.status}`} key={event.id}>
            <span className="agent-trace-step">{event.step}</span>
            <div className="agent-trace-copy">
              <strong>{event.label}</strong>
              <span>{event.detail || event.status}</span>
            </div>
          </div>
        ))}
      </div>}
      {mergedTools.length > 0 && <div className="tool-call-list">
        {mergedTools.map((tool) => (
          <div className="tool-call" key={tool.id}>
            <Wrench size={13} />
            <div>
              <strong>{_canonicalizeToolName(tool.name)}{tool.source === "inline" ? " (detected)" : ""}</strong>
              {(tool.alias_name || (tool.name.startsWith("spacecode.") ? tool.name : "")) && <small>alias: {tool.alias_name || tool.name}</small>}
              <span>{tool.status} / {tool.summary}</span>
            </div>
          </div>
        ))}
      </div>}
      {message.actions && message.actions.length > 0 && <div className="agent-actions">
        {message.actions.map((action) => (
          <button className={action.kind} onClick={() => onRunAction(action)} title={action.summary} key={action.id}>
            {action.kind === "apply" ? <CheckCircle2 size={14} /> : action.kind === "layout" ? <PanelBottom size={14} /> : <Play size={14} />}
            {action.label}
          </button>
        ))}
      </div>}
    </article>
  );
}

function MessageRichText({ content, animate, compact = false }: { content: string; animate: boolean; compact?: boolean }) {
  const lines = content.split("\n");
  const blocks: React.ReactNode[] = [];
  let listItems: string[] = [];
  let listType: "ul" | "ol" = "ul";
  let codeLines: string[] = [];
  let inCode = false;

  const flushList = () => {
    if (!listItems.length) return;
    const children = listItems.map((item, index) => <li key={`i-${index}`}>{renderInlineRichText(item)}</li>);
    blocks.push(listType === "ol" ? <ol key={`l-${blocks.length}`}>{children}</ol> : <ul key={`l-${blocks.length}`}>{children}</ul>);
    listItems = [];
    listType = "ul";
  };

  const flushCode = () => {
    if (!codeLines.length) return;
    blocks.push(<pre key={`c-${blocks.length}`}><code>{codeLines.join("\n")}</code></pre>);
    codeLines = [];
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      flushList();
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        inCode = true;
      }
      return;
    }
    if (inCode) {
      codeLines.push(line);
      return;
    }
    if (/^[-*]\s+/.test(trimmed)) {
      if (listItems.length > 0 && listType !== "ul") flushList();
      listType = "ul";
      listItems.push(trimmed.replace(/^[-*]\s+/, ""));
      return;
    }
    if (/^\d+[.)]\s+/.test(trimmed)) {
      if (listItems.length > 0 && listType !== "ol") flushList();
      listType = "ol";
      listItems.push(trimmed.replace(/^\d+[.)]\s+/, ""));
      return;
    }
    flushList();
    if (!trimmed) {
      blocks.push(<div className="rich-spacer" key={`s-${blocks.length}`} />);
      return;
    }
    if (/^###\s+/.test(trimmed)) {
      blocks.push(<h4 key={`h4-${blocks.length}`}>{renderInlineRichText(trimmed.replace(/^###\s+/, ""))}</h4>);
      return;
    }
    if (/^##\s+/.test(trimmed)) {
      blocks.push(<h3 key={`h3-${blocks.length}`}>{renderInlineRichText(trimmed.replace(/^##\s+/, ""))}</h3>);
      return;
    }
    if (/^#\s+/.test(trimmed)) {
      blocks.push(<h2 key={`h2-${blocks.length}`}>{renderInlineRichText(trimmed.replace(/^#\s+/, ""))}</h2>);
      return;
    }
    blocks.push(<p key={`p-${blocks.length}`}>{renderInlineRichText(line)}</p>);
  });

  flushList();
  if (inCode) flushCode();

  return (
    <div className={`chat-rich ${animate ? "animate" : ""} ${compact ? "compact" : ""}`}>
      {blocks.map((block, index) => <div className="rich-block" style={{ animationDelay: `${index * 35}ms` }} key={`b-${index}`}>{block}</div>)}
    </div>
  );
}

function BottomPanel({ run, status, job, onControl, terminalCommand, terminalRunning, terminalResult, onTerminalCommandChange, onTerminalRun }: {
  run: MissionRun | undefined;
  status: string;
  job: HandoffJob | null;
  onControl: (action: "cancel" | "pause" | "resume") => void;
  terminalCommand: string;
  terminalRunning: boolean;
  terminalResult: TerminalRunResult | null;
  onTerminalCommandChange: (value: string) => void;
  onTerminalRun: () => void;
}) {
  const running = job ? ["starting", "running"].includes(job.status) : false;
  const paused = job?.status === "paused";
  const iterationLines = job ? extractIterationLines(job.logs) : [];
  const [activeTab, setActiveTab] = useState<"timeline" | "terminal" | "output">("terminal");
  return (
    <section className="bottom-panel">
      <div className="bottom-tabs">
        <button type="button" className={activeTab === "timeline" ? "active" : ""} onClick={() => setActiveTab("timeline")}>
          <PanelBottom size={14} /> Timeline
        </button>
        <button type="button" className={activeTab === "terminal" ? "active" : ""} onClick={() => setActiveTab("terminal")}>
          <TerminalSquare size={14} /> Terminal
        </button>
        <button type="button" className={activeTab === "output" ? "active" : ""} onClick={() => setActiveTab("output")}>
          <Clock3 size={14} /> Output
        </button>
      </div>
      <div className="bottom-content">
        {activeTab === "terminal" && (
          <>
            <div className="terminal-line"><span>nemo</span> {status}</div>
            <div className="job-console">
              <div className="job-iteration-title">Terminal real</div>
              <div className="repo-open-row" style={{ padding: "6px 12px", gridTemplateColumns: "minmax(0, 1fr) auto", marginBottom: 0 }}>
                <input
                  value={terminalCommand}
                  onChange={(event) => onTerminalCommandChange(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      onTerminalRun();
                    }
                  }}
                  placeholder="git status --short"
                />
                <button className="repo-item" onClick={onTerminalRun} disabled={terminalRunning}>
                  {terminalRunning ? "Running" : "Run"}
                </button>
              </div>
              {terminalResult && <div className="mini-list" style={{ padding: "0 12px 8px" }}>
                <span>exit: {terminalResult.exit_code ?? "timeout"} / duration: {terminalResult.duration_ms} ms</span>
                <span>cwd: {terminalResult.cwd}</span>
              </div>}
              <div className="job-iteration-title">Salida</div>
              <pre>{terminalResult ? `${terminalResult.stdout || ""}${terminalResult.stderr ? `\n${terminalResult.stderr}` : ""}`.trim() || "(no output)" : "Ejecuta un comando para ver salida."}</pre>
            </div>
            {job ? <div className="job-console">
              <div className="job-header">
                <strong>{job.job_id}</strong>
                <span>{job.status}{job.returncode !== null ? ` returncode=${job.returncode}` : ""}</span>
                <div>
                  <button disabled={!running} onClick={() => onControl("pause")}>Pause</button>
                  <button disabled={!paused} onClick={() => onControl("resume")}>Resume</button>
                  <button disabled={!running} onClick={() => onControl("cancel")}>Cancel</button>
                </div>
              </div>
            </div> : null}
          </>
        )}

        {activeTab === "output" && (
          <>
            {job ? <div className="job-console">
              {iterationLines.length > 0 ? (
                <>
                  <div className="job-iteration-title">Iteration output ({iterationLines.length})</div>
                  <pre>{iterationLines.join("\n")}</pre>
                </>
              ) : null}
              <div className="job-iteration-title">Full live output</div>
              <pre>{job.logs.slice(-300).join("\n") || "No logs yet."}</pre>
            </div> : <EmptyState />}
          </>
        )}

        {activeTab === "timeline" && (
          <>
            {run?.timeline.length ? run.timeline.map((event, index) => (
              <div className="timeline-row" key={`${event.sequence ?? index}-${event.kind}`}>
                <span>{event.sequence ?? index + 1}</span>
                <strong>{event.kind ?? "event"}</strong>
                <p>{event.summary ?? event.phase ?? "No summary"}{event.payload_ref ? ` / ${event.payload_ref}` : ""}</p>
              </div>
            )) : <EmptyState />}
          </>
        )}
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

function RunsSkeleton() {
  return (
    <div className="runs-skeleton">
      {[1, 2, 3].map((n) => (
        <div key={n} className="runs-skeleton-row" />
      ))}
    </div>
  );
}

function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="tooltip-wrap">
      {children}
      <span className="tooltip-bubble">{text}</span>
    </span>
  );
}

function EmptyState() {
  return <div className="empty">No live workspace data available.</div>;
}

const rootElement = document.getElementById("root");
if (rootElement) {
  type MissionControlWindow = Window & { __missionControlRoot?: ReturnType<typeof createRoot> };
  const missionControlWindow = window as MissionControlWindow;
  const missionControlRoot = missionControlWindow.__missionControlRoot ?? createRoot(rootElement);
  missionControlWindow.__missionControlRoot = missionControlRoot;
  missionControlRoot.render(<App />);
}
