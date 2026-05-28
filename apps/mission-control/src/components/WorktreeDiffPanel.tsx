import { useState, useEffect, useRef } from "react";
import { GitMerge, Trash2, RefreshCw } from "lucide-react";

interface Props {
  jobId: string;
  isLive?: boolean;
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

interface WorktreeDiffStatData {
  files: Array<{ path: string; stat: string }>;
  summary: string;
  file_count: number;
  branch: string;
  worktree_exists: boolean;
  status?: string;
  error?: string;
}

export function WorktreeDiffPanel({ jobId, isLive = false, onMerged, onRejected }: Props) {
  const [diff, setDiff] = useState("");
  const [branch, setBranch] = useState("");
  const [worktreeExists, setWorktreeExists] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<"idle" | "merging" | "merged" | "rejected">("idle");
  const [error, setError] = useState<string | null>(null);
  const [liveStat, setLiveStat] = useState<WorktreeDiffStatData | null>(null);
  const lastStatSig = useRef<string>("");

  useEffect(() => {
    if (jobId) load();
  }, [jobId]);

  // Live polling of diff-stat while job is running. Refreshes the full diff only
  // when the stat changes (file count or summary line). Avoids constantly pulling
  // multi-MB diffs during a long job.
  useEffect(() => {
    if (!jobId || !isLive) return;
    let cancelled = false;
    const pollStat = async () => {
      try {
        const res = await fetch(`/api/run/${jobId}/worktree-diff-stat`);
        const data: WorktreeDiffStatData = await res.json();
        if (cancelled) return;
        if (data.error) return;
        setLiveStat(data);
        const sig = `${data.file_count}|${data.summary}`;
        if (sig !== lastStatSig.current) {
          lastStatSig.current = sig;
          load();
        }
      } catch {
        // best-effort — keep polling
      }
    };
    pollStat();
    const id = setInterval(pollStat, 15_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [jobId, isLive]);

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
        {isLive && <span className="worktree-live-dot" title="Polling diff every 15s" />}
        {liveStat?.summary && (
          <span className="worktree-live-summary" title={liveStat.summary}>
            {liveStat.file_count} file{liveStat.file_count === 1 ? "" : "s"} · {liveStat.summary}
          </span>
        )}
        <button className="icon-btn" onClick={load} title="Refresh diff">
          <RefreshCw size={12} />
        </button>
      </header>
      {diff ? (
        <pre className="diff-output">{diff}</pre>
      ) : (
        <div className="diff-empty">
          {isLive ? "Waiting for first changes…" : "No changes in this worktree."}
        </div>
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
