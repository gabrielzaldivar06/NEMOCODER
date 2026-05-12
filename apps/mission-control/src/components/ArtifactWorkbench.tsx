import { useEffect, useState, type ReactNode } from "react";
import { Braces, Code2, Copy, Download, Eye, FileText, History, Image, Info, Layers3, Paperclip, Puzzle, Zap } from "lucide-react";
import { buildArtifactLineDiff, type GeneratedArtifact, type GeneratedArtifactKind } from "../services/artifactUtils";

type ArtifactViewMode = "preview" | "source" | "inspect" | "compare";

type ArtifactWorkbenchProps = {
  artifacts: GeneratedArtifact[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onAttachToPrompt: (artifact: GeneratedArtifact) => void;
  renderMarkdown: (content: string) => ReactNode;
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
  return Boolean(artifact && ["html", "svg", "markdown", "mermaid", "react", "image_request"].includes(artifact.kind));
}

function artifactFileExtension(artifact: GeneratedArtifact): string {
  if (artifact.kind === "html") return "html";
  if (artifact.kind === "svg") return "svg";
  if (artifact.kind === "markdown") return "md";
  if (artifact.kind === "json") return "json";
  if (artifact.kind === "mermaid") return "mmd";
  if (artifact.kind === "react") return "jsx";
  return artifact.language && artifact.language !== "text" ? artifact.language.replace(/[^a-z0-9]+/gi, "").toLowerCase() || "txt" : "txt";
}

function artifactMimeType(artifact: GeneratedArtifact): string {
  if (artifact.kind === "html") return "text/html";
  if (artifact.kind === "svg") return "image/svg+xml";
  if (artifact.kind === "markdown") return "text/markdown";
  if (artifact.kind === "json") return "application/json";
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

export function ArtifactWorkbench({ artifacts, activeId, onSelect, onAttachToPrompt, renderMarkdown }: ArtifactWorkbenchProps) {
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
        <div className="artifact-toolbar-actions">
          <button onClick={copyArtifact} disabled={!activeArtifact} title="Copiar artifact"><Copy size={13} /><span>{copyStatus === "copied" ? "Copied" : copyStatus === "failed" ? "Copy?" : "Copy"}</span></button>
          <button onClick={downloadArtifact} disabled={!activeArtifact} title="Descargar artifact"><Download size={13} /><span>Save</span></button>
          <button onClick={() => activeArtifact && onAttachToPrompt(activeArtifact)} disabled={!activeArtifact} title="Adjuntar al siguiente prompt"><Paperclip size={13} /><span>Attach</span></button>
        </div>
      </div>
      {artifacts.length > 0 ? <>
        <div className="artifact-index">
          <div className="artifact-tabs" aria-label="Artifacts disponibles">
            {artifacts.map((artifact) => (
              <button key={artifact.id} className={artifact.id === activeArtifact?.id ? "active" : ""} onClick={() => onSelect(artifact.id)} title={artifact.title}>
                <span>{artifact.version ? `v${artifact.version} · ${artifact.kind}` : artifact.kind}</span>
                <strong>{artifact.title}</strong>
              </button>
            ))}
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
          </div>
          <div className="artifact-canvas-bar">
            <div className="artifact-canvas-meta">
              <span>{activeArtifact.language}</span>
              <span>{activeArtifact.tokenEstimate} tok</span>
              <span>{artifactLineCount(activeArtifact)} lines</span>
            </div>
            <div className="artifact-view-switch" aria-label="Modo de artifact">
              {viewOptions.map((option) => <button key={option.mode} className={viewMode === option.mode ? "active" : ""} onClick={() => setViewMode(option.mode)} disabled={option.disabled} title={option.label}>{option.icon}<span>{option.label}</span></button>)}
            </div>
          </div>
          <div className={`artifact-stage ${viewMode}`}>
            {viewMode === "preview" && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react") ? (
              <iframe title={activeArtifact.title} sandbox="allow-scripts" srcDoc={artifactSrcDoc(activeArtifact)} />
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
      </> : <div className="artifact-empty">
        <Puzzle size={18} aria-hidden="true" />
        <strong>Sin artifact activo</strong>
        <span>Pide una interfaz, dashboard, diagrama o reporte visual para abrir el canvas.</span>
        <div className="artifact-empty-prompts" aria-label="Ejemplos de artifact">
          <code>html_artifact dashboard</code>
          <code>mermaid architecture</code>
          <code>image_request concept</code>
        </div>
      </div>}
    </aside>
  );
}