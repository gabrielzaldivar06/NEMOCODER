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
  onStreamEnd?: () => void;
}

const KIND_ICON: Record<string, string> = {
  context_bootstrapped: "🧠",
  plan_created: "📋",
  mutation_created: "⚙",
  validation_run: "✓",
  checkpoint: "💾",
  review_package_created: "📦",
  heartbeat: "◉",
  paused: "⏸",
  resumed: "▶",
  permission_decided: "🔒",
  tool_use: "🔧",
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

export function TimelinePanel({ jobId, isLive, onStreamEnd }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [live, setLive] = useState(isLive);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [auditOpen, setAuditOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    setLive(isLive);

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
      <div className="timeline-events" ref={scrollRef}>
        {events.length === 0 && (
          <div className="timeline-empty">
            {live ? "Esperando eventos…" : "Sin eventos registrados."}
          </div>
        )}
        {events.map((ev) => (
          <div className="timeline-event" key={`${ev.sequence}-${ev.kind}`}>
            <span className="timeline-event-icon">{KIND_ICON[ev.kind] ?? "·"}</span>
            <span className={`timeline-phase-badge ${phaseLabel(ev.phase)}`}>
              {phaseLabel(ev.phase)}
            </span>
            <div className="timeline-event-body">
              <span className="timeline-event-summary">{ev.summary}</span>
              <span className="timeline-event-meta">{formatTs(ev.ts)}</span>
            </div>
          </div>
        ))}
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
