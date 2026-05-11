import { useEffect, useState, type ReactNode } from "react";
import { Code2, Copy, Download, Eye, Info, Paperclip, Puzzle, Zap } from "lucide-react";

export type GeneratedArtifactKind = "html" | "svg" | "markdown" | "json" | "code";

export type GeneratedArtifact = {
  id: string;
  messageId: string;
  title: string;
  kind: GeneratedArtifactKind;
  language: string;
  content: string;
  tokenEstimate: number;
  registryId?: string;
  contentHash?: string;
  version?: number;
  versionGroup?: string;
  createdAt?: string;
  updatedAt?: string;
  persisted?: boolean;
};

type ArtifactViewMode = "preview" | "source" | "inspect";

type ArtifactMessageSource = {
  id: string;
  role: string;
  content: string;
};

type ArtifactWorkbenchProps = {
  artifacts: GeneratedArtifact[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onAttachToPrompt: (artifact: GeneratedArtifact) => void;
  renderMarkdown: (content: string) => ReactNode;
};

function estimateArtifactTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

export function artifactKindFromBlock(language: string, content: string): GeneratedArtifactKind {
  const normalized = language.toLowerCase().trim();
  const trimmed = content.trimStart().toLowerCase();
  if (["html", "htm"].includes(normalized) || trimmed.startsWith("<!doctype html") || trimmed.startsWith("<html")) return "html";
  if (normalized === "svg" || trimmed.startsWith("<svg")) return "svg";
  if (["md", "markdown", "mdx"].includes(normalized)) return "markdown";
  if (["json", "jsonc"].includes(normalized)) return "json";
  return "code";
}

function artifactTitle(kind: GeneratedArtifactKind, language: string, index: number): string {
  const label = kind === "html" ? "Interactive HTML" : kind === "svg" ? "SVG Scene" : kind === "markdown" ? "Document" : kind === "json" ? "Data" : "Code";
  const suffix = language && language !== kind ? ` / ${language}` : "";
  return `${label}${suffix} ${index + 1}`;
}

export function collectGeneratedArtifacts(messages: ArtifactMessageSource[]): GeneratedArtifact[] {
  const artifacts: GeneratedArtifact[] = [];
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    const blocks = message.content.matchAll(/```([^\n`]*)\n([\s\S]*?)```/g);
    let localIndex = 0;
    for (const match of blocks) {
      const language = match[1].trim().split(/\s+/)[0]?.toLowerCase() || "text";
      const content = match[2].trim();
      if (!content) continue;
      const kind = artifactKindFromBlock(language, content);
      artifacts.unshift({
        id: `${message.id}-artifact-${localIndex}`,
        messageId: message.id,
        title: artifactTitle(kind, language, localIndex),
        kind,
        language,
        content,
        tokenEstimate: estimateArtifactTokens(content),
      });
      localIndex += 1;
    }
  }
  return artifacts.slice(0, 8);
}

export function stripGeneratedArtifactBlocks(content: string): string {
  const stripped = content.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (block, language, body) => {
    const kind = artifactKindFromBlock(String(language).trim().split(/\s+/)[0] || "text", String(body));
    return ["html", "svg", "markdown", "json", "code"].includes(kind) ? "" : block;
  }).trim();
  return stripped || "Artifact generado.";
}

function artifactSrcDoc(artifact: GeneratedArtifact): string {
  if (artifact.kind === "svg") {
    return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;min-height:100%;display:grid;place-items:center;background:#07101f;color:#e5edf8}svg{max-width:100%;max-height:100%;}</style></head><body>${artifact.content}</body></html>`;
  }
  if (artifact.kind === "html") return artifact.content;
  return "";
}

function isRenderableArtifact(artifact: GeneratedArtifact | undefined): boolean {
  return Boolean(artifact && ["html", "svg", "markdown"].includes(artifact.kind));
}

function artifactFileExtension(artifact: GeneratedArtifact): string {
  if (artifact.kind === "html") return "html";
  if (artifact.kind === "svg") return "svg";
  if (artifact.kind === "markdown") return "md";
  if (artifact.kind === "json") return "json";
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

export function ArtifactWorkbench({ artifacts, activeId, onSelect, onAttachToPrompt, renderMarkdown }: ArtifactWorkbenchProps) {
  const activeArtifact = artifacts.find((artifact) => artifact.id === activeId) ?? artifacts[0];
  const renderable = isRenderableArtifact(activeArtifact);
  const versionSiblings = activeArtifact?.versionGroup ? artifacts
    .filter((artifact) => artifact.versionGroup === activeArtifact.versionGroup)
    .sort((left, right) => (left.version ?? 0) - (right.version ?? 0)) : [];
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
            {viewMode === "preview" && (activeArtifact.kind === "html" || activeArtifact.kind === "svg") ? (
              <iframe title={activeArtifact.title} sandbox="allow-scripts" srcDoc={artifactSrcDoc(activeArtifact)} />
            ) : viewMode === "preview" && activeArtifact.kind === "markdown" ? (
              <div className="artifact-markdown">{renderMarkdown(activeArtifact.content)}</div>
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
        <span>HTML / SVG / Markdown / JSON / Code</span>
      </div>}
    </aside>
  );
}