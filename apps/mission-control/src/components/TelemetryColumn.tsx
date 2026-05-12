import { ArrowUp, Circle } from "lucide-react";

export type TelemetryRun = {
  objective: string;
  review_status: string;
  source_json: string;
  execution_phase?: string | null;
  runtime_state?: string | null;
  grade?: string | null;
  score?: number | null;
};

type PhaseTone = "idle" | "planning" | "executing" | "reviewing" | "ready" | "blocked";

type PhaseTelemetry = {
  tone: PhaseTone;
  label: string;
  progress: number;
  detail: string;
};

type TelemetryColumnProps<RunType extends TelemetryRun> = {
  activeRun: RunType | undefined;
  visibleQueue: RunType[];
  totalRuns: number;
  readyRuns: number;
  blockedRuns: number;
  queueCount: number;
  running: boolean;
  contextLabel: string;
  memoryAtomCount: number;
  evidenceCount: number;
  feedbackCount: number;
  sourceReads: number;
  sourceCacheHitRate: number;
  status: string;
  onSelectRun: (run: RunType) => void;
  onOpenMemory: () => void;
  onRefreshCognitiveStats: () => void;
  onRefreshMissionStats: () => void;
};

function statusLabel(status: string): string {
  if (status === "approved") return "Approved";
  if (status === "blocked") return "Blocked";
  if (status === "needs_review") return "Review";
  return status || "pending";
}

function toQueueBucket(status: string): "blocked" | "queued" | "ready" {
  if (status === "blocked") return "blocked";
  if (status === "approved") return "ready";
  return "queued";
}

function shortenObjective(value: string, maxLength = 78): string {
  const trimmed = value.trim();
  return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength - 1)}…` : trimmed;
}

function activePhaseTelemetry(run: TelemetryRun | undefined, running: boolean): PhaseTelemetry {
  if (!run) return { tone: "idle", label: "Idle", progress: 8, detail: "awaiting objective" };
  if (run.review_status === "blocked" || run.runtime_state === "blocked" || run.grade === "blocked") return { tone: "blocked", label: "Blocked", progress: 88, detail: run.runtime_state || run.review_status };
  if (run.review_status === "approved" || run.grade === "ready") return { tone: "ready", label: "Ready", progress: 100, detail: run.grade || run.review_status };

  const phase = String(run.execution_phase || run.runtime_state || "").toLowerCase();
  if (phase.includes("review") || run.review_status === "needs_review") return { tone: "reviewing", label: "Review", progress: 78, detail: run.execution_phase || run.review_status };
  if (phase.includes("execut") || phase.includes("run") || running) return { tone: "executing", label: "Execute", progress: 56, detail: run.execution_phase || run.runtime_state || "live" };
  if (phase.includes("plan") || phase.includes("queue")) return { tone: "planning", label: "Plan", progress: 28, detail: run.execution_phase || run.runtime_state || "planning" };
  return { tone: "planning", label: "Plan", progress: 22, detail: run.review_status || "queued" };
}

export function TelemetryColumn<RunType extends TelemetryRun>({ activeRun, visibleQueue, totalRuns, readyRuns, blockedRuns, queueCount, running, contextLabel, memoryAtomCount, evidenceCount, feedbackCount, sourceReads, sourceCacheHitRate, status, onSelectRun, onOpenMemory, onRefreshCognitiveStats, onRefreshMissionStats }: TelemetryColumnProps<RunType>) {
  const queueByBucket = {
    queued: visibleQueue.filter((run) => toQueueBucket(run.review_status) === "queued"),
    ready: visibleQueue.filter((run) => toQueueBucket(run.review_status) === "ready"),
  };
  const queueSummary = `${visibleQueue.length} pendientes, ${queueByBucket.ready.length} por validacion, ${queueByBucket.queued.length} por contexto`;
  const phaseTelemetry = activePhaseTelemetry(activeRun, running);

  return (
    <aside className="mission-telemetry" aria-label="Mission telemetry">
      <section className="telemetry-card active-run-card">
        <header><span>Active run</span><b>{phaseTelemetry.label}</b></header>
        <strong>{activeRun?.objective ?? "Esperando objetivo"}</strong>
        <div className={`phase-progress ${phaseTelemetry.tone}`} aria-label={`Run phase ${phaseTelemetry.label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={phaseTelemetry.progress} role="progressbar">
          <span style={{ width: `${phaseTelemetry.progress}%` }} />
        </div>
        <div className="phase-progress-meta"><span>{phaseTelemetry.detail}</span><b>{phaseTelemetry.progress}%</b></div>
        <div className="signal-wave"><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /></div>
        <div className="telemetry-metrics">
          <span>Runs <b>{totalRuns}</b></span>
          <span>Ready <b>{readyRuns}</b></span>
          <span>Blocked <b>{blockedRuns}</b></span>
          <span>Queue <b>{queueCount}</b></span>
        </div>
        {activeRun && <button onClick={() => onSelectRun(activeRun)}>View run <ArrowUp size={13} /></button>}
      </section>
      <section className="telemetry-card handoff-card">
        <header><span>Handoff chain</span><b>Auto</b></header>
        {[
          ["Planner", "Mission Architect", readyRuns > 0 ? "complete" : "ready"],
          ["Engineer", "Core Executor", running ? "active" : "ready"],
          ["Tester", "Quality Sentinel", blockedRuns > 0 ? "attention" : "waiting"],
          ["Deployer", "Release Steward", "waiting"],
        ].map(([name, detail, stateName]) => <div className={`handoff-row ${stateName}`} key={name}><Circle size={10} /><div><strong>{name}</strong><small>{detail}</small></div><span>{stateName}</span></div>)}
      </section>
      <section className="telemetry-card memory-field-card">
        <header><span>Memory field</span><b>{contextLabel}</b></header>
        <div className="memory-constellation" aria-hidden="true">
          {Array.from({ length: 18 }).map((_, index) => <i key={index} style={{ transform: `rotate(${index * 20}deg) translate(${28 + (index % 4) * 10}px)` }} />)}
        </div>
        <div className="telemetry-metrics memory">
          <span>Atoms <b>{memoryAtomCount}</b></span>
          <span>Evidence <b>{evidenceCount}</b></span>
          <span>Feedback <b>{feedbackCount}</b></span>
          <span>Sources <b>{sourceReads}</b></span>
        </div>
        <button onClick={onOpenMemory}>Explore memory <ArrowUp size={13} /></button>
      </section>
      {visibleQueue.length > 0 && <section className="telemetry-card queue-card">
        <header><span>Approval queue</span><b>{visibleQueue.length}</b></header>
        <p>{queueSummary}</p>
        {visibleQueue.slice(0, 3).map((run) => <button key={run.source_json} onClick={() => onSelectRun(run)}><span>{shortenObjective(run.objective)}</span><b>{statusLabel(run.review_status)}</b></button>)}
      </section>}
      <section className="telemetry-card system-card">
        <header><span>System signal</span><b>{sourceCacheHitRate}% cache</b></header>
        <p>{status}</p>
        <div className="telemetry-actions"><button onClick={onRefreshCognitiveStats}>Refresh memory</button><button onClick={onRefreshMissionStats}>Refresh stats</button></div>
      </section>
    </aside>
  );
}
