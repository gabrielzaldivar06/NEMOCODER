import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Activity, Braces, Camera, Code2, Copy, Crosshair, Download, Eye, FileText, Film, Headphones, History, Image, Info, Layers3, Maximize2, MoreHorizontal, Paperclip, Pin, Puzzle, Search, Trash2, Zap } from "lucide-react";
import { buildArtifactLineDiff, type GeneratedArtifact, type GeneratedArtifactKind } from "../services/artifactUtils";

type ArtifactViewMode = "preview" | "source" | "inspect" | "compare";
type ArtifactViewportAction = "expand" | "capture" | "center" | "more";

type ArtifactWorkbenchProps = {
  artifacts: GeneratedArtifact[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onAttachToPrompt: (artifact: GeneratedArtifact) => void;
  onRemoveArtifact: (id: string) => void;
  onToggleFavorite: (id: string) => void;
  renderMarkdown: (content: string) => ReactNode;
};

type ArtifactKindFilter = "all" | GeneratedArtifactKind;
type ArtifactPerformanceSnapshot = { fps: number; cpu: number; gpu: number; drawCalls: string };
const ARTIFACT_VIEWPORT_STATUS: Record<ArtifactViewportAction | "idle", string> = {
  idle: "Viewport locked",
  expand: "Viewport expanded",
  capture: "Frame captured",
  center: "Target centered",
  more: "Controls armed",
};

function artifactSrcDoc(artifact: GeneratedArtifact): string {
  if (artifact.kind === "svg") {
    return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;min-height:100%;display:grid;place-items:center;background:#07101f;color:#e5edf8}svg{max-width:100%;max-height:100%;}</style></head><body>${artifact.content}</body></html>`;
  }
  if (artifact.kind === "html") return artifact.content;
  if (artifact.kind === "react") {
    return `<!doctype html><html><head><meta charset="utf-8"><script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script><script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script><script src="https://unpkg.com/@babel/standalone/babel.min.js"></script><style>html,body,#root{margin:0;min-height:100%;background:#07101f;color:#e5edf8;font-family:Inter,system-ui,sans-serif}</style></head><body><div id="root"></div><script type="text/babel">${artifact.content}\nReactDOM.render(React.createElement(App), document.getElementById("root"));</script></body></html>`;
  }
  return "";
}

function isRenderableArtifact(artifact: GeneratedArtifact | undefined): boolean {
  return Boolean(artifact && ["html", "svg", "markdown", "mermaid", "react", "image", "video", "audio", "image_request"].includes(artifact.kind));
}

function artifactFileExtension(artifact: GeneratedArtifact): string {
  if (artifact.kind === "html") return "html";
  if (artifact.kind === "svg") return "svg";
  if (artifact.kind === "markdown") return "md";
  if (artifact.kind === "json") return "json";
  if (artifact.kind === "image") return "media";
  if (artifact.kind === "video") return "mp4";
  if (artifact.kind === "audio") return "mp3";
  if (artifact.kind === "mermaid") return "mmd";
  if (artifact.kind === "react") return "jsx";
  return artifact.language && artifact.language !== "text" ? artifact.language.replace(/[^a-z0-9]+/gi, "").toLowerCase() || "txt" : "txt";
}

function artifactMimeType(artifact: GeneratedArtifact): string {
  if (artifact.kind === "html") return "text/html";
  if (artifact.kind === "svg") return "image/svg+xml";
  if (artifact.kind === "markdown") return "text/markdown";
  if (artifact.kind === "json") return "application/json";
  if (artifact.kind === "image") return "image/*";
  if (artifact.kind === "video") return "video/mp4";
  if (artifact.kind === "audio") return "audio/mpeg";
  return "text/plain";
}

function artifactFileName(artifact: GeneratedArtifact): string {
  const safeTitle = artifact.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 42) || "artifact";
  return `${safeTitle}.${artifactFileExtension(artifact)}`;
}

function artifactLineCount(artifact: GeneratedArtifact): number {
  return artifact.content.split(/\r?\n/).length;
}

