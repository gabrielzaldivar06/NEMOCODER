import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, AlertTriangle, Wrench, Coins, GitBranch } from "lucide-react";

interface ScorecardData {
  job_id: string;
  available: boolean;
  readiness?: {
    score: number | null;
    grade: string | null;
    reasons: string[];
    validation_passed: boolean | null;
    has_checkpoint: boolean | null;
    has_review_package: boolean | null;
    memory_writeback_present: boolean | null;
    mutation_present: boolean | null;
  };
  validation?: {
    passed: boolean;
    failed: Array<{ command: string | null; status: string | null; returncode: number | null }>;
    total_checks: number;
  };
  repair?: {
    attempts_used: number;
    budget: number | null;
    stop_reason: string | null;
    best_score: number | null;
    tokens_consumed: number | null;
    attempt_reasons: string[];
  };
  subtasks?: Array<{ index: number | null; total: number | null; title: string | null; ts: string | null }>;
  quality_scores?: Array<{ score: number | null; stop_reason: string | null; ts: string | null }>;
  changed_files_count?: number;
}

interface Props {
  jobId: string;
  jobStatus: string;
}

function gradeTone(grade: string | null | undefined): string {
  if (grade === "ready") return "good";
  if (grade === "needs_review") return "mid";
  if (grade === "blocked") return "low";
  return "neutral";
}

function scoreTone(score: number | null | undefined): string {
  if (score == null) return "neutral";
  if (score >= 7) return "good";
  if (score >= 5) return "mid";
  return "low";
}

export function QualityScorecard({ jobId, jobStatus }: Props) {
  const [data, setData] = useState<ScorecardData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/run/${jobId}/quality-scorecard`);
        const json: ScorecardData = await res.json();
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) setData(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    // Re-fetch while job is still running to surface progress
    if (jobStatus === "running" || jobStatus === "starting") {
      const id = setInterval(load, 12_000);
      return () => {
        cancelled = true;
        clearInterval(id);
      };
    }
    return () => { cancelled = true; };
  }, [jobId, jobStatus]);

  if (!jobId) return null;
  if (loading && !data) return <div className="quality-scorecard loading">Loading scorecard…</div>;
  if (!data?.available) return null;

  const readiness = data.readiness ?? {} as NonNullable<ScorecardData["readiness"]>;
  const validation = data.validation ?? { passed: false, failed: [], total_checks: 0 };
  const repair = data.repair ?? { attempts_used: 0, budget: null, stop_reason: null, best_score: null, tokens_consumed: null, attempt_reasons: [] };
  const subtasks = data.subtasks ?? [];
  const qualityScores = data.quality_scores ?? [];

  return (
    <div className="quality-scorecard">
      <div className="qs-header">
        <span className="qs-title">Quality scorecard</span>
        {readiness.grade && (
          <span className={`qs-grade-pill ${gradeTone(readiness.grade)}`}>
            {readiness.grade.replace("_", " ")}
          </span>
        )}
      </div>

      {/* Readiness checklist */}
      <div className="qs-section">
        <div className="qs-section-label">Readiness</div>
        <div className="qs-readiness-grid">
          <ReadinessRow label="Validation" pass={readiness.validation_passed} />
          <ReadinessRow label="Checkpoint" pass={readiness.has_checkpoint} />
          <ReadinessRow label="Review pkg" pass={readiness.has_review_package} />
          <ReadinessRow label="Memory writeback" pass={readiness.memory_writeback_present} />
          <ReadinessRow label="Mutation" pass={readiness.mutation_present} />
        </div>
        {readiness.score != null && (
          <div className={`qs-score-bar ${scoreTone(readiness.score * 10)}`}>
            <span className="qs-score-fill" style={{ width: `${Math.round(readiness.score * 100)}%` }} />
            <span className="qs-score-label">{Math.round(readiness.score * 100)}%</span>
          </div>
        )}
      </div>

      {/* Validation summary */}
      {validation.total_checks > 0 && (
        <div className="qs-section">
          <div className="qs-section-label">
            Validation · {validation.passed ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
            <span className="qs-section-aux">{validation.total_checks - validation.failed.length}/{validation.total_checks} passed</span>
          </div>
          {validation.failed.slice(0, 3).map((f, i) => (
            <div key={i} className="qs-failed-cmd" title={f.command ?? ""}>
              <code>{f.command?.slice(0, 60) ?? "—"}</code>
              <span className="qs-failed-rc">rc={f.returncode ?? "?"}</span>
            </div>
          ))}
        </div>
      )}

      {/* Repair */}
      {(repair.attempts_used > 0 || repair.stop_reason || repair.best_score != null) && (
        <div className="qs-section">
          <div className="qs-section-label"><Wrench size={11} /> Repair</div>
          <div className="qs-repair-row">
            <span>
              Attempts <strong>{repair.attempts_used}{repair.budget ? `/${repair.budget}` : ""}</strong>
            </span>
            {repair.best_score != null && (
              <span className={`qs-best-score ${scoreTone(repair.best_score)}`}>
                Best score <strong>{repair.best_score.toFixed(1)}/10</strong>
              </span>
            )}
            {repair.tokens_consumed != null && repair.tokens_consumed > 0 && (
              <span><Coins size={10} /> {repair.tokens_consumed.toLocaleString()} tok</span>
            )}
          </div>
          {repair.stop_reason && (
            <div className="qs-stop-reason">
              <AlertTriangle size={10} /> Stop: <code>{repair.stop_reason}</code>
            </div>
          )}
        </div>
      )}

      {/* Quality score evolution */}
      {qualityScores.length > 0 && (
        <div className="qs-section">
          <div className="qs-section-label">Critique scores over attempts</div>
          <div className="qs-score-sparkline">
            {qualityScores.map((q, i) => {
              const v = typeof q.score === "number" ? q.score : 0;
              return (
                <span
                  key={i}
                  className={`qs-spark-bar ${scoreTone(v)}`}
                  style={{ height: `${Math.max(8, Math.round(v * 10))}%` }}
                  title={`${v.toFixed(1)}/10${q.stop_reason ? ` — ${q.stop_reason}` : ""}`}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* Subtasks */}
      {subtasks.length > 0 && (
        <div className="qs-section">
          <div className="qs-section-label">
            <GitBranch size={11} /> Subtasks <span className="qs-section-aux">{subtasks.length}</span>
          </div>
          <ol className="qs-subtask-list">
            {subtasks.map((st, i) => (
              <li key={i}>
                <span className="qs-st-idx">{st.index ?? i + 1}/{st.total ?? subtasks.length}</span>
                <span className="qs-st-title">{st.title ?? "—"}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function ReadinessRow({ label, pass }: { label: string; pass: boolean | null | undefined }) {
  const icon = pass === true ? <CheckCircle2 size={11} /> : pass === false ? <XCircle size={11} /> : <AlertTriangle size={11} />;
  const cls = pass === true ? "good" : pass === false ? "low" : "neutral";
  return (
    <div className={`qs-readiness-row ${cls}`}>
      {icon}<span>{label}</span>
    </div>
  );
}
