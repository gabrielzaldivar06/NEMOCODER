import { useEffect, useRef, useState } from "react";

interface TimelineEvent {
  kind: string;
  summary: string;
  phase: string;
  sequence: number;
  ts: string;
  payload?: Record<string, unknown>;
}

interface AuditEntry {
  ts: string;
  outcome: string;
  tool_call_audit: {
    tool: string;
    risk: string;
    allowed: boolean;
    reason?: string;
  };
  tool_args?: Record<string, unknown>;
}

interface Props {
  jobId: string;
  isLive: boolean;
  jobStatus?: string;
  onStreamEnd?: () => void;
}

// ── Pipeline stage definitions ────────────────────────────────────────────────

const PIPELINE_STAGES = [
  { id: "context",  label: "Contexto", icon: "🧠", kinds: ["context_bootstrapped"] },
  { id: "plan",     label: "Plan",     icon: "📋", kinds: ["plan_created", "subtask_start"] },
  { id: "execute",  label: "Ejecutar", icon: "⚙",  kinds: ["mutation_created", "tool_use"] },
  { id: "validate", label: "Validar",  icon: "✓",  kinds: ["validation_run"] },
  { id: "review",   label: "Review",   icon: "📦", kinds: ["review_package_created", "checkpoint"] },
] as const;

type StageId = typeof PIPELINE_STAGES[number]["id"];
type StageState = "waiting" | "active" | "done" | "error";

interface StageDetail {
  state: StageState;
  eventCount: number;
  firstTs: string | null;
  lastTs: string | null;
  lastSummary: string | null;
}

function highestStageIdx(events: TimelineEvent[]): number {
  let max = -1;
  for (const ev of events) {
    for (let i = 0; i < PIPELINE_STAGES.length; i++) {
      if ((PIPELINE_STAGES[i].kinds as readonly string[]).includes(ev.kind)) {
        if (i > max) max = i;
      }
    }
  }
  return max;
}

function computeStageDetails(
  events: TimelineEvent[],
  isLive: boolean,
  jobStatus: string,
): Record<StageId, StageDetail> {
  const isFailed = jobStatus === "failed" || jobStatus === "error";
  const isDone = jobStatus === "completed" || (!isLive && !isFailed);
  const maxIdx = highestStageIdx(events);

  const details = Object.fromEntries(
    PIPELINE_STAGES.map((s) => [s.id, { state: "waiting" as StageState, eventCount: 0, firstTs: null, lastTs: null, lastSummary: null }])
  ) as Record<StageId, StageDetail>;

  // Assign state
  if (maxIdx < 0) {
    if (isLive) details["context"].state = "active";
  } else {
    for (let i = 0; i < PIPELINE_STAGES.length; i++) {
      const id = PIPELINE_STAGES[i].id;
      if (i < maxIdx) {
        details[id].state = "done";
      } else if (i === maxIdx) {
        if (isFailed) details[id].state = "error";
        else if (isDone || !isLive) details[id].state = "done";
        else details[id].state = "active";
      }
    }
    if (isDone) {
      for (let i = 0; i <= maxIdx; i++) {
        details[PIPELINE_STAGES[i].id].state = "done";
      }
    }
  }

  // Accumulate per-stage stats
  for (const ev of events) {
    for (let i = 0; i < PIPELINE_STAGES.length; i++) {
      if ((PIPELINE_STAGES[i].kinds as readonly string[]).includes(ev.kind)) {
        const d = details[PIPELINE_STAGES[i].id];
        d.eventCount++;
        if (!d.firstTs) d.firstTs = ev.ts;
        d.lastTs = ev.ts;
        d.lastSummary = ev.summary;
        break;
      }
    }
  }

  return details;
}

function stageDuration(firstTs: string | null, lastTs: string | null): string | null {
  if (!firstTs || !lastTs) return null;
  try {
    const ms = new Date(lastTs).getTime() - new Date(firstTs).getTime();
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
  } catch { return null; }
}

