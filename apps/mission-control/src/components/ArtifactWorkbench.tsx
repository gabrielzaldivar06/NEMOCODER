import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Braces, Code2, Copy, Download, Eye, FileText, Film, Globe, Headphones, History, Image, Info, Layers3, Maximize2, Minimize2, Paperclip, Pin, Puzzle, RefreshCw, Search, Trash2, Zap } from "lucide-react";
import { buildArtifactLineDiff, type GeneratedArtifact, type GeneratedArtifactKind } from "../services/artifactUtils";

type ArtifactViewMode = "preview" | "source" | "inspect" | "compare";

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
  return Boolean(artifact && ["html", "svg", "markdown", "mermaid", "react", "image", "video", "audio", "image_request", "browser"].includes(artifact.kind));
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

function artifactKindIcon(kind: GeneratedArtifactKind): ReactNode {
  if (kind === "image_request") return <Image size={15} />;
  if (kind === "image") return <Image size={15} />;
  if (kind === "video") return <Film size={15} />;
  if (kind === "audio") return <Headphones size={15} />;
  if (kind === "json") return <Braces size={15} />;
  if (kind === "markdown") return <FileText size={15} />;
  if (kind === "html" || kind === "svg" || kind === "react") return <Layers3 size={15} />;
  if (kind === "browser") return <Globe size={15} />;
  return <Code2 size={15} />;
}

// ---------------------------------------------------------------------------
// BrowserLiveView — interactive embedded browser via screenshot polling
// ---------------------------------------------------------------------------

type BrowserInfo = { session_id: string; url: string; title: string; step_count: number; active: boolean };

function parseBrowserArtifact(content: string): { session_id: string; url: string; task: string; final_screenshot?: string } | null {
  try {
    const parsed = JSON.parse(content) as { session_id?: string; url?: string; task?: string; final_screenshot?: string };
    if (parsed && parsed.session_id) return { session_id: parsed.session_id, url: parsed.url ?? "", task: parsed.task ?? "", final_screenshot: parsed.final_screenshot };
  } catch { /* ignore */ }
  return null;
}