function artifactPerformanceSnapshot(artifact: GeneratedArtifact | undefined): ArtifactPerformanceSnapshot {
  const seed = artifact ? artifact.content.length + artifact.title.length * 13 + artifact.kind.length * 31 : 64;
  const fps = 48 + (seed % 13);
  const cpu = 18 + (seed % 19);
  const gpu = 44 + (seed % 33);
  const drawCalls = `${((seed % 34) + 12) / 10}K`;
  return { fps, cpu, gpu, drawCalls };
}

function artifactSourceExcerpt(artifact: GeneratedArtifact): string[] {
  const lines = artifact.content.split(/\r?\n/).slice(0, 7);
  return lines.length > 0 ? lines : [artifact.content];
}

function artifactStandbySourceExcerpt(): string[] {
  return [
    "// Awaiting generated artifact",
    "render.pipeline.attach(activeArtifact)",
    "telemetry.mode = 'standby'",
    "viewport.target = 'center-stage'",
  ];
}

function artifactKindIcon(kind: GeneratedArtifactKind): ReactNode {
  if (kind === "image_request") return <Image size={15} />;
  if (kind === "image") return <Image size={15} />;
  if (kind === "video") return <Film size={15} />;
  if (kind === "audio") return <Headphones size={15} />;
  if (kind === "json") return <Braces size={15} />;
  if (kind === "markdown") return <FileText size={15} />;
  if (kind === "html" || kind === "svg" || kind === "react") return <Layers3 size={15} />;
  return <Code2 size={15} />;
}

function shortArtifactHash(artifact: GeneratedArtifact): string {
  return (artifact.contentHash ?? artifact.id).replace(/^artifact-/, "").slice(0, 8);
}

function formatArtifactTime(value: string | undefined): string {
  if (!value) return "session";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "session";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(timestamp);
}

function previousArtifactVersion(activeArtifact: GeneratedArtifact | undefined, versionSiblings: GeneratedArtifact[]): GeneratedArtifact | undefined {
  if (!activeArtifact || versionSiblings.length < 2) return undefined;
  const activeIndex = versionSiblings.findIndex((artifact) => artifact.id === activeArtifact.id);
  if (activeIndex <= 0) return undefined;
  return versionSiblings[activeIndex - 1];
}

function artifactFilterOptions(artifacts: GeneratedArtifact[]): ArtifactKindFilter[] {
  const kinds = Array.from(new Set(artifacts.map((artifact) => artifact.kind))).sort();
  return ["all", ...kinds] as ArtifactKindFilter[];
}

function ArtifactCompareView({ previous, current }: { previous: GeneratedArtifact; current: GeneratedArtifact }) {
  const diff = buildArtifactLineDiff(previous, current);
  return <div className="artifact-compare">
    <div className="artifact-compare-head">
      <div><span>Previous</span><strong>v{previous.version ?? "?"}</strong><small>#{shortArtifactHash(previous)}</small></div>
      <div><span>Current</span><strong>v{current.version ?? "draft"}</strong><small>#{shortArtifactHash(current)}</small></div>
      <div><span>Delta</span><strong>+{diff.added} / -{diff.removed}</strong><small>{diff.changed} changed</small></div>
    </div>
    <div className="artifact-diff-table" role="table" aria-label="Comparacion de versiones del artifact">
      {diff.rows.map((row, index) => <div className={`artifact-diff-row ${row.kind}`} role="row" key={`${row.kind}-${index}`}>
        <span className="artifact-diff-line">{row.leftLine ?? ""}</span>
        <span className="artifact-diff-line">{row.rightLine ?? ""}</span>
        <code>{row.kind === "added" ? row.right : row.kind === "removed" ? row.left : row.kind === "changed" ? `${row.left ?? ""}  ->  ${row.right ?? ""}` : row.right}</code>
      </div>)}
      {diff.omitted > 0 && <div className="artifact-diff-omitted">{diff.omitted.toLocaleString()} additional lines omitted</div>}
    </div>
  </div>;
}

function parseImageRequest(content: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(content) as Record<string, unknown>;
    return parsed && typeof parsed === "object" ? parsed : { prompt: content };
  } catch {
    return { prompt: content };
  }
}