function PipelineBar({
  events,
  isLive,
  jobStatus,
}: {
  events: TimelineEvent[];
  isLive: boolean;
  jobStatus: string;
}) {
  const details = computeStageDetails(events, isLive, jobStatus);
  const activeStage = PIPELINE_STAGES.find((s) => details[s.id].state === "active");
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  return (
    <div className="handoff-pipeline">
      <div className="handoff-pipeline-track">
        {PIPELINE_STAGES.map((stage, idx) => {
          const d = details[stage.id];
          const dur = stageDuration(d.firstTs, d.lastTs);
          return (
            <div key={stage.id} className="handoff-pipeline-step">
              <div className={`handoff-pipeline-card ${d.state}`}>
                <div className="pipeline-card-top">
                  <span className="pipeline-card-num">{idx + 1}</span>
                  <span className="pipeline-card-icon">
                    {d.state === "done" ? "✓" : d.state === "error" ? "✕" : stage.icon}
                  </span>
                </div>
                <div className="pipeline-card-label">{stage.label}</div>
                {d.state === "done" && d.eventCount > 0 && (
                  <div className="pipeline-card-meta">
                    <span className="pipeline-card-count">{d.eventCount} ev</span>
                    {dur && <span className="pipeline-card-dur">{dur}</span>}
                  </div>
                )}
                {d.state === "active" && d.lastSummary && (
                  <div className="pipeline-card-activity">{d.lastSummary}</div>
                )}
                {d.state === "error" && d.lastSummary && (
                  <div className="pipeline-card-activity error">{d.lastSummary}</div>
                )}
              </div>
              {idx < PIPELINE_STAGES.length - 1 && (
                <div className={`handoff-pipeline-connector ${d.state === "done" ? "done" : d.state === "active" ? "active" : ""}`} />
              )}
            </div>
          );
        })}
      </div>
      {(activeStage || lastEvent) && (
        <div className="handoff-pipeline-status">
          {activeStage && <span className="pipeline-status-dot active" />}
          <span className="pipeline-status-text">
            {activeStage
              ? `${activeStage.label}: ${details[activeStage.id].lastSummary ?? "procesando…"}`
              : lastEvent?.summary}
          </span>
        </div>
      )}
    </div>
  );
}

// ── Existing components ───────────────────────────────────────────────────────

const KIND_ICON: Record<string, string> = {
  context_bootstrapped: "🧠",
  plan_created: "📋",
  subtask_start: "→",
  mutation_created: "⚙",
  validation_run: "✓",
  checkpoint: "💾",
  review_package_created: "📦",
  heartbeat: "◉",
  paused: "⏸",
  resumed: "▶",
  permission_decided: "🔒",
  tool_use: "🔧",
  nemo_guided_repair_start: "🧭",
  nemo_guided_repair_succeeded: "🌟",
  nemo_guided_repair_failed: "✖",
  nemo_guided_repair_no_strategy: "·",
  repair_quality_score: "📊",
};

const RISK_COLOR: Record<string, string> = {
  read_only: "#4ade80",
  memory_write: "#facc15",
  destructive: "#f87171",
  scheduling_write: "#fb923c",
  unknown: "#94a3b8",
};

function phaseLabel(phase: string) {
  return phase === "plan" ? "plan" : phase === "review" ? "review" : "execute";
}

function formatTs(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const { tool_call_audit: audit, outcome, ts } = entry;
  const riskColor = RISK_COLOR[audit.risk] ?? RISK_COLOR.unknown;
  const icon = outcome === "allowed" ? "🔧" : outcome === "denied" ? "🚫" : "·";
  return (
    <div className={`timeline-event tool-audit-event ${outcome}`}>
      <span className="timeline-event-icon">{icon}</span>
      <span className="tool-audit-risk" style={{ color: riskColor }} title={audit.risk}>
        {audit.risk.replace("_", " ")}
      </span>
      <div className="timeline-event-body">
        <span className="timeline-event-summary">{audit.tool}</span>
        <span className="timeline-event-meta">{formatTs(ts)}</span>
      </div>
    </div>
  );
}