function BrowserLiveView({ content }: { content: string }) {
  const parsed = parseBrowserArtifact(content);
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [urlBar, setUrlBar] = useState(parsed?.url ?? "");
  const [info, setInfo] = useState<BrowserInfo | null>(null);
  const [connected, setConnected] = useState(false);
  const [frameTs, setFrameTs] = useState(0);
  const sessionId = parsed?.session_id ?? "";

  // Poll frames at ~5 fps while session is alive
  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    const tick = async () => {
      if (!active) return;
      try {
        const res = await fetch(`/api/browser/${sessionId}/frame?t=${Date.now()}`);
        if (res.ok && imgRef.current) {
          const blob = await res.blob();
          const objUrl = URL.createObjectURL(blob);
          const prev = imgRef.current.src;
          imgRef.current.src = objUrl;
          if (prev.startsWith("blob:")) URL.revokeObjectURL(prev);
          setConnected(true);
          setFrameTs(Date.now());
        } else if (res.status === 404) {
          setConnected(false);
        }
      } catch { /* network error */ }
      if (active) window.setTimeout(tick, 200);
    };
    void tick();
    return () => { active = false; };
  }, [sessionId]);

  // Poll info (URL / title) every 1 s
  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    const poll = async () => {
      if (!active) return;
      try {
        const res = await fetch(`/api/browser/${sessionId}/info`);
        if (res.ok) {
          const data = await res.json() as BrowserInfo;
          setInfo(data);
          setUrlBar(data.url || urlBar);
        }
      } catch { /* ignore */ }
      if (active) window.setTimeout(poll, 1000);
    };
    void poll();
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const sendInteract = useCallback((body: Record<string, unknown>) => {
    if (!sessionId || !connected) return;
    fetch(`/api/browser/${sessionId}/interact`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => null);
  }, [sessionId, connected]);

  // Map click position from displayed img size → browser viewport (1280×720)
  const handleImgClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 1280;
    const y = ((e.clientY - rect.top) / rect.height) * 720;
    sendInteract({ action: "click", x, y });
  };

  const handleImgWheel = (e: React.WheelEvent<HTMLImageElement>) => {
    e.preventDefault();
    sendInteract({ action: "scroll", delta_x: 0, delta_y: e.deltaY });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.target instanceof HTMLInputElement) return; // let URL bar have focus
    e.preventDefault();
    if (e.key.length === 1) {
      sendInteract({ action: "type", text: e.key });
    } else {
      sendInteract({ action: "key", key: e.key });
    }
  };

  const navigate = (url: string) => {
    const target = url.startsWith("http") ? url : `https://${url}`;
    setUrlBar(target);
    sendInteract({ action: "navigate", url: target });
  };

  if (!parsed) {
    return <div className="browser-live-error">Invalid browser artifact</div>;
  }

  return (
    <div
      ref={containerRef}
      className="browser-live-view"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label="Embedded browser"
    >
      {/* Toolbar */}
      <div className="browser-toolbar">
        <button title="Back" onClick={() => sendInteract({ action: "key", key: "Alt+ArrowLeft" })}><ArrowLeft size={13} /></button>
        <button title="Forward" onClick={() => sendInteract({ action: "key", key: "Alt+ArrowRight" })}><ArrowRight size={13} /></button>
        <button title="Refresh" onClick={() => sendInteract({ action: "key", key: "F5" })}><RefreshCw size={13} /></button>
        <input
          className="browser-url-bar"
          value={urlBar}
          onChange={(e) => setUrlBar(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") navigate(urlBar); e.stopPropagation(); }}
          spellCheck={false}
          aria-label="URL"
        />
        <span className="browser-status-dot" title={connected ? `Live · step ${info?.step_count ?? 0}` : "Disconnected"}>
          {connected ? "●" : "○"}
        </span>
      </div>
      {/* Frame */}
      <div className="browser-frame-wrap">
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
        <img
          ref={imgRef}
          alt={info?.title || parsed.task || "Browser"}
          className="browser-frame-img"
          onClick={handleImgClick}
          onWheel={handleImgWheel}
          draggable={false}
          style={{ cursor: connected ? "crosshair" : "default" }}
          data-ts={frameTs}
        />
        {!connected && parsed?.final_screenshot && (
          <img src={parsed.final_screenshot} alt="Final state" className="browser-frame-img" draggable={false} />
        )}
        {!connected && !parsed?.final_screenshot && (
          <div className="browser-offline-overlay">
            <Globe size={28} />
            <span>Browser session ended or not yet started</span>
          </div>
        )}
      </div>
      {/* Status footer */}
      {info && (
        <div className="browser-footer">
          <span>{info.title}</span>
          <span>Step {info.step_count}</span>
        </div>
      )}
    </div>
  );
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

function FullscreenButton({ stageRef }: { stageRef: React.RefObject<HTMLDivElement | null> }) {
  const [isFs, setIsFs] = useState(false);
  useEffect(() => {
    const handler = () => setIsFs(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);
  const toggle = () => {
    if (!stageRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => null);
    } else {
      stageRef.current.requestFullscreen().catch(() => null);
    }
  };
  return (
    <button type="button" className="artifact-fullscreen-btn" onClick={toggle} title={isFs ? "Exit fullscreen" : "Fullscreen"}>
      {isFs ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
      <span>{isFs ? "Exit" : "Full"}</span>
    </button>
  );
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

function ArtifactStandbyCanvas() {
  return (
    <div className="artifact-canvas artifact-canvas-standby" aria-label="Standby artifact canvas">
      <div className="artifact-standby-placeholder">
        <Puzzle size={32} />
        <strong>Artifact Studio</strong>
        <span>Los artifacts generados por el agente aparecerán aquí automáticamente.</span>
      </div>
    </div>
  );
}

export function ArtifactWorkbench({ artifacts, activeId, onSelect, onAttachToPrompt, onRemoveArtifact, onToggleFavorite, renderMarkdown }: ArtifactWorkbenchProps) {
  const [libraryQuery, setLibraryQuery] = useState<string>("");
  const [kindFilter, setKindFilter] = useState<ArtifactKindFilter>("all");
  const stageRef = useRef<HTMLDivElement>(null);
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
  const [viewMode, setViewMode] = useState<ArtifactViewMode>("preview");
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");

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
        <div className="artifact-live-hud" aria-label="Artifact kind">
          <span>{activeArtifact?.kind ?? "standby"}</span>
          {activeArtifact && <span>{artifactLineCount(activeArtifact)} lines</span>}
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
            <FullscreenButton stageRef={stageRef} />
          </div>
          <div className="artifact-canvas-bar">
            <div className="artifact-canvas-meta">
              <span>{activeArtifact.language}</span>
              <span>{activeArtifact.tokenEstimate} tok</span>
              <span>{artifactLineCount(activeArtifact)} lines</span>
            </div>
            <div className="artifact-library-actions" aria-label="Acciones de biblioteca">
              <button onClick={() => onToggleFavorite(activeArtifact.id)} title={activeArtifact.favorite ? "Quitar pin" : "Fijar artifact"}><Pin size={12} /><span>{activeArtifact.favorite ? "Pinned" : "Pin"}</span></button>
              <button className="danger" onClick={() => onRemoveArtifact(activeArtifact.id)} title="Eliminar artifact del registry"><Trash2 size={12} /><span>Delete</span></button>
            </div>
            <div className="artifact-view-switch" aria-label="Modo de artifact">
              {viewOptions.map((option) => <button key={option.mode} className={viewMode === option.mode ? "active" : ""} onClick={() => setViewMode(option.mode)} disabled={option.disabled} title={option.label}>{option.icon}<span>{option.label}</span></button>)}
            </div>
          </div>
          <div ref={stageRef} className={`artifact-stage ${viewMode}`}>
            {viewMode === "preview" && activeArtifact.kind === "browser" ? (
              <BrowserLiveView content={activeArtifact.content} />
            ) : viewMode === "preview" && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react") ? (
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
        </div>}
      </> : <ArtifactStandbyCanvas />}
    </aside>
  );
}