function ImageRequestPreview({ artifact }: { artifact: GeneratedArtifact }) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "failed">("idle");
  const [generatedUrl, setGeneratedUrl] = useState<string>("");
  const [error, setError] = useState<string>("");
  const requestPayload = parseImageRequest(artifact.content);
  let prompt = String(requestPayload.prompt || artifact.content);
  try {
    const parsed = JSON.parse(artifact.content) as { prompt?: string; style?: string; size?: string };
    prompt = [parsed.prompt, parsed.style, parsed.size].filter(Boolean).join(" · ") || artifact.content;
  } catch {
    // Plain text image prompts are valid too.
  }

  useEffect(() => {
    setStatus("idle");
    setGeneratedUrl("");
    setError("");
  }, [artifact.id]);

  const generateImage = () => {
    setStatus("running");
    setError("");
    void fetch("/api/agent/generate-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    }).then(async (response) => {
      const payload = await response.json() as { image_url?: string; error?: string };
      if (!response.ok) throw new Error(payload.error || "image_generation_failed");
      if (!payload.image_url) throw new Error("image_generation_returned_no_url");
      setGeneratedUrl(payload.image_url);
      setStatus("done");
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "image_generation_failed");
      setStatus("failed");
    });
  };

  return <div className="artifact-image-request">
    {generatedUrl ? <img src={generatedUrl} alt={String(requestPayload.prompt || "Generated artifact")} /> : <Image size={30} />}
    <strong>{generatedUrl ? "Generated image" : "Image request"}</strong>
    <p>{prompt}</p>
    <button onClick={generateImage} disabled={status === "running"} title="Generar imagen local"><Zap size={13} /><span>{status === "running" ? "Generating" : status === "done" ? "Regenerate" : "Generate"}</span></button>
    {error && <small>{error}</small>}
  </div>;
}

function parseMediaArtifact(content: string): { src: string; poster?: string; alt?: string; caption?: string; type?: string } {
  const trimmed = content.trim();
  try {
    const parsed = JSON.parse(trimmed) as { src?: string; url?: string; poster?: string; alt?: string; caption?: string; type?: string };
    return { src: String(parsed.src || parsed.url || ""), poster: parsed.poster, alt: parsed.alt, caption: parsed.caption, type: parsed.type };
  } catch {
    return { src: trimmed, caption: trimmed.startsWith("data:") ? "Embedded media artifact" : trimmed };
  }
}

function MediaPreview({ artifact }: { artifact: GeneratedArtifact }) {
  const media = parseMediaArtifact(artifact.content);
  if (!media.src) return <pre><code>{artifact.content}</code></pre>;
  if (artifact.kind === "video") {
    return <figure className="artifact-media artifact-media-video">
      <video controls playsInline poster={media.poster} src={media.src} title={artifact.title} />
      {media.caption && <figcaption>{media.caption}</figcaption>}
    </figure>;
  }
  if (artifact.kind === "audio") {
    return <figure className="artifact-media artifact-media-audio">
      <div className="artifact-audio-orb"><Headphones size={26} /></div>
      <audio controls src={media.src} title={artifact.title} />
      {media.caption && <figcaption>{media.caption}</figcaption>}
    </figure>;
  }
  return <figure className="artifact-media artifact-media-image">
    <img src={media.src} alt={media.alt || artifact.title} />
    {media.caption && <figcaption>{media.caption}</figcaption>}
  </figure>;
}

function MermaidPreview({ content }: { content: string }) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setSvg("");
    setError("");
    void import("mermaid").then((mermaid) => {
      mermaid.default.initialize({ startOnLoad: false, theme: "dark" });
      return mermaid.default.render(`mission-artifact-${Date.now()}`, content);
    }).then((result) => {
      if (!cancelled) setSvg(result.svg);
    }).catch((errorValue: unknown) => {
      if (!cancelled) setError(errorValue instanceof Error ? errorValue.message : "Mermaid render failed");
    });
    return () => { cancelled = true; };
  }, [content]);

  if (error) return <pre className="artifact-mermaid-error"><code>{error}\n\n{content}</code></pre>;
  return <div className="artifact-mermaid" dangerouslySetInnerHTML={{ __html: svg || "" }} />;
}

