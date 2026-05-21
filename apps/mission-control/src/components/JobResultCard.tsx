import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";

const CAT_LABELS: Record<string, string> = {
  network_call: "Red",
  shell_command: "Shell",
  config_file_write: "Config",
  file_write_outside_worktree: "Archivos externos",
};

function fmtDuration(secs: number): string {
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

interface Props {
  kind: "success" | "blocked" | "error";
  jobId: string;
  objective: string;
  durationSeconds?: number;
  changedFiles?: string[];
  branch?: string;
  permissionCategories?: string[];
  onViewDetail: (jobId: string) => void;
  onMerge?: () => void;
  onApprove?: () => void;
  onDeny?: () => void;
}

export function JobResultCard({
  kind, jobId, objective, durationSeconds, changedFiles, branch,
  permissionCategories, onViewDetail, onMerge, onApprove, onDeny,
}: Props) {
  const shortObj = objective.length > 60 ? objective.slice(0, 60) + "…" : objective;

  return (
    <div className={`job-result-card ${kind}`}>
      <div className="job-result-card-header">
        {kind === "success" && <CheckCircle2 size={13} style={{ color: "#4ade80", flexShrink: 0 }} />}
        {kind === "blocked" && <AlertTriangle size={13} style={{ color: "#f59e0b", flexShrink: 0 }} />}
        {kind === "error"   && <XCircle size={13} style={{ color: "#ef4444", flexShrink: 0 }} />}
        <span className="job-result-card-title">
          {kind === "success" && `${shortObj} — completado`}
          {kind === "blocked" && `${shortObj} — necesita tu OK`}
          {kind === "error"   && `${shortObj} — falló`}
        </span>
        {durationSeconds != null && (
          <span className="job-result-card-duration">{fmtDuration(durationSeconds)}</span>
        )}
      </div>

      {kind === "success" && (
        <div className="job-result-card-meta">
          {changedFiles != null && <span>📁 {changedFiles.length} archivo{changedFiles.length !== 1 ? "s" : ""}</span>}
          {branch && <span>🌿 {branch}</span>}
          <span>✓ validación OK</span>
        </div>
      )}

      {kind === "blocked" && permissionCategories && permissionCategories.length > 0 && (
        <div className="job-result-card-categories">
          {permissionCategories.map((cat) => (
            <span key={cat} className="job-result-card-cat">{CAT_LABELS[cat] ?? cat}</span>
          ))}
        </div>
      )}

      <div className="job-result-card-actions">
        {kind === "success" && onMerge && (
          <button className="job-result-btn merge" onClick={onMerge}>Merge a main</button>
        )}
        {kind === "blocked" && onApprove && (
          <button className="job-result-btn approve" onClick={onApprove}>Aprobar</button>
        )}
        {kind === "blocked" && onDeny && (
          <button className="job-result-btn deny" onClick={onDeny}>Denegar</button>
        )}
        <button className="job-result-btn detail" onClick={() => onViewDetail(jobId)}>
          Ver detalle
        </button>
      </div>
    </div>
  );
}
