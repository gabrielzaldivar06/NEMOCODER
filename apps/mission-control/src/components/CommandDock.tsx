import { useEffect, useRef, useState } from "react";
import { ArrowUp, Bot, ChevronDown, Clock3, Database, Files, Hand, Plus, Square, Target, Thermometer, Wrench } from "lucide-react";

export type CommandDockMode = "send" | "queue" | "steer" | "plan";

type CommandDockProps = {
  draft: string;
  provider: string;
  endpointLabel: string;
  currentModel: string;
  running: boolean;
  queuedPrompt: string | null;
  queuedPrompts: string[];
  onDraftChange: (objective: string) => void;
  onSubmit: (mode?: CommandDockMode) => void;
  onStop: () => void;
  onProviderChange: (provider: string) => void;
  onModelChange: (model: string) => void;
  onOpenComposer: () => void;
  onOpenMemory: () => void;
};

type ModelEntry = { id: string; type: string };

function shortName(id: string): string {
  const slash = id.indexOf("/");
  return slash >= 0 ? id.slice(slash + 1) : id;
}

const TEMP_STEPS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];

function loadTemp(): number {
  const stored = parseFloat(localStorage.getItem("mc_temperature") ?? "");
  return Number.isFinite(stored) && TEMP_STEPS.includes(stored) ? stored : 0.6;
}