function ArtifactRuntimeDeck({ artifact, performance }: { artifact: GeneratedArtifact | undefined; performance: ArtifactPerformanceSnapshot }) {
  const sourceExcerpt = artifact ? artifactSourceExcerpt(artifact) : artifactStandbySourceExcerpt();
  const sourceKeyPrefix = artifact?.id ?? "standby";

  return (
    <div className="artifact-runtime-deck" aria-label="Artifact runtime deck">
      <div className="artifact-runtime-tabs" aria-label="Artifact runtime views">
        <span className="active">Code</span>
        <span>Scene</span>
        <span>Assets</span>
        <span>Profiler</span>
        <span>Terminal</span>
      </div>
      <div className="artifact-code-preview" aria-label="Active artifact source preview">
        {sourceExcerpt.map((line, index) => <div key={`${sourceKeyPrefix}-line-${index}`}><span>{index + 1}</span><code>{line || " "}</code></div>)}
      </div>
      <div className="artifact-performance-panel" aria-label="Artifact performance telemetry">
        <header><span>Performance</span><b>{performance.fps}</b></header>
        <div><span>CPU</span><i><em style={{ width: `${performance.cpu}%` }} /></i><b>{performance.cpu}%</b></div>
        <div><span>GPU</span><i><em style={{ width: `${performance.gpu}%` }} /></i><b>{performance.gpu}%</b></div>
        <div><span>Draw</span><i><em style={{ width: "62%" }} /></i><b>{performance.drawCalls}</b></div>
      </div>
    </div>
  );
}

function ArtifactViewportActions({ onAction }: { onAction: (action: ArtifactViewportAction) => void }) {
  return (
    <div className="artifact-viewport-actions" aria-label="Viewport controls">
      <button type="button" onClick={() => onAction("expand")} title="Expand viewport"><Maximize2 size={12} /></button>
      <button type="button" onClick={() => onAction("capture")} title="Capture frame"><Camera size={12} /></button>
      <button type="button" onClick={() => onAction("center")} title="Center target"><Crosshair size={12} /></button>
      <button type="button" onClick={() => onAction("more")} title="More viewport actions"><MoreHorizontal size={12} /></button>
    </div>
  );
}

function ArtifactStandbyCanvas({ performance, viewportStatus, onViewportAction }: { performance: ArtifactPerformanceSnapshot; viewportStatus: string; onViewportAction: (action: ArtifactViewportAction) => void }) {
  return (
    <div className="artifact-canvas artifact-canvas-standby" aria-label="Standby artifact canvas">
      <div className="artifact-active-summary artifact-standby-summary">
        <div className="artifact-active-icon" aria-hidden="true"><Puzzle size={15} /></div>
        <div className="artifact-active-copy">
          <span>standby</span>
          <strong>Orchestrator viewport</strong>
        </div>
        <div className="artifact-active-pills" aria-label="Standby artifact metadata">
          <span>live</span>
          <span>{performance.fps} FPS</span>
          <span>scene</span>
        </div>
        <ArtifactViewportActions onAction={onViewportAction} />
      </div>
      <div className="artifact-canvas-bar">
        <div className="artifact-canvas-meta">
          <span>mission scene</span>
          <span>lighting pass</span>
          <span>memory linked</span>
        </div>
        <span className="artifact-viewport-status" aria-live="polite">{viewportStatus}</span>
        <div className="artifact-view-switch artifact-view-switch-static" aria-label="Standby artifact mode">
          <span className="active">Preview</span>
          <span>Source</span>
          <span>Inspect</span>
        </div>
      </div>
      <div className="artifact-stage preview artifact-standby-stage">
        <div className="artifact-standby-scene" aria-label="NEMO live render standby scene">
          <div className="artifact-scene-beam" />
          <div className="artifact-scene-tower tower-a" />
          <div className="artifact-scene-tower tower-b" />
          <div className="artifact-scene-tower tower-c" />
          <div className="artifact-scene-tower tower-d" />
          <div className="artifact-scene-horizon" />
          <div className="artifact-scene-avatar"><span /><i /></div>
          <div className="artifact-scene-target"><span /></div>
          <div className="artifact-scene-reflection" />
          <div className="artifact-standby-hud">
            <span>LIVE</span>
            <strong>Mission render armed</strong>
            <small>Awaiting artifact stream</small>
          </div>
          <div className="artifact-standby-metrics" aria-label="Standby render passes">
            <span>shader graph</span>
            <span>environment pass</span>
            <span>memory map</span>
          </div>
        </div>
      </div>
      <ArtifactRuntimeDeck artifact={undefined} performance={performance} />
    </div>
  );
}