export function TimelinePanel({ jobId, isLive, jobStatus = "", onStreamEnd }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [live, setLive] = useState(isLive);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [auditOpen, setAuditOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    setLive(isLive);
    setStreamError(null);

    if (isLive) {
      let currentEs: EventSource | null = null;
      let cancelled = false;
      let attempts = 0;
      const maxAttempts = 3;
      const delays = [2000, 5000, 10000];

      function connect() {
        if (cancelled) return;
        const es = new EventSource(`/api/run/${jobId}/timeline/stream`);
        currentEs = es;
        let streamEndedCleanly = false;
        es.onmessage = (e) => {
          try {
            const event: TimelineEvent = JSON.parse(e.data);
            if (event.kind === "stream_end") {
              streamEndedCleanly = true;
              setLive(false);
              es.close();
              onStreamEnd?.();
              return;
            }
            setEvents((prev) => [...prev, event]);
          } catch {
            // ignore malformed SSE data
          }
        };
        es.onerror = () => {
          setLive(false);
          es.close();
          if (!streamEndedCleanly && !cancelled && attempts < maxAttempts) {
            const delay = delays[attempts++];
            setTimeout(connect, delay);
          } else if (!streamEndedCleanly && !cancelled) {
            setStreamError(`Conexión perdida (${attempts} intentos). Recarga para ver actualizaciones en vivo.`);
            onStreamEnd?.();
          }
        };
      }

      connect();
      return () => {
        cancelled = true;
        currentEs?.close();
      };
    } else {
      fetch(`/api/run/${jobId}/timeline`)
        .then((r) => r.json())
        .then((data) => {
          if (Array.isArray(data.timeline)) setEvents(data.timeline);
        })
        .catch(() => {});
    }
  }, [jobId, isLive]);

  // Poll MCP audit when the section is open, filtered by this job's time window
  useEffect(() => {
    if (!auditOpen) return;
    const fetchAudit = () => {
      fetch(`/api/mcp/audit?limit=40&job_id=${encodeURIComponent(jobId)}`)
        .then((r) => r.json())
        .then((data) => {
          if (Array.isArray(data.entries)) setAudit(data.entries);
        })
        .catch(() => {});
    };
    fetchAudit();
    const id = setInterval(fetchAudit, live ? 5000 : 30000);
    return () => clearInterval(id);
  }, [auditOpen, live, jobId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  return (
    <div className="timeline-panel">
      <div className="timeline-panel-header">
        <span>Timeline</span>
        {live && <span className="timeline-live-dot" title="Live" />}
      </div>

      <PipelineBar events={events} isLive={live} jobStatus={jobStatus} />

      {streamError && (
        <div className="timeline-stream-error">{streamError}</div>
      )}
      <div className="timeline-events" ref={scrollRef}>
        {events.length === 0 && (
          <div className="timeline-empty">
            {live ? "Esperando eventos…" : "Sin eventos registrados."}
          </div>
        )}
        {events.map((ev) => {
          const isNemoGuided = ev.kind === "nemo_guided_repair_start";
          const strategyLines = isNemoGuided
            ? ((ev.payload?.strategy_lines as string[] | undefined) ?? null)
            : null;
          const memoriesFound = isNemoGuided
            ? Number(ev.payload?.memories_found ?? 0)
            : 0;
          const qualityScore = ev.kind === "repair_quality_score"
            ? Number(ev.payload?.score ?? 0)
            : null;
          const stopReason = ev.kind === "repair_quality_score"
            ? String(ev.payload?.stop_reason ?? "")
            : null;
          return (
            <div className={`timeline-event ${ev.kind}`} key={`${ev.sequence}-${ev.kind}`}>
              <span className="timeline-event-icon">{KIND_ICON[ev.kind] ?? "·"}</span>
              <span className={`timeline-phase-badge ${phaseLabel(ev.phase)}`}>
                {phaseLabel(ev.phase)}
              </span>
              <div className="timeline-event-body">
                <span className="timeline-event-summary">{ev.summary}</span>
                <span className="timeline-event-meta">{formatTs(ev.ts)}</span>
                {isNemoGuided && strategyLines && strategyLines.length > 0 && (
                  <div className="timeline-nemo-strategy">
                    <div className="timeline-nemo-strategy-header">
                      🧭 NEMO recovery strategy — {memoriesFound} memoria{memoriesFound === 1 ? "" : "s"}
                    </div>
                    <ul>
                      {strategyLines.slice(0, 5).map((line, i) => (
                        <li key={i}>{line.replace(/^-\s*/, "")}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {qualityScore !== null && (
                  <div className={`timeline-quality-score ${qualityScore >= 7 ? "good" : qualityScore >= 5 ? "mid" : "low"}`}>
                    <span className="quality-score-bar">
                      <span className="quality-score-fill" style={{ width: `${Math.min(100, qualityScore * 10)}%` }} />
                    </span>
                    <span className="quality-score-value">{qualityScore.toFixed(1)}/10</span>
                    {stopReason && <span className="quality-score-reason">{stopReason}</span>}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="tool-audit-section">
        <button
          className="tool-audit-toggle"
          onClick={() => setAuditOpen((v) => !v)}
        >
          <span>🔧 MCP Tool Activity</span>
          <span className="tool-audit-chevron">{auditOpen ? "▲" : "▼"}</span>
        </button>
        {auditOpen && (
          <div className="tool-audit-list">
            {audit.length === 0 ? (
              <div className="timeline-empty">Sin actividad MCP reciente.</div>
            ) : (
              audit.map((entry, i) => <AuditRow key={i} entry={entry} />)
            )}
          </div>
        )}
      </div>
    </div>
  );
}
