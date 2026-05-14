// apps/mission-control/src/components/PermissionRequestPanel.tsx
import { useState } from "react";
import { ShieldX, CheckCircle2, XCircle } from "lucide-react";

interface PermissionRequestData {
  job_id: string;
  categories: string[];
  rationale: string;
  auto_approved: string[];
  requires_user_approval: string[];
}

interface Props {
  jobId: string;
  objective: string;
  permissionRequest: PermissionRequestData;
  onGranted: () => void;
  onDenied: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  file_write_outside_worktree: "Escritura fuera del sandbox",
  shell_command:               "Ejecución de comandos",
  network_call:                "Llamadas de red",
  config_file_write:           "Archivos de configuración",
};

const CATEGORY_ICONS: Record<string, string> = {
  file_write_outside_worktree: "📁",
  shell_command:               "▶",
  network_call:                "🌐",
  config_file_write:           "⚙",
};

export function PermissionRequestPanel({ jobId, objective, permissionRequest, onGranted, onDenied }: Props) {
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<"idle" | "granting" | "denying">("idle");
  const [error, setError] = useState<string | null>(null);

  const handleGrant = async () => {
    setStatus("granting");
    setError(null);
    try {
      const res = await fetch(`/api/run/${jobId}/permission-grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      const data = await res.json();
      if (data.granted) {
        onGranted();
      } else {
        setError(data.error ?? "Error al conceder permiso");
        setStatus("idle");
      }
    } catch (e) {
      setError(String(e));
      setStatus("idle");
    }
  };

  const handleDeny = async () => {
    setStatus("denying");
    setError(null);
    try {
      const res = await fetch(`/api/run/${jobId}/permission-deny`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      const data = await res.json();
      if (data.denied) {
        onDenied();
      } else {
        setError(data.error ?? "Error al denegar permiso");
        setStatus("idle");
      }
    } catch (e) {
      setError(String(e));
      setStatus("idle");
    }
  };

  const shortObj = objective.length > 80 ? objective.slice(0, 80) + "…" : objective;
  const requires = permissionRequest.requires_user_approval;
  const autoOk = permissionRequest.auto_approved;

  return (
    <div className="permission-request-panel">
      <div className="permission-request-header">
        <ShieldX size={16} className="permission-request-icon" />
        <span>Permiso requerido antes de ejecutar</span>
      </div>

      <div className="permission-request-objective">
        <span className="permission-label">Task:</span>
        <em>{shortObj}</em>
      </div>

      {permissionRequest.rationale && (
        <p className="permission-rationale">{permissionRequest.rationale}</p>
      )}

      {requires.length > 0 && (
        <div className="permission-category-group">
          <span className="permission-label">Requiere aprobación:</span>
          <div className="permission-badges">
            {requires.map((cat) => (
              <span key={cat} className="permission-badge permission-badge--ask">
                {CATEGORY_ICONS[cat] ?? "?"} {CATEGORY_LABELS[cat] ?? cat}
              </span>
            ))}
          </div>
        </div>
      )}

      {autoOk.length > 0 && (
        <div className="permission-category-group">
          <span className="permission-label">Auto-aprobadas (política):</span>
          <div className="permission-badges">
            {autoOk.map((cat) => (
              <span key={cat} className="permission-badge permission-badge--auto">
                {CATEGORY_ICONS[cat] ?? "?"} {CATEGORY_LABELS[cat] ?? cat}
              </span>
            ))}
          </div>
        </div>
      )}

      <input
        className="permission-note-input"
        type="text"
        placeholder="Nota opcional…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        disabled={status !== "idle"}
      />

      {error && <p className="permission-error">{error}</p>}

      <div className="permission-actions">
        <button
          className="permission-grant-btn"
          onClick={handleGrant}
          disabled={status !== "idle"}
        >
          <CheckCircle2 size={14} />
          {status === "granting" ? "Aprobando…" : "Aprobar y lanzar"}
        </button>
        <button
          className="permission-deny-btn"
          onClick={handleDeny}
          disabled={status !== "idle"}
        >
          <XCircle size={14} />
          {status === "denying" ? "Denegando…" : "Denegar"}
        </button>
      </div>
    </div>
  );
}