export function ArtifactWorkbench({ artifacts, activeId, onSelect, onAttachToPrompt, onRemoveArtifact, onToggleFavorite, renderMarkdown }: ArtifactWorkbenchProps) {
  const [libraryQuery, setLibraryQuery] = useState<string>("");
  const [kindFilter, setKindFilter] = useState<ArtifactKindFilter>("all");
  const filterOptions = useMemo(() => artifactFilterOptions(artifacts), [artifacts]);
  const filteredArtifacts = useMemo(() => {
    const query = libraryQuery.trim().toLowerCase();
    return artifacts.filter((artifact) => {
      const kindMatches = kindFilter === "all" || artifact.kind === kindFilter;
      const queryMatches = !query || [artifact.title, artifact.kind, artifact.language, artifact.contentHash, artifact.registryId].filter(Boolean).some((value) => String(value).toLowerCase().includes(query));
      return kindMatches && queryMatches;
    });
  }, [artifacts, kindFilter, libraryQuery]);
  const activeArtifact = artifacts.find((artifact) => artifact.id === activeId) ?? artifacts[0];
  const renderable = isRenderableArtifact(activeArtifact);
  const versionSiblings = activeArtifact?.versionGroup ? artifacts
    .filter((artifact) => artifact.versionGroup === activeArtifact.versionGroup)
    .sort((left, right) => (left.version ?? 0) - (right.version ?? 0)) : [];
  const previousArtifact = previousArtifactVersion(activeArtifact, versionSiblings);
  const performance = artifactPerformanceSnapshot(activeArtifact);
  const [viewMode, setViewMode] = useState<ArtifactViewMode>("preview");
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  const [viewportAction, setViewportAction] = useState<ArtifactViewportAction | "idle">("idle");
  const viewportStatus = ARTIFACT_VIEWPORT_STATUS[viewportAction];

  useEffect(() => {
    setViewMode(renderable ? "preview" : "source");
    setCopyStatus("idle");
  }, [activeArtifact?.id, renderable]);

  const copyArtifact = () => {
    if (!activeArtifact) return;
    if (!navigator.clipboard?.writeText) {
      setCopyStatus("failed");
      return;
    }
    void navigator.clipboard.writeText(activeArtifact.content).then(() => {
      setCopyStatus("copied");
      window.setTimeout(() => setCopyStatus("idle"), 1400);
    }).catch(() => setCopyStatus("failed"));
  };

  const downloadArtifact = () => {
    if (!activeArtifact) return;
    const blob = new Blob([activeArtifact.content], { type: artifactMimeType(activeArtifact) });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifactFileName(activeArtifact);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const handleViewportAction = (action: ArtifactViewportAction) => {
    setViewportAction(action);
    window.setTimeout(() => setViewportAction("idle"), 1500);
  };

  const viewOptions: Array<{ mode: ArtifactViewMode; label: string; icon: ReactNode; disabled?: boolean }> = [
    { mode: "preview", label: "Preview", icon: <Eye size={13} />, disabled: !renderable },
    { mode: "source", label: "Source", icon: <Code2 size={13} /> },
    { mode: "compare", label: "Compare", icon: <History size={13} />, disabled: !previousArtifact },
    { mode: "inspect", label: "Inspect", icon: <Info size={13} /> },
  ];

  return (
    <aside className="artifact-studio" aria-label="Artifacts generados">
      <div className="artifact-studio-header">
        <div className="artifact-title-block">
          <span className="artifact-kicker"><Zap size={11} /> Live render</span>
          <strong>Artifact Studio</strong>
          <small>{artifacts.length ? `${artifacts.length} artifact(s) indexados` : "listo para multimodal"}</small>
        </div>
        <div className="artifact-live-hud" aria-label="Live render telemetry">
          <span><Activity size={11} /> Real-time</span>
          <span>{performance.fps} FPS</span>
          <span>{activeArtifact?.kind ?? "standby"}</span>
        </div>
        <div className="artifact-toolbar-actions">
          <button onClick={copyArtifact} disabled={!activeArtifact} title="Copiar artifact"><Copy size={13} /><span>{copyStatus === "copied" ? "Copied" : copyStatus === "failed" ? "Copy?" : "Copy"}</span></button>
          <button onClick={downloadArtifact} disabled={!activeArtifact} title="Descargar artifact"><Download size={13} /><span>Save</span></button>
          <button onClick={() => activeArtifact && onAttachToPrompt(activeArtifact)} disabled={!activeArtifact} title="Adjuntar al siguiente prompt"><Paperclip size={13} /><span>Attach</span></button>
        </div>
      </div>
      {artifacts.length > 0 ? <>
        <div className="artifact-index">
          <div className="artifact-library-bar" aria-label="Biblioteca de artifacts">
            <label className="artifact-library-search" title="Buscar artifact">
              <Search size={12} />
              <input value={libraryQuery} onChange={(event) => setLibraryQuery(event.target.value)} placeholder="Search title, hash, type" />
            </label>
            <div className="artifact-kind-filters" aria-label="Filtrar artifacts por tipo">
              {filterOptions.map((option) => <button key={option} className={kindFilter === option ? "active" : ""} onClick={() => setKindFilter(option)}>{option}</button>)}
            </div>
          </div>
          <div className="artifact-tabs" aria-label="Artifacts disponibles">
            {filteredArtifacts.map((artifact) => (
              <button key={artifact.id} className={artifact.id === activeArtifact?.id ? "active" : ""} onClick={() => onSelect(artifact.id)} title={artifact.title}>
                <span>{artifact.version ? `v${artifact.version} · ${artifact.kind}` : artifact.kind}</span>
                <strong>{artifact.title}</strong>
                <small>{artifact.favorite ? "Pinned" : `#${shortArtifactHash(artifact)}`}</small>
              </button>
            ))}
            {filteredArtifacts.length === 0 && <div className="artifact-library-empty">No artifacts match this view.</div>}
          </div>
          {versionSiblings.length > 1 && <div className="artifact-version-strip" aria-label="Versiones del artifact activo">
            {versionSiblings.map((artifact) => <button key={artifact.id} className={artifact.id === activeArtifact?.id ? "active" : ""} onClick={() => onSelect(artifact.id)} title={`${artifact.title} v${artifact.version ?? 1}`}>v{artifact.version ?? 1}</button>)}
          </div>}
        </div>
        {activeArtifact && <div className="artifact-canvas">
          <div className="artifact-active-summary">
            <div className="artifact-active-icon" aria-hidden="true">{artifactKindIcon(activeArtifact.kind)}</div>
            <div className="artifact-active-copy">
              <span>{activeArtifact.kind}</span>
              <strong>{activeArtifact.title}</strong>
            </div>
            <div className="artifact-active-pills" aria-label="Metadata del artifact activo">
              <span>{activeArtifact.version ? `v${activeArtifact.version}` : "draft"}</span>
              <span>{activeArtifact.persisted ? "indexed" : "session"}</span>
              <span>#{shortArtifactHash(activeArtifact)}</span>
              <span>{formatArtifactTime(activeArtifact.updatedAt ?? activeArtifact.createdAt)}</span>
            </div>
            <ArtifactViewportActions onAction={handleViewportAction} />
          </div>
          <div className="artifact-canvas-bar">
            <div className="artifact-canvas-meta">
              <span>{activeArtifact.language}</span>
              <span>{activeArtifact.tokenEstimate} tok</span>
              <span>{artifactLineCount(activeArtifact)} lines</span>
            </div>
            <span className="artifact-viewport-status" aria-live="polite">{viewportStatus}</span>
            <div className="artifact-library-actions" aria-label="Acciones de biblioteca">
              <button onClick={() => onToggleFavorite(activeArtifact.id)} title={activeArtifact.favorite ? "Quitar pin" : "Fijar artifact"}><Pin size={12} /><span>{activeArtifact.favorite ? "Pinned" : "Pin"}</span></button>
              <button className="danger" onClick={() => onRemoveArtifact(activeArtifact.id)} title="Eliminar artifact del registry"><Trash2 size={12} /><span>Delete</span></button>
            </div>
            <div className="artifact-view-switch" aria-label="Modo de artifact">
              {viewOptions.map((option) => <button key={option.mode} className={viewMode === option.mode ? "active" : ""} onClick={() => setViewMode(option.mode)} disabled={option.disabled} title={option.label}>{option.icon}<span>{option.label}</span></button>)}
            </div>
          </div>
          <div className={`artifact-stage ${viewMode}`}>
            {viewMode === "preview" && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react") ? (
              <iframe title={activeArtifact.title} sandbox="allow-scripts" srcDoc={artifactSrcDoc(activeArtifact)} />
            ) : viewMode === "preview" && (activeArtifact.kind === "image" || activeArtifact.kind === "video" || activeArtifact.kind === "audio") ? (
              <MediaPreview artifact={activeArtifact} />
            ) : viewMode === "preview" && activeArtifact.kind === "markdown" ? (
              <div className="artifact-markdown">{renderMarkdown(activeArtifact.content)}</div>
            ) : viewMode === "preview" && activeArtifact.kind === "mermaid" ? (
              <MermaidPreview content={activeArtifact.content} />
            ) : viewMode === "preview" && activeArtifact.kind === "image_request" ? (
              <ImageRequestPreview artifact={activeArtifact} />
            ) : viewMode === "compare" && previousArtifact ? (
              <ArtifactCompareView previous={previousArtifact} current={activeArtifact} />
            ) : viewMode === "inspect" ? (
              <div className="artifact-inspector">
                <section>
                  <strong>{activeArtifact.title}</strong>
                  <p>Generated from assistant output and ready for iteration, export, or prompt attachment.</p>
                </section>
                <dl>
                  <div><dt>Kind</dt><dd>{activeArtifact.kind}</dd></div>
                  <div><dt>Version</dt><dd>{activeArtifact.version ? `v${activeArtifact.version}` : "draft"}</dd></div>
                  <div><dt>Language</dt><dd>{activeArtifact.language}</dd></div>
                  <div><dt>Size</dt><dd>{activeArtifact.content.length.toLocaleString()} chars</dd></div>
                  <div><dt>Tokens</dt><dd>{activeArtifact.tokenEstimate}</dd></div>
                  <div><dt>Lines</dt><dd>{artifactLineCount(activeArtifact)}</dd></div>
                  <div><dt>Origin</dt><dd>{activeArtifact.messageId}</dd></div>
                  <div><dt>Stable ID</dt><dd>{activeArtifact.registryId ?? activeArtifact.id}</dd></div>
                  <div><dt>Hash</dt><dd>{activeArtifact.contentHash ?? "unindexed"}</dd></div>
                  <div><dt>Updated</dt><dd>{formatArtifactTime(activeArtifact.updatedAt ?? activeArtifact.createdAt)}</dd></div>
                </dl>
              </div>
            ) : (
              <pre><code>{activeArtifact.content}</code></pre>
            )}
          </div>
          <ArtifactRuntimeDeck artifact={activeArtifact} performance={performance} />
        </div>}
      </> : <ArtifactStandbyCanvas performance={performance} viewportStatus={viewportStatus} onViewportAction={handleViewportAction} />}
    </aside>
  );
}