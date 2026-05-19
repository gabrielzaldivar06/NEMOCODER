import { useEffect, useRef, useState } from "react";
import { ArrowUp, Brain, ChevronDown, Clock3, Database, Files, Hand, Plus, Server, Square, Target, Thermometer, Wrench, Zap } from "lucide-react";

export type CommandDockMode = "send" | "queue" | "steer" | "plan";

export type ModelCaps = { thinking: boolean; temperature: boolean; reasoning_effort: boolean; vision: boolean; tools: boolean };
export type ModelEntry = { id: string; type: string; state?: string; caps: ModelCaps };
export type ProviderEntry = { id: string; label: string; base_url: string; models: ModelEntry[]; available: boolean };
export type ChatParams = { model: string; baseUrl: string; temperature: number; enableThinking: boolean };

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
  onParamsChange?: (params: ChatParams) => void;
  onOpenComposer: () => void;
  onOpenMemory: () => void;
};

const TEMP_STEPS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];
const DEFAULT_CAPS: ModelCaps = { thinking: false, temperature: true, reasoning_effort: false, vision: false, tools: true };

function loadTemp(): number {
  const stored = parseFloat(localStorage.getItem("mc_temperature") ?? "");
  return Number.isFinite(stored) && TEMP_STEPS.includes(stored) ? stored : 0.6;
}

function shortName(id: string): string {
  const slash = id.lastIndexOf("/");
  return slash >= 0 ? id.slice(slash + 1) : id;
}

function modelSize(id: string): string | null {
  const hits = [...id.matchAll(/(\d+\.?\d*)\s*b(?=[^a-zA-Z]|$)/gi)];
  const sizes = hits.map((m) => parseFloat(m[1])).filter((n) => n >= 1 && n <= 2000);
  if (!sizes.length) return null;
  const n = Math.max(...sizes);
  return n >= 1000 ? `${(n / 1000).toFixed(1)}T` : `${n}B`;
}

function sizeTier(label: string): "small" | "medium" | "large" {
  const n = parseFloat(label);
  if (label.endsWith("T") || n > 70) return "large";
  if (n > 13) return "medium";
  return "small";
}

