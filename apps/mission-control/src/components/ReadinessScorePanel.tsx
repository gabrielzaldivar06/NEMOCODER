import { useRef, useState } from "react";

interface ReadinessScore {
  score: number;
  grade: string;
  validation_passed: boolean;
  has_checkpoint: boolean;
  has_review_package: boolean;
  memory_writeback_present: boolean;
  mutation_present: boolean;
  reasons: string[];
}

interface ReplayCheck {
  index: number;
  command: string;
  original_status: string;
  replay_status: string;
  output: string;
}

interface ReplayResult {
  original_score: number;
  original_grade: string;
  replay_score: number;
  replay_grade: string;
  score_delta: number;
  sandbox_available: boolean;
  checks_run: number;
  checks_passed: number;
  checks: ReplayCheck[];
}

interface Props {
  readiness: ReadinessScore | null | undefined;
  runId?: string | null;
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className={`readiness-check ${ok ? "pass" : "fail"}`}>
      <span className="readiness-check-icon">{ok ? "✓" : "✗"}</span>
      <span className="readiness-check-label">{label}</span>
    </div>
  );
}

function gradeColor(grade: string) {
  if (grade === "ready") return "#22c55e";
  if (grade === "needs_review") return "#f59e0b";
  return "#ef4444";
}

export function ReadinessScorePanel({ readiness, runId }: Props) {
  const [replayState, setReplayState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const checksRef = useRef<ReplayCheck[]>([]);
  const esRef = useRef<EventSource | null>(null);

  function runReplay() {
    if (!runId || replayState === "running") return;
    setReplayState("running");
    setReplayResult(null);
    setReplayError(null);
    checksRef.current = [];

    const es = new EventSource(`/api/run/${encodeURIComponent(runId)}/replay`);
    esRef.current = es;

    es.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (data.type === "check") {
          checksRef.current = [...checksRef.current, data as ReplayCheck];
        } else if (data.type === "done") {
          setReplayResult({ ...data, checks: checksRef.current });
          setReplayState("done");
          es.close();
        } else if (data.type === "error") {
          setReplayError(data.error ?? "unknown error");
          setReplayState("error");
          es.close();
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      setReplayError("Connection lost");
      setReplayState("error");
      es.close();
    };
  }

  if (!readiness) {
    return (
      <div className="readiness-score-panel empty">
        No readiness data for this run.
      </div>
    );
  }

  const pct = Math.round(readiness.score * 100);
  const checksCount = [
    readiness.validation_passed,
    readiness.has_checkpoint,
    readiness.has_review_package,
    readiness.memory_writeback_present,
    readiness.mutation_present,
  ].filter(Boolean).length;

  return (
    <div className="readiness-score-panel">
      <div className="readiness-header">
        <span className="readiness-title">Autonomy Scorecard</span>
        <span className={`readiness-grade grade-${readiness.grade}`}>{readiness.grade}</span>
      </div>
      <div className="readiness-score-row">
        <span className="readiness-score-value">{checksCount}/5</span>
        <div className="readiness-bar">
          <div className="readiness-bar-fill" style={{ width: `${pct}%`, background: gradeColor(readiness.grade) }} />
        </div>
        <span className="readiness-pct">{pct}%</span>
      </div>
      <div className="readiness-checks">
        <Check ok={readiness.validation_passed} label="Validation" />
        <Check ok={readiness.has_checkpoint} label="Checkpoint" />
        <Check ok={readiness.has_review_package} label="Review package" />
        <Check ok={readiness.memory_writeback_present} label="Memory writeback" />
        <Check ok={readiness.mutation_present} label="Mutation" />
      </div>
      {readiness.reasons.length > 0 && (
        <div className="readiness-reasons">
          {readiness.reasons.map((r) => (
            <span key={r} className="readiness-reason">{r.replace(/_/g, " ")}</span>
          ))}
        </div>
      )}

      {runId && (
        <div className="replay-section">
          <div className="replay-header">
            <span className="replay-title">Replay Validation</span>
            <button
              className={`replay-btn ${replayState}`}
              onClick={runReplay}
              disabled={replayState === "running"}
            >
              {replayState === "running" ? "Running…" : replayState === "done" ? "Re-run" : "Run Replay"}
            </button>
          </div>

          {replayState === "running" && (
            <div className="replay-running">
              <span className="replay-spinner" />
              Re-executing validation commands…
            </div>
          )}

          {replayState === "error" && replayError && (
            <div className="replay-error">{replayError}</div>
          )}

          {replayState === "done" && replayResult && (
            <div className="replay-result">
              <div className="replay-scores">
                <div className="replay-score-item">
                  <span className="replay-score-label">Original</span>
                  <span className="replay-score-val" style={{ color: gradeColor(replayResult.original_grade) }}>
                    {Math.round(replayResult.original_score * 100)}%
                  </span>
                  <span className={`replay-grade-chip grade-${replayResult.original_grade}`}>
                    {replayResult.original_grade}
                  </span>
                </div>
                <span className="replay-arrow">→</span>
                <div className="replay-score-item">
                  <span className="replay-score-label">Replay</span>
                  <span className="replay-score-val" style={{ color: gradeColor(replayResult.replay_grade) }}>
                    {Math.round(replayResult.replay_score * 100)}%
                  </span>
                  <span className={`replay-grade-chip grade-${replayResult.replay_grade}`}>
                    {replayResult.replay_grade}
                  </span>
                </div>
                {replayResult.score_delta !== 0 && (
                  <span className={`replay-delta ${replayResult.score_delta > 0 ? "positive" : "negative"}`}>
                    {replayResult.score_delta > 0 ? "+" : ""}{Math.round(replayResult.score_delta * 100)}%
                  </span>
                )}
              </div>

              {!replayResult.sandbox_available && (
                <div className="replay-no-sandbox">Sandbox unavailable — validation skipped</div>
              )}

              {replayResult.checks.length > 0 && (
                <div className="replay-checks">
                  {replayResult.checks.map((c) => (
                    <div key={c.index} className={`replay-check-row ${c.replay_status}`}>
                      <span className={`replay-check-icon ${c.replay_status}`}>
                        {c.replay_status === "passed" ? "✓" : c.replay_status === "skipped" ? "—" : "✗"}
                      </span>
                      <span className="replay-check-cmd" title={c.command}>
                        {c.command.length > 40 ? c.command.slice(0, 40) + "…" : c.command}
                      </span>
                      {c.original_status !== c.replay_status && (
                        <span className="replay-check-change">
                          {c.original_status} → {c.replay_status}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
