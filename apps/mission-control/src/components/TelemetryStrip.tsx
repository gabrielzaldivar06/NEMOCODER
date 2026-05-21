interface Props {
  nemoReady: boolean;
  llmReady: boolean;
  gitReady: boolean;
  pendingApprovals: number;
}

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="tstrip-item">
      <span className={`tstrip-dot ${ok ? "ok" : "warn"}`} />
      <span style={{ color: ok ? "#4ade80" : "#f59e0b" }}>{label}</span>
    </span>
  );
}

export function TelemetryStrip({ nemoReady, llmReady, gitReady, pendingApprovals }: Props) {
  return (
    <div className="telemetry-strip">
      <span className="tstrip-label">Sistema</span>
      <Dot ok={nemoReady} label="NEMO" />
      <Dot ok={llmReady} label="LLM" />
      <Dot ok={gitReady} label="Git" />
      {pendingApprovals > 0 && (
        <span className="tstrip-pending">
          ⚠ {pendingApprovals} OK pendiente{pendingApprovals > 1 ? "s" : ""}
        </span>
      )}
    </div>
  );
}
