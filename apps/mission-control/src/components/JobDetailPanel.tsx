import { X, GitBranch, Clock, CheckCircle2, XCircle } from "lucide-react";
import { TimelinePanel } from "./TimelinePanel";
import { QualityScorecard } from "./QualityScorecard";

type DetailJob = {
  job_id: string; task_id: string; run_id: string;
  status: string; returncode: number | null;
  objective?: string | null;
  permission_request?: {
    categories: string[]; rationale: string;
    requires_user_approval: string[];
    decision?: unknown;
  } | null;
};

type DetailRun = {
  task_id: string; run_id: string; objective: string;
  source_json: string; review_status: string;
  changed_files: string[]; runtime_id: string;
};

interface Props {
  jobId: string;
  jobs: DetailJob[];
  runs: DetailRun[];
  onClose: () => void;
  onMerge: (job: DetailJob, run: DetailRun) => void;
  onApprove: (jobId: string) => void;
  onDeny: (jobId: string) => void;
}

function timeAgo(jobId: string): string {
  const m = jobId.match(/job-(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
  if (!m) return "—";
  const t = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`);
  const diffSec = Math.round((Date.now() - t.getTime()) / 1000);
  if (diffSec < 60) return `${diffSec}s`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  return `${Math.floor(diffSec / 3600)}h`;
}

export function JobDetailPanel({ jobId, jobs, runs, onClose, onMerge, onApprove, onDeny }: Props) {
  const job = jobs.find((j) => j.job_id === jobId);
  const run = job ? runs.find((r) => r.task_id === job.task_id && r.run_id === job.run_id) : undefined;

  if (!job) return null;

  const needsApproval =
    (job.permission_request?.requires_user_approval?.length ?? 0) > 0 &&
    !job.permission_request?.decision;
  const canMerge = job.status === "completed" && run?.review_status === "awaiting_review";
  const isSuccess = job.status === "completed" && (job.returncode === 0 || job.returncode === null);
  const isLive = job.status === "running" || job.status === "starting";
  const objective = job.objective ?? run?.objective ?? job.job_id;
  const statusClass = needsApproval ? "warn" : isSuccess ? "success" : isLive ? "running" : "error";

  return (
    <div className="job-detail-panel">
      <div className="job-detail-header">
        <span className="job-detail-title" title={objective}>
          {objective.length > 44 ? objective.slice(0, 44) + "…" : objective}
        </span>
        <button className="job-detail-close" onClick={onClose} aria-label="Cerrar panel">
          <X size={14} />
        </button>
      </div>

      <div className="job-detail-status-row">
        <span className={`job-detail-status-chip ${statusClass}`}>
          {isSuccess && !needsApproval && <><CheckCircle2 size={10} /> Éxito</>}
          {needsApproval && <><Clock size={10} /> Esperando OK</>}
          {job.status === "running" && <><Clock size={10} /> Corriendo</>}
          {!isSuccess && !needsApproval && job.status !== "running" && <><XCircle size={10} /> Falló</>}
        </span>
        <span className="job-detail-duration"><Clock size={9} /> {timeAgo(job.job_id)}</span>
        {run?.runtime_id && (
          <span className="job-detail-branch">
            <GitBranch size={9} /> {run.runtime_id.slice(0, 12)}
          </span>
        )}
      </div>

      {run && run.changed_files.length > 0 && (
        <div className="job-detail-files">
          <div className="job-detail-section-label">Archivos cambiados ({run.changed_files.length})</div>
          {run.changed_files.map((f) => (
            <div key={f} className="job-detail-file-row">
              <span className="job-detail-file-name">{f.split(/[\\/]/).pop()}</span>
              <span className="job-detail-file-path" title={f}>{f}</span>
            </div>
          ))}
        </div>
      )}

      <TimelinePanel jobId={job.job_id} isLive={isLive} jobStatus={job.status} />

      <QualityScorecard jobId={job.job_id} jobStatus={job.status} />

      <div className="job-detail-actions">
        {canMerge && run && (
          <button className="job-detail-btn merge" onClick={() => onMerge(job, run)}>
            Merge a main
          </button>
        )}
        {needsApproval && (
          <>
            <button className="job-detail-btn approve" onClick={() => onApprove(job.job_id)}>Aprobar</button>
            <button className="job-detail-btn deny" onClick={() => onDeny(job.job_id)}>Denegar</button>
          </>
        )}
        {!canMerge && !needsApproval && (
          <span className="job-detail-no-action">Sin acciones disponibles</span>
        )}
      </div>
    </div>
  );
}