export function CommandDock({ draft, provider, endpointLabel, currentModel, running, queuedPrompt, queuedPrompts, onDraftChange, onSubmit, onStop, onProviderChange, onModelChange, onOpenComposer, onOpenMemory }: CommandDockProps) {
  const [sendHaloOpen, setSendHaloOpen] = useState<boolean>(false);
  const [pendingAttachments, setPendingAttachments] = useState<File[]>([]);
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [temperature, setTemperature] = useState<number>(loadTemp);
  const modelPickerRef = useRef<HTMLDivElement | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const canSendDraft = draft.trim().length > 0;
  const draftFieldId = "space-code-command-draft";
  const attachmentFieldId = "space-code-command-attachments";
  const modelFieldId = "space-code-model-select";

  useEffect(() => {
    fetch("/api/models")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d: { models?: ModelEntry[] }) => { setAvailableModels(d.models ?? []); })
      .catch(() => {})
      .finally(() => setModelsLoaded(true));
  }, []);

  useEffect(() => {
    if (!modelPickerOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target as Node)) {
        setModelPickerOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [modelPickerOpen]);

  const cycleTemperature = () => {
    setTemperature((prev) => {
      const idx = TEMP_STEPS.indexOf(prev);
      const next = TEMP_STEPS[(idx + 1) % TEMP_STEPS.length];
      localStorage.setItem("mc_temperature", String(next));
      return next;
    });
  };

  // Group by provider prefix
  const groups: Record<string, string[]> = {};
  for (const m of availableModels) {
    const slash = m.id.indexOf("/");
    const prefix = slash >= 0 ? m.id.slice(0, slash) : "other";
    (groups[prefix] ??= []).push(m.id);
  }
  const prefixes = Object.keys(groups).sort();
  const currentInList = availableModels.some((m) => m.id === currentModel);

  const openAttachmentPicker = () => {
    attachmentInputRef.current?.click();
  };

  const attachMedia = (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextFiles = Array.from(event.target.files ?? []);
    if (nextFiles.length === 0) return;
    setPendingAttachments((current) => {
      const seen = new Set(current.map((file) => `${file.name}-${file.size}-${file.type}`));
      const merged = [...current];
      for (const file of nextFiles) {
        const key = `${file.name}-${file.size}-${file.type}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      }
      return merged;
    });
    event.target.value = "";
  };

  const removeAttachment = (name: string) => {
    setPendingAttachments((current) => current.filter((file) => file.name !== name));
  };

  const runHaloAction = (mode: CommandDockMode | "stop") => {
    if (mode === "stop") {
      onStop();
      setSendHaloOpen(false);
      return;
    }
    onSubmit(mode);
    setSendHaloOpen(false);
  };

  const statusLabel = queuedPrompts.length > 0 ? `${queuedPrompts.length} queued` : endpointLabel;

  return (
    <div className="command-dock">
      <div className="command-dock-head" aria-label="Command dock status">
        <span><i /> Command dock</span>
        <strong>{running ? "Streaming" : canSendDraft ? "Ready" : "Awaiting objective"}</strong>
        <small>{statusLabel}</small>
      </div>
      <label className="sr-only" htmlFor={attachmentFieldId}>Attach files for the next Space Code prompt</label>
      <input
        id={attachmentFieldId}
        ref={attachmentInputRef}
        type="file"
        accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.txt,.md,.json"
        multiple
        className="hidden-file-input"
        onChange={attachMedia}
      />
      {pendingAttachments.length > 0 && <div className="attachment-strip" aria-label="Adjuntos preparados">
        {pendingAttachments.map((file) => (
          <button key={`${file.name}-${file.size}`} className="attachment-chip" onClick={() => removeAttachment(file.name)} title={`Quitar adjunto: ${file.name}`}>
            <Files size={12} />
            <span>{file.name}</span>
          </button>
        ))}
      </div>}
      <div className="context-bar">
        <div className="context-chip-wrap" ref={modelPickerRef}>
          <button
            className="context-chip model-chip"
            onClick={() => setModelPickerOpen((o) => !o)}
            title={currentModel || "Seleccionar modelo"}
            aria-haspopup="listbox"
            aria-expanded={modelPickerOpen}
          >
            <Bot size={11} />
            <span>{shortName(currentModel) || (modelsLoaded ? "Sin modelo" : "Cargando…")}</span>
            <ChevronDown size={10} />
          </button>
          {modelPickerOpen && (
            <div className="model-picker-dropdown" role="listbox" aria-label="Seleccionar modelo">
              {availableModels.length === 0 && <div className="model-picker-empty">{modelsLoaded ? "Sin modelos disponibles" : "Cargando…"}</div>}
              {availableModels.map((m) => (
                <button
                  key={m.id}
                  className={`model-picker-option${m.id === currentModel ? " active" : ""}`}
                  role="option"
                  aria-selected={m.id === currentModel}
                  onClick={() => { onModelChange(m.id); setModelPickerOpen(false); }}
                >
                  {m.id === currentModel && <span className="model-active-dot">●</span>}
                  {shortName(m.id)}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          className="context-chip temp-chip"
          onClick={cycleTemperature}
          title={`Temperatura: ${temperature} — click para cambiar`}
        >
          <Thermometer size={11} />
          <span>{temperature.toFixed(1)}</span>
        </button>
      </div>
      <label className="sr-only" htmlFor={draftFieldId}>Describe the next objective, constraint, or experiment</label>
      <textarea
        id={draftFieldId}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") onSubmit();
        }}
        placeholder="Describe the next objective, constraint, or experiment..."
      />
      <div className="command-dock-actions">
        <button onClick={openAttachmentPicker} title="Adjuntar multimedia"><Plus size={15} /> File</button>
        <button onClick={onOpenComposer} title="Configurar handoff"><Hand size={15} /> Handoff</button>
        <button onClick={onOpenMemory} title="Memoria NEMO"><Database size={15} /> Memory</button>
        <label className="provider-switch real" htmlFor={modelFieldId} title={`Modelo: ${currentModel || "—"} via ${endpointLabel}`}>
          <Bot size={15} />
          <select
            id={modelFieldId}
            value={currentModel}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={!modelsLoaded || availableModels.length === 0}
            title={currentModel}
          >
            {!currentInList && currentModel && <option value={currentModel}>{shortName(currentModel)}</option>}
            {modelsLoaded && availableModels.length === 0 && <option value={currentModel}>{shortName(currentModel) || "—"}</option>}
            {prefixes.map((prefix) => (
              <optgroup key={prefix} label={prefix}>
                {groups[prefix].map((id) => <option key={id} value={id}>{shortName(id)}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        <div className={`send-halo ${sendHaloOpen ? "open" : ""}`} onMouseLeave={() => setSendHaloOpen(false)}>
          <span className="send-halo-label plan">PLAN</span>
          <span className="send-halo-label queue">QUEUE</span>
          <span className="send-halo-label steer">STEER</span>
          <span className="send-halo-label hold">HOLD</span>
          <button className="send-intent" onClick={() => running ? runHaloAction("stop") : runHaloAction("send")} disabled={!running && !canSendDraft} onMouseEnter={() => setSendHaloOpen(true)} onFocus={() => setSendHaloOpen(true)} title={running ? "Detener respuesta" : "Enviar"} aria-label={running ? "Detener respuesta" : "Enviar objetivo"}>{running ? <Square size={16} /> : <ArrowUp size={18} />}</button>
          <div className="send-halo-menu" aria-label="Acciones del agente">
            <button className="send-halo-option plan" onClick={() => runHaloAction("plan")} disabled={!canSendDraft && !running} title="Modo plan" aria-label="Modo plan" data-label="Plan"><Target size={13} /><span>Plan</span></button>
            <button className="send-halo-option queue" onClick={() => runHaloAction("queue")} disabled={!canSendDraft} title="Poner en cola" aria-label="Poner en cola" data-label="Queue"><Clock3 size={13} /><span>Queue</span></button>
            <button className="send-halo-option steer" onClick={() => runHaloAction("steer")} disabled={!canSendDraft} title="Steer prioritario" aria-label="Steer prioritario" data-label="Steer"><Wrench size={13} /><span>Steer</span></button>
            <button className="send-halo-option stop" onClick={() => runHaloAction("stop")} disabled={!running} title="Detener" aria-label="Detener" data-label="Hold"><Square size={13} /><span>Hold</span></button>
          </div>
        </div>
      </div>
      {(pendingAttachments.length > 0 || queuedPrompt || queuedPrompts.length > 1) && <div className="home-chat-notes" role="status" aria-live="polite">
        {pendingAttachments.length > 0 && <span>Adjuntos preparados para envio multimodal.</span>}
        {queuedPrompt && <span>En cola: {queuedPrompt}</span>}
        {queuedPrompts.length > 1 && <span>Pendientes: {queuedPrompts.length}</span>}
      </div>}
    </div>
  );
}
