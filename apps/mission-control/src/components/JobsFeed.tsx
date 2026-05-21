import { ArrowRight } from "lucide-react";

type FeedJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  status: string;
  returncode: number | null;
  objective?: string | null;
  permission_request?: {
    categories: string[];
    rationale: string;
    requires_user_approval: string[];
    decision?: unknown;
  } | null;
};

type FeedRun = {
  task_id: string;
  run_id: string;
  objective: string;
  source_json: string;
  review_status: string;
  changed_files: string[];
  runtime_id: string;
};

interface Props {
  jobs: FeedJob[];
  runs: FeedRun[];
  onSelectJob: (jobId: string) => void;
  onApprove: (jobId: string) => void;
  onDeny: (jobId: string) => void;
  onMerge: (job: FeedJob, run: FeedRun) => void;
}

type ItemKind = "approval" | "running" | "success" | "error";

function itemKind(job: FeedJob): ItemKind {
  const needsApproval =
    (job.permission_request?.requires_user_approval?.length ?? 0) > 0 &&
    !job.permission_request?.decision;
  if (needsApproval) return "approval";
  if (job.status === "running" || job.status === "starting") return "running";
  if (job.status === "completed" && (job.returncode === 0 || job.returncode === null)) return "success";
  return "error";
}

function timeLabel(jobId: string): string {
  const m = jobId.match(/job-(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
  if (!m) return "";
  const t = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`);
  const diffMin = Math.round((Date.now() - t.getTime()) / 60000);
  if (diffMin < 1) return "ahora";
  if (diffMin < 60) return `hace ${diffMin}m`;
  return `hace ${Math.round(diffMin / 60)}h`;
}

const KIND_SORT: Record<ItemKind, number> = { approval: 0, running: 1, success: 2, error: 3 };

export function JobsFeed({ jobs, runs, onSelectJob, onApprove, onDeny, onMerge }: Props) {
  const runByKey: Record<string, FeedRun> = {};
  for (const r of runs) runByKey[`${r.task_id}:${r.run_id}`] = r;

  const items = jobs
    .map((job) => ({ job, run: runByKey[`${job.task_id}:${job.run_id}`], kind: itemKind(job) }))
    .sort((a, b) => KIND_SORT[a.kind] - KIND_SORT[b.kind]);

  return (
    <div className="jobs-feed">
      <div className="jobs-feed-header">
        <span className="jobs-feed-title">Jobs</span>
        <span className="jobs-feed-count">{jobs.length}</span>
      </div>
      {items.length === 0 && (
        <div className="jobs-feed-empty">Sin jobs activos</div>
      )}
      {items.map(({ job, run, kind }) => {
        const name = (job.objective ?? run?.objective ?? job.job_id).slice(0, 32);
        const cssKind = kind === "approval" ? "blocked" : kind;
        const icon = kind === "approval" ? "⚠" : kind === "running" ? "⏳" : kind === "success" ? "✅" : "❌";
        return (
          <div key={job.job_id} className={`job-feed-item ${cssKind}`}>
            <div className="job-feed-item-header">
              <span className="job-feed-item-name">{icon} {name}</span>
              <span className="job-feed-item-time">{timeLabel(job.job_id)}</span>
            </div>
            <div className="job-feed-item-meta">
              {kind === "approval" && `OK: ${job.permission_request!.requires_user_approval.join(", ")}`}
              {kind === "running" && "ejecutando..."}
              {kind === "success" && `${run?.changed_files.length ?? 0} archivos · ${run?.review_status === "awaiting_review" ? "merge listo" : (run?.review_status ?? "completado")}`}
              {kind === "error" && `falló · código ${job.returncode ?? "?"}`}
            </div>
            <div className="job-feed-item-actions">
              {kind === "approval" && <>
                <button className="job-feed-btn approve" onClick={(e) => { e.stopPropagation(); onApprove(job.job_id); }}>Aprobar</button>
                <button className="job-feed-btn deny" onClick={(e) => { e.stopPropagation(); onDeny(job.job_id); }}>Denegar</button>
              </>}
              {kind === "success" && run?.review_status === "awaiting_review" && (
                <button className="job-feed-btn merge" onClick={(e) => { e.stopPropagation(); onMerge(job, run); }}>Merge</button>
              )}
              <button className="job-feed-btn detail" onClick={() => onSelectJob(job.job_id)}>
                Ver <ArrowRight size={9} />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
