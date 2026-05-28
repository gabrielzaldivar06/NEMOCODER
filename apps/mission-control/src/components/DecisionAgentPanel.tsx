// apps/mission-control/src/components/DecisionAgentPanel.tsx
import { Activity, CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

export type AgentEvent = {
  seq: number;
  type: string;
  phase?: string;
  detail?: string;
  ts?: string;
  confidence?: number;
  dominant_pattern?: string;
  task_type?: string;
  target_files?: string[];
  proposed_description?: string;
  grade?: string;
  risk_flags?: string[];
  changed_files?: string[];
  run_json?: string;
};

export type DASynthesisReport = {
  dominant_pattern: string;
  task_type: string;
  target_files: string[];
  proposed_description: string;
  confidence: number;
  rationale: string;
};

export type DecisionAgentJobState = {
  job_id: string;
  status: string;
  events: AgentEvent[];
  synthesis_report: DASynthesisReport | null;
  run_json: string | null;
  error: string | null;
  updated_at: string;
};

type Props = {
  job: DecisionAgentJobState | null;
  onRun: () => void;
  onApply: (jobId: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  idle: "Idle",
  analyzing: "Analyzing…",
  generating: "Generating…",
  executing: "Running Aider…",
  awaiting_review: "Awaiting Review",
  completed: "Completed",
  failed: "Failed",
};

const _PHASE_ORDER = ["analyzing", "generating", "executing", "awaiting_review"] as const;
const _PHASE_LABELS = ["Analyze", "Generate", "Execute", "Review"] as const;

function phaseIndex(status: string): number {
  return _PHASE_ORDER.indexOf(status as (typeof _PHASE_ORDER)[number]);
}

function StatusIcon({ status }: { status: string }) {
  if (status === "idle") return <Circle size={10} className="da-status-icon idle" />;
  if (status === "analyzing" || status === "generating")
    return <Loader2 size={10} className="da-status-icon spin" />;
  if (status === "executing")
    return <Activity size={10} className="da-status-icon pulse" />;
  if (status === "awaiting_review")
    return <CheckCircle2 size={10} className="da-status-icon ready" />;
  if (status === "completed")
    return <CheckCircle2 size={10} className="da-status-icon done" />;
  return <XCircle size={10} className="da-status-icon failed" />;
}

export function DecisionAgentPanel({ job, onRun, onApply }: Props) {
  const status = job?.status ?? "idle";
  const isActive = ["analyzing", "generating", "executing"].includes(status);
  const phaseIdx = phaseIndex(status);
  const recentEvents = (job?.events ?? []).slice(-8);
  const report = job?.synthesis_report;

  return (
    <section className="telemetry-card da-card" aria-label="Decision Agent">
      <header>
        <span>Decision Agent</span>
        <b className={`da-badge ${status}`}>
          <StatusIcon status={status} />
          {STATUS_LABEL[status] ?? status}
        </b>
      </header>

      {/* Phase pipeline */}
      <div className="da-pipeline" role="list" aria-label="Agent phases">
        {_PHASE_LABELS.map((label, idx) => {
          const isDone = phaseIdx > idx;
          const isNow = phaseIdx === idx && isActive;
          return (
            <div key={label} role="listitem"
              className={`da-step ${isDone ? "done" : isNow ? "active" : ""}`}>
              {isDone ? <CheckCircle2 size={8} /> : isNow ? <Loader2 size={8} className="spin" /> : <Circle size={8} />}
              <span>{label}</span>
            </div>
          );
        })}
      </div>

      {/* Synthesis report */}
      {report && (
        <div className="da-report">
          <div className="da-report-pattern">{report.dominant_pattern}</div>
          <div className="da-report-meta">
            <span className="da-tag">{report.task_type}</span>
            <span className="da-confidence">{Math.round(report.confidence * 100)}% conf</span>
          </div>
          {report.target_files.length > 0 && (
            <div className="da-report-files">
              {report.target_files.map((f) => (
                <code key={f}>{f.split("/").slice(-2).join("/")}</code>
              ))}
            </div>
          )}
          <p className="da-report-desc">{report.proposed_description}</p>
        </div>
      )}

      {/* Events timeline */}
      {recentEvents.length > 0 && (
        <ol className="da-events" aria-label="Agent events">
          {recentEvents.map((e) => (
            <li key={e.seq} className={`da-event ${e.type}`}>
              <Circle size={5} />
              <span>{e.detail ?? e.dominant_pattern ?? e.type}</span>
            </li>
          ))}
        </ol>
      )}

      {/* Error */}
      {status === "failed" && job?.error && (
        <p className="da-error">{job.error}</p>
      )}

      {/* Actions */}
      <div className="da-actions">
        {(status === "idle" || status === "completed" || status === "failed") && (
          <button className="da-btn run" onClick={onRun}>
            Run Analysis
          </button>
        )}
        {status === "awaiting_review" && job && (
          <button className="da-btn apply" onClick={() => onApply(job.job_id)}>
            Apply Changes
          </button>
        )}
      </div>
    </section>
  );
}
