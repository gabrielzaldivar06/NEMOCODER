import { useState, useEffect } from "react";
import { GitMerge, Trash2, RefreshCw } from "lucide-react";

interface Props {
  jobId: string;
  onMerged?: () => void;
  onRejected?: () => void;
}

interface WorktreeDiffData {
  diff: string;
  branch: string;
  runtime_id: string;
  worktree_exists: boolean;
  error?: string;
}

export function WorktreeDiffPanel({ jobId, onMerged, onRejected }: Props) {
  const [diff, setDiff] = useState("");
  const [branch, setBranch] = useState("");
  const [worktreeExists, setWorktreeExists] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<"idle" | "merging" | "merged" | "rejected">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (jobId) load();
  }, [jobId]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/run/${jobId}/worktree-diff`);
      const data: WorktreeDiffData = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setDiff(data.diff ?? "");
        setBranch(data.branch ?? "");
        setWorktreeExists(Boolean(data.worktree_exists));
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleMerge = async () => {
    setStatus("merging");
    try {
      const res = await fetch(`/api/run/${jobId}/worktree-merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true, message: `merge ${branch}` }),
      });
      const data = await res.json();
      if (data.merged) {
        setStatus("merged");
        onMerged?.();
      } else {
        setStatus("idle");
        setError(data.error ?? "Merge failed");
      }
    } catch (e) {
      setStatus("idle");
      setError(String(e));
    }
  };

  const handleReject = async () => {
    const reason = window.prompt("Why are you rejecting this run? (optional)") ?? "";
    try {
      await fetch(`/api/run/${jobId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
    } catch {
      // best-effort cleanup
    }
    setStatus("rejected");
    onRejected?.();
  };

  if (!jobId) return <div className="worktree-diff-panel empty">No job selected.</div>;
  if (loading) return <div className="worktree-diff-panel loading">Loading diff…</div>;
  if (status === "merged") return <div className="worktree-diff-panel merged">Merged to main. Branch removed.</div>;
  if (status === "rejected") return <div className="worktree-diff-panel rejected">Worktree removed.</div>;
  if (error) return <div className="worktree-diff-panel error">{error}</div>;
  if (!worktreeExists) return <div className="worktree-diff-panel empty">No active worktree for this run.</div>;

  return (
    <div className="worktree-diff-panel">
      <header className="worktree-diff-header">
        <code className="branch-label">{branch}</code>
        <button className="icon-btn" onClick={load} title="Refresh diff">
          <RefreshCw size={12} />
        </button>
      </header>
      {diff ? (
        <pre className="diff-output">{diff}</pre>
      ) : (
        <div className="diff-empty">No changes in this worktree.</div>
      )}
      <div className="worktree-actions">
        <button
          className="worktree-merge-btn"
          onClick={handleMerge}
          disabled={status === "merging" || !diff}
        >
          <GitMerge size={14} />
          {status === "merging" ? "Merging…" : "Approve & Merge to main"}
        </button>
        <button className="worktree-reject-btn" onClick={handleReject}>
          <Trash2 size={14} /> Reject & Remove
        </button>
      </div>
    </div>
  );
}
