import { useEffect, useRef, useState } from "react";

interface TimelineEvent {
  kind: string;
  summary: string;
  phase: string;
  sequence: number;
  ts: string;
  payload?: Record<string, unknown>;
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

export function TimelinePanel({ jobId, isLive, onStreamEnd }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [live, setLive] = useState(isLive);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    setLive(isLive);

    if (isLive) {
      const es = new EventSource(`/api/run/${jobId}/timeline/stream`);
      es.onmessage = (e) => {
        try {
          const event: TimelineEvent = JSON.parse(e.data);
          if (event.kind === "stream_end") {
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
      };
      return () => es.close();
    } else {
      // static fetch for completed jobs
      fetch(`/api/run/${jobId}/timeline`)
        .then((r) => r.json())
        .then((data) => {
          if (Array.isArray(data.timeline)) setEvents(data.timeline);
        })
        .catch(() => {});
    }
  }, [jobId, isLive]);

  // auto-scroll to bottom when events arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="timeline-panel">
      <div className="timeline-panel-header">
        <span>Timeline</span>
        {live && <span className="timeline-live-dot" title="Live" />}
      </div>
      <div className="timeline-events">
        {events.length === 0 && (
          <div className="timeline-empty">
            {live ? "Esperando eventos…" : "Sin eventos registrados."}
          </div>
        )}
        {events.map((ev, i) => (
          <div className="timeline-event" key={i}>
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
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