export function CommandDock({ draft, provider, endpointLabel, currentModel, running, queuedPrompt, queuedPrompts, onDraftChange, onSubmit, onStop, onProviderChange, onModelChange, onParamsChange, onOpenComposer, onOpenMemory }: CommandDockProps) {
  const [sendHaloOpen, setSendHaloOpen] = useState<boolean>(false);
  const [pendingAttachments, setPendingAttachments] = useState<File[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [temperature, setTemperature] = useState<number>(loadTemp);
  const [enableThinking, setEnableThinking] = useState<boolean>(() => localStorage.getItem("mc_thinking") === "1");
  const [selectedBaseUrl, setSelectedBaseUrl] = useState<string>("");
  const modelPickerRef = useRef<HTMLDivElement | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const canSendDraft = draft.trim().length > 0;
  const draftFieldId = "space-code-command-draft";
  const attachmentFieldId = "space-code-command-attachments";

  useEffect(() => {
    fetch("/api/models")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d: { providers?: ProviderEntry[]; models?: ModelEntry[]; current?: string; current_provider?: string }) => {
        if (d.providers) {
          setProviders(d.providers);
          // Set initial base_url from current provider
          const active = d.providers.find((p) => p.models.some((m) => m.id === currentModel)) ?? d.providers[0];
          if (active) setSelectedBaseUrl(active.base_url);
        }
      })
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

  // Find current model's caps and provider
  const allModels = providers.flatMap((p) => p.models.map((m) => ({ ...m, providerLabel: p.label, base_url: p.base_url, providerId: p.id })));
  const currentEntry = allModels.find((m) => m.id === currentModel);
  const caps: ModelCaps = currentEntry?.caps ?? DEFAULT_CAPS;
  const isNvidiaProvider = currentEntry ? currentEntry.providerId === "nvidia" : selectedBaseUrl.includes("nvidia");

  const notifyParams = (model: string, baseUrl: string, temp: number, thinking: boolean) => {
    onParamsChange?.({ model, baseUrl, temperature: temp, enableThinking: thinking });
  };

  const selectModel = (modelId: string, baseUrl: string) => {
    onModelChange(modelId);
    setSelectedBaseUrl(baseUrl);
    setModelPickerOpen(false);
    notifyParams(modelId, baseUrl, temperature, enableThinking);
  };

  const cycleTemperature = () => {
    setTemperature((prev) => {
      const idx = TEMP_STEPS.indexOf(prev);
      const next = TEMP_STEPS[(idx + 1) % TEMP_STEPS.length];
      localStorage.setItem("mc_temperature", String(next));
      notifyParams(currentModel, selectedBaseUrl, next, enableThinking);
      return next;
    });
  };

  const toggleThinking = () => {
    setEnableThinking((prev) => {
      const next = !prev;
      localStorage.setItem("mc_thinking", next ? "1" : "0");
      notifyParams(currentModel, selectedBaseUrl, temperature, next);
      return next;
    });
  };

  const openAttachmentPicker = () => attachmentInputRef.current?.click();

  const attachMedia = (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextFiles = Array.from(event.target.files ?? []);
    if (nextFiles.length === 0) return;
    setPendingAttachments((current) => {
      const seen = new Set(current.map((file) => `${file.name}-${file.size}-${file.type}`));
      const merged = [...current];
      for (const file of nextFiles) {
        const key = `${file.name}-${file.size}-${file.type}`;
        if (!seen.has(key)) { seen.add(key); merged.push(file); }
      }
      return merged;
    });
    event.target.value = "";
  };

  const removeAttachment = (name: string) => {
    setPendingAttachments((current) => current.filter((file) => file.name !== name));
  };

  const runHaloAction = (mode: CommandDockMode | "stop") => {
    if (mode === "stop") { onStop(); setSendHaloOpen(false); return; }
    onSubmit(mode);
    setSendHaloOpen(false);
  };

  const statusLabel = queuedPrompts.length > 0 ? `${queuedPrompts.length} en cola` : endpointLabel;

  return (
    <div className="command-dock">
      <div className="command-dock-head" aria-label="Command dock status">
        <span><i /> Command dock</span>
        <strong>{running ? "Streaming" : canSendDraft ? "Listo" : "Esperando objetivo"}</strong>
        <small>{statusLabel}</small>
      </div>
      <label className="sr-only" htmlFor={attachmentFieldId}>Adjuntar archivos al próximo prompt</label>
      <input
        id={attachmentFieldId}
        ref={attachmentInputRef}
        type="file"
        accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.txt,.md,.json"
        multiple
        className="hidden-file-input"
        onChange={attachMedia}
      />
      {pendingAttachments.length > 0 && (
        <div className="attachment-strip" aria-label="Adjuntos preparados">
          {pendingAttachments.map((file) => (
            <button key={`${file.name}-${file.size}`} className="attachment-chip" onClick={() => removeAttachment(file.name)} title={`Quitar adjunto: ${file.name}`}>
              <Files size={12} /><span>{file.name}</span>
            </button>
          ))}
        </div>
      )}

      {/* ── CONTEXT BAR ── */}
      <div className="context-bar">
        {/* Model chip + dropdown */}
        <div className="context-chip-wrap" ref={modelPickerRef}>
          <button
            className="context-chip model-chip"
            onClick={() => setModelPickerOpen((o) => !o)}
            title={currentModel || "Seleccionar modelo"}
            aria-haspopup="listbox"
            aria-expanded={modelPickerOpen}
          >
            <Server size={11} />
            <span>{shortName(currentModel) || (modelsLoaded ? "Sin modelo" : "Cargando…")}</span>
            <ChevronDown size={10} />
          </button>
          {modelPickerOpen && (
            <div className="model-picker-dropdown" role="listbox" aria-label="Seleccionar modelo">
              {!modelsLoaded && <div className="model-picker-empty">Cargando modelos…</div>}
              {modelsLoaded && providers.every((p) => p.models.length === 0) && (
                <div className="model-picker-empty">Sin modelos disponibles</div>
              )}
              {providers.filter((p) => p.models.length > 0).map((prov) => (
                <div key={prov.id} className="model-picker-group">
                  <div className="model-picker-group-label">{prov.label}</div>
                  {prov.models.map((m) => (
                    <button
                      key={m.id}
                      className={`model-picker-option${m.id === currentModel ? " active" : ""}${m.state === "loaded" ? " loaded" : ""}`}
                      role="option"
                      aria-selected={m.id === currentModel}
                      onClick={() => selectModel(m.id, prov.base_url)}
                    >
                      {m.id === currentModel && <span className="model-active-dot">●</span>}
                      <span className="model-option-name">{shortName(m.id)}</span>
                      <span className="model-option-badges">
                        {m.state === "loaded" && <span className="model-badge loaded" title="Cargado en memoria">▲</span>}
                        {(() => { const sz = modelSize(m.id); return sz ? <span className={`model-badge size ${sizeTier(sz)}`} title={`Parámetros del modelo: ~${sz}`}>{sz}</span> : null; })()}
                        {m.caps.thinking && <span className="model-badge thinking" title="Thinking / razonamiento extendido">💭</span>}
                        {m.caps.vision && <span className="model-badge vision" title="Visión / multimodal">👁</span>}
                        {m.caps.tools && <span className="model-badge tools" title="Function calling / herramientas">🔧</span>}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Provider badge */}
        <span className={`context-provider-badge ${isNvidiaProvider ? "nvidia" : "local"}`}>
          {isNvidiaProvider ? "NIM" : "Local"}
        </span>

        {/* Thinking toggle — always available; dimmed when model doesn't declare thinking support */}
        <button
          className={`context-chip thinking-chip${enableThinking ? " on" : ""}${!caps.thinking ? " uncertain" : ""}`}
          onClick={toggleThinking}
          title={caps.thinking
            ? (enableThinking ? "Thinking activado — click para desactivar" : "Activar thinking")
            : (enableThinking ? "Thinking activado (experimental para este modelo) — click para desactivar" : "Activar thinking (experimental para este modelo)")}
        >
          <Brain size={11} />
          <span>{enableThinking ? "Thinking" : "Think"}</span>
        </button>

        {/* Temperature chip — shown when model supports it (locked while thinking is on) */}
        {caps.temperature && (
          <button
            className={`context-chip temp-chip${enableThinking ? " dimmed" : ""}`}
            onClick={cycleTemperature}
            disabled={enableThinking}
            title={enableThinking ? "Temperatura fija en modo thinking" : `Temperatura: ${temperature} — click para cambiar`}
          >
            <Thermometer size={11} />
            <span>{enableThinking ? "T:auto" : temperature.toFixed(1)}</span>
          </button>
        )}

        {/* Reasoning effort — for o1-style models */}
        {caps.reasoning_effort && (
          <button className="context-chip effort-chip" title="Nivel de razonamiento (reasoning effort)">
            <Zap size={11} />
            <span>medium</span>
          </button>
        )}
      </div>

      <label className="sr-only" htmlFor={draftFieldId}>Describe el próximo objetivo, restricción o experimento</label>
      <textarea
        id={draftFieldId}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") onSubmit();
        }}
        placeholder="Describe el próximo objetivo, restricción o experimento…"
      />
      <div className="command-dock-actions">
        <button onClick={openAttachmentPicker} title="Adjuntar multimedia"><Plus size={15} /> Archivo</button>
        <button onClick={onOpenComposer} title="Configurar handoff"><Hand size={15} /> Handoff</button>
        <button onClick={onOpenMemory} title="Memoria NEMO"><Database size={15} /> Memoria</button>
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
      {(pendingAttachments.length > 0 || queuedPrompt || queuedPrompts.length > 1) && (
        <div className="home-chat-notes" role="status" aria-live="polite">
          {pendingAttachments.length > 0 && <span>Adjuntos preparados para envío multimodal.</span>}
          {queuedPrompt && <span>En cola: {queuedPrompt}</span>}
          {queuedPrompts.length > 1 && <span>Pendientes: {queuedPrompts.length}</span>}
        </div>
      )}
    </div>
  );
}
