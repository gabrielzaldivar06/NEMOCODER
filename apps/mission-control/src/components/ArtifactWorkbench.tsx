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
  onClearAll?: () => void;
  renderMarkdown: (content: string) => ReactNode;
};

type ArtifactKindFilter = "all" | GeneratedArtifactKind;

// In-memory storage polyfill injected into html_artifact iframes.
// sandbox="allow-scripts" gives the iframe a null origin, making localStorage throw SecurityError.
const _STORAGE_POLYFILL = `<script>(function(){var _s={};var P={getItem:function(k){return Object.prototype.hasOwnProperty.call(_s,k)?_s[k]:null},setItem:function(k,v){_s[String(k)]=String(v)},removeItem:function(k){delete _s[String(k)]},clear:function(){_s={}},get length(){return Object.keys(_s).length},key:function(i){return Object.keys(_s)[i]??null}};try{localStorage.getItem('__p')}catch(e){try{Object.defineProperty(window,'localStorage',{value:P,writable:false,configurable:true})}catch(_){}try{Object.defineProperty(window,'sessionStorage',{value:P,writable:false,configurable:true})}catch(_){}}}());<\/script>`;

// Console interceptor: overrides console.* in sandboxed iframes and relays messages to parent via postMessage.
const _CONSOLE_INTERCEPTOR_SCRIPT = `<script>(function(){var _s=function(l,a){try{window.parent.postMessage({type:'console',level:l,args:Array.prototype.slice.call(a).map(function(x){try{return typeof x==='object'&&x!==null?JSON.stringify(x):String(x)}catch(e){return'[obj]'}})},'*')}catch(e){}};['log','warn','error','info','debug'].forEach(function(m){var o=console[m];console[m]=function(){_s(m,arguments);if(o)o.apply(console,arguments);};});window.onerror=function(msg,_,ln){_s('error',[msg+(ln?' L'+ln:'')]);return false;};window.addEventListener('unhandledrejection',function(e){_s('error',['Rejection: '+String(e.reason)]);});}());<\/script>`;

function injectPolyfill(html: string): string {
  const injections = _STORAGE_POLYFILL + _CONSOLE_INTERCEPTOR_SCRIPT;
  if (html.includes("</head>")) return html.replace("</head>", injections + "</head>");
  if (html.includes("<body")) return html.replace("<body", injections + "<body");
  return injections + html;
}

// Closes mid-stream HTML so the iframe renders something instead of waiting for the
// final tag. The browser is forgiving — even a partial <body> with no </body> will
// render — but explicitly closing the structure prevents some content from being
// hidden behind a missing root tag.
function completePartialHtml(content: string): string {
  let html = content;
  // Detect if we're in the middle of a tag (e.g. "<div clas") and close it cleanly
  // so the parser doesn't keep buffering. Only do this when we have an unclosed tag.
  const lastLt = html.lastIndexOf("<");
  const lastGt = html.lastIndexOf(">");
  if (lastLt > lastGt) {
    html = html.slice(0, lastLt);
  }
  if (!/<\/body>/i.test(html) && /<body/i.test(html)) html += "</body>";
  if (!/<\/html>/i.test(html) && /<html/i.test(html)) html += "</html>";
  return html;
}

function artifactSrcDoc(artifact: GeneratedArtifact): string {
  if (artifact.kind === "svg") {
    return `<!doctype html><html><head><meta charset="utf-8">${_CONSOLE_INTERCEPTOR_SCRIPT}<style>html,body{margin:0;min-height:100%;display:grid;place-items:center;background:#07101f;color:#e5edf8}svg{max-width:100%;max-height:100%;}</style></head><body>${artifact.content}</body></html>`;
  }
  if (artifact.kind === "html") {
    const body = artifact.streaming ? completePartialHtml(artifact.content) : artifact.content;
    return injectPolyfill(body);
  }
  if (artifact.kind === "react") {
    return `<!doctype html><html><head><meta charset="utf-8">${_CONSOLE_INTERCEPTOR_SCRIPT}<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script><script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script><script src="https://unpkg.com/@babel/standalone/babel.min.js"></script><style>html,body,#root{margin:0;min-height:100%;background:#07101f;color:#e5edf8;font-family:Inter,system-ui,sans-serif}</style></head><body><div id="root"></div><script type="text/babel">${artifact.content}\nReactDOM.render(React.createElement(App), document.getElementById("root"));</script></body></html>`;
  }
  return "";
}

function codeHighlightSrcDoc(artifact: GeneratedArtifact): string {
  const lang = artifact.language && artifact.language !== "text" ? artifact.language : "plaintext";
  const escaped = artifact.content.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark-dimmed.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
  html,body{margin:0;padding:0;background:#0d1117;color:#e6edf3;font-family:'Fira Code',Consolas,'Courier New',monospace;font-size:13px;line-height:1.65}
  pre{margin:0;padding:14px 16px;overflow:auto;min-height:100vh;box-sizing:border-box}
  code.hljs{background:transparent;padding:0;font-size:inherit;line-height:inherit}
  ::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:#0d1117}::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
</style>
${_CONSOLE_INTERCEPTOR_SCRIPT}
</head><body>
<pre><code class="language-${lang}">${escaped}</code></pre>
<script>hljs.highlightAll();<\/script>
</body></html>`;
}

function isRenderableArtifact(artifact: GeneratedArtifact | undefined): boolean {
  return Boolean(artifact && ["html", "svg", "markdown", "mermaid", "react", "code", "json", "image", "video", "audio", "image_request", "browser"].includes(artifact.kind));
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
    const parsed = JSON.parse(content) as { session_id?: unknown; url?: unknown; task?: unknown; final_screenshot?: unknown };
    if (parsed && typeof parsed.session_id === "string" && parsed.session_id)
      return { session_id: parsed.session_id, url: typeof parsed.url === "string" ? parsed.url : "", task: typeof parsed.task === "string" ? parsed.task : "", final_screenshot: typeof parsed.final_screenshot === "string" ? parsed.final_screenshot : undefined };
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
    if (rect.width === 0 || rect.height === 0) return;
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

  const generateImage = useCallback(() => {
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
  }, [requestPayload]);

  // Auto-generate as soon as the artifact appears — no manual button click needed.
  useEffect(() => { generateImage(); }, [artifact.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="artifact-image-request">
    {generatedUrl ? <img src={generatedUrl} alt={String(requestPayload.prompt || "Generated artifact")} /> : <Image size={30} />}
    <strong>{generatedUrl ? "Generated image" : status === "running" ? "Generating…" : "Image request"}</strong>
    <p>{prompt}</p>
    <button onClick={generateImage} disabled={status === "running"} title="Regenerar imagen"><Zap size={13} /><span>{status === "done" ? "Regenerate" : status === "running" ? "Generating" : "Generate"}</span></button>
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

let _mermaidInitialized = false;

function MermaidPreview({ content }: { content: string }) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setSvg("");
    setError("");
    void import("mermaid").then((mermaid) => {
      if (!_mermaidInitialized) {
        mermaid.default.initialize({ startOnLoad: false, theme: "dark" });
        _mermaidInitialized = true;
      }
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

// ---------------------------------------------------------------------------
// RunOutputPanel — shows stdout/stderr/images after inline code execution
// ---------------------------------------------------------------------------

type RunOutput = {
  exec_ok: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  images: { name: string; data_url: string }[];
};

function RunOutputPanel({ output, onClear }: { output: RunOutput; onClear: () => void }) {
  return (
    <div className="artifact-run-panel">
      <div className="artifact-run-header">
        <span className={output.exec_ok ? "artifact-run-badge ok" : "artifact-run-badge fail"}>
          {output.exec_ok ? "✓" : "✗"} exit {output.exit_code}
        </span>
        <span className="artifact-run-time">{output.duration_ms}ms</span>
        <button onClick={onClear} className="artifact-run-clear" title="Clear output">✕</button>
      </div>
      <div className="artifact-run-body">
        {output.stdout && <pre className="artifact-run-stdout">{output.stdout}</pre>}
        {output.stderr && <pre className="artifact-run-stderr">{output.stderr}</pre>}
        {output.images.map((img) => (
          <img key={img.name} src={img.data_url} alt={img.name} className="artifact-run-image" />
        ))}
        {!output.stdout && !output.stderr && output.images.length === 0 && (
          <div className="artifact-run-empty">No output — script ran silently.</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConsolePanel — captures console.* output from sandboxed artifact iframes
// ---------------------------------------------------------------------------

type ConsoleEntry = { level: string; args: string[]; ts: number };

function ConsolePanel({ entries, onClear, onClose }: { entries: ConsoleEntry[]; onClear: () => void; onClose: () => void }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [entries.length]);
  return (
    <div className="artifact-console-panel">
      <div className="artifact-console-header">
        <span>Console</span>
        {entries.length > 0 && <span className="artifact-console-count">{entries.length}</span>}
        <button onClick={onClear} title="Clear console" className="artifact-console-clear">Clear</button>
        <button onClick={onClose} title="Close console" className="artifact-console-close">✕</button>
      </div>
      <div className="artifact-console-body">
        {entries.length === 0 && <div className="artifact-console-empty">No output yet. console.log() from the artifact appears here.</div>}
        {entries.map((entry, index) => (
          <div key={index} className={`artifact-console-line artifact-console-${entry.level}`}>
            <span className="artifact-console-level">{entry.level}</span>
            <span className="artifact-console-text">{entry.args.join(" ")}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// JsonExplorer — interactive collapsible JSON tree
// ---------------------------------------------------------------------------

function JsonNode({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [collapsed, setCollapsed] = useState(depth > 1);

  if (value === null) return <span className="json-null">null</span>;
  if (typeof value === "boolean") return <span className="json-bool">{String(value)}</span>;
  if (typeof value === "number") return <span className="json-num">{String(value)}</span>;
  if (typeof value === "string") {
    const display = value.length > 120 ? `"${value.slice(0, 120)}…"` : `"${value}"`;
    return <span className="json-str" title={value.length > 120 ? value : undefined}>{display}</span>;
  }
  if (depth > 18) return <span className="json-deep">[…]</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="json-bracket">[]</span>;
    return (
      <span className="json-expandable">
        <button className="json-toggle" onClick={() => setCollapsed((c) => !c)} title={collapsed ? "Expand" : "Collapse"}>{collapsed ? "▶" : "▼"}</button>
        <span className="json-bracket">[</span>
        {collapsed ? (
          <button className="json-summary" onClick={() => setCollapsed(false)}>{value.length} item{value.length !== 1 ? "s" : ""}</button>
        ) : (
          <>
            <div className="json-children">
              {value.map((item, i) => (
                <div key={i} className="json-row">
                  <span className="json-index">{i}</span>
                  <span className="json-colon">: </span>
                  <JsonNode value={item} depth={depth + 1} />
                  {i < value.length - 1 && <span className="json-comma">,</span>}
                </div>
              ))}
            </div>
            <span className="json-bracket">]</span>
          </>
        )}
      </span>
    );
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="json-bracket">{"{}"}</span>;
    return (
      <span className="json-expandable">
        <button className="json-toggle" onClick={() => setCollapsed((c) => !c)} title={collapsed ? "Expand" : "Collapse"}>{collapsed ? "▶" : "▼"}</button>
        <span className="json-bracket">{"{"}</span>
        {collapsed ? (
          <button className="json-summary" onClick={() => setCollapsed(false)}>{entries.length} key{entries.length !== 1 ? "s" : ""}</button>
        ) : (
          <>
            <div className="json-children">
              {entries.map(([key, val], i) => (
                <div key={key} className="json-row">
                  <span className="json-key">"{key}"</span>
                  <span className="json-colon">: </span>
                  <JsonNode value={val} depth={depth + 1} />
                  {i < entries.length - 1 && <span className="json-comma">,</span>}
                </div>
              ))}
            </div>
            <span className="json-bracket">{"}"}</span>
          </>
        )}
      </span>
    );
  }

  return <span className="json-unknown">{String(value)}</span>;
}

function JsonExplorer({ content }: { content: string }) {
  const [parsed, parseError] = useMemo<[unknown, string | null]>(() => {
    try { return [JSON.parse(content), null]; }
    catch (e) { return [null, e instanceof Error ? e.message : "Invalid JSON"]; }
  }, [content]);

  if (parseError) return <pre className="artifact-json-error"><code>JSON parse error: {parseError}{"\n\n"}{content}</code></pre>;
  return <div className="artifact-json-explorer"><JsonNode value={parsed} depth={0} /></div>;
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

export function ArtifactWorkbench({ artifacts, activeId, onSelect, onAttachToPrompt, onRemoveArtifact, onToggleFavorite, onClearAll, renderMarkdown }: ArtifactWorkbenchProps) {
  const [libraryQuery, setLibraryQuery] = useState<string>("");
  const [kindFilter, setKindFilter] = useState<ArtifactKindFilter>("all");
  const stageRef = useRef<HTMLDivElement>(null);
  const [consoleEntries, setConsoleEntries] = useState<ConsoleEntry[]>([]);
  const [showConsole, setShowConsole] = useState(false);

  // Receive console messages from sandboxed iframes
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (!e.data || (e.data as Record<string, unknown>).type !== "console") return;
      const { level, args } = e.data as { level?: unknown; args?: unknown };
      setConsoleEntries((prev) => [
        ...prev.slice(-199),
        { level: String(level ?? "log"), args: Array.isArray(args) ? (args as unknown[]).map(String) : [String(args)], ts: Date.now() },
      ]);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  // Clear console entries when switching artifacts
  useEffect(() => { setConsoleEntries([]); }, [activeId]);

  // REPL state
  const [editableCode, setEditableCode] = useState<string | null>(null);
  const [isEditMode, setIsEditMode] = useState(false);
  const [runOutput, setRunOutput] = useState<RunOutput | null>(null);
  const [runRunning, setRunRunning] = useState(false);

  // Reset REPL state when switching artifacts
  useEffect(() => {
    setEditableCode(null);
    setIsEditMode(false);
    setRunOutput(null);
  }, [activeId]);

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
  // Debounce the srcDoc used by the live iframe so a streaming artifact does not
  // tear down and re-mount the iframe on every single token chunk. Without this
  // the iframe shows a blank flash for the whole stream and the user only sees
  // the final HTML at the end. We update the debounced value every 200ms which
  // is fast enough to feel live but slow enough for the renderer to flush.
  const [debouncedSrcDoc, setDebouncedSrcDoc] = useState<string>(() => activeArtifact ? artifactSrcDoc(activeArtifact) : "");
  const _lastSrcKeyRef = useRef<string>("");
  useEffect(() => {
    if (!activeArtifact) {
      setDebouncedSrcDoc("");
      _lastSrcKeyRef.current = "";
      return;
    }
    // Streaming updates every token; flush at ~200ms. Finalized artifacts update
    // immediately so the user gets the polished render the moment it's done.
    const isStreaming = Boolean(activeArtifact.streaming);
    const key = `${activeArtifact.id}-${activeArtifact.content.length}-${isStreaming ? "s" : "f"}`;
    if (key === _lastSrcKeyRef.current) return;
    if (!isStreaming) {
      _lastSrcKeyRef.current = key;
      setDebouncedSrcDoc(artifactSrcDoc(activeArtifact));
      return;
    }
    const timer = window.setTimeout(() => {
      _lastSrcKeyRef.current = key;
      setDebouncedSrcDoc(artifactSrcDoc(activeArtifact));
    }, 200);
    return () => window.clearTimeout(timer);
  }, [activeArtifact?.id, activeArtifact?.content, activeArtifact?.streaming]);
  const versionSiblings = activeArtifact?.versionGroup ? artifacts
    .filter((artifact) => artifact.versionGroup === activeArtifact.versionGroup)
    .sort((left, right) => (left.version ?? 0) - (right.version ?? 0)) : [];
  const previousArtifact = previousArtifactVersion(activeArtifact, versionSiblings);

  const RUNNABLE_LANGS = new Set(["python", "py", "javascript", "js", "bash", "sh", "sql"]);
  const isRunnable = Boolean(activeArtifact?.kind === "code" && RUNNABLE_LANGS.has((activeArtifact.language ?? "").toLowerCase()));

  const handleRun = useCallback(async () => {
    if (!activeArtifact || runRunning) return;
    const codeToRun = editableCode ?? activeArtifact.content;
    setRunRunning(true);
    try {
      const res = await fetch("/api/code/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: codeToRun, language: activeArtifact.language || "python" }),
      });
      const data = await res.json() as RunOutput;
      setRunOutput(data);
    } catch (e) {
      setRunOutput({ exec_ok: false, stdout: "", stderr: String(e), exit_code: -1, duration_ms: 0, images: [] });
    } finally {
      setRunRunning(false);
    }
  }, [activeArtifact, editableCode, runRunning]);

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
    { mode: "preview", label: "Vista previa", icon: <Eye size={13} />, disabled: !renderable },
    { mode: "source", label: "Fuente", icon: <Code2 size={13} /> },
    { mode: "compare", label: "Comparar", icon: <History size={13} />, disabled: !previousArtifact },
    { mode: "inspect", label: "Inspeccionar", icon: <Info size={13} /> },
  ];

  return (
    <aside className={`artifact-studio${activeArtifact?.kind === "image" && viewMode === "preview" ? " image-focus" : ""}`} aria-label="Artifacts generados">
      {/* Compact single-row header. Title + kind + line count + actions. Eliminates
          the previous 3-row chrome (kicker / wordmark / subtitle / kind HUD / toolbar)
          that ate ~120 px of vertical space before the actual artifact rendered. */}
      <div className="artifact-studio-header artifact-studio-header-compact">
        <div className="artifact-studio-summary">
          {activeArtifact && <span className="artifact-studio-icon" aria-hidden="true">{artifactKindIcon(activeArtifact.kind)}</span>}
          <strong title={activeArtifact?.title}>{activeArtifact?.title ?? "Artifact Studio"}</strong>
          {activeArtifact && <span className="artifact-studio-kind-pill">{activeArtifact.kind}</span>}
          {activeArtifact && <small>{artifactLineCount(activeArtifact)} L · {activeArtifact.tokenEstimate}t{activeArtifact.streaming ? " · streaming" : ""}</small>}
        </div>
        <div className="artifact-toolbar-actions">
          <button onClick={copyArtifact} disabled={!activeArtifact} title="Copiar artifact"><Copy size={13} /></button>
          <button onClick={downloadArtifact} disabled={!activeArtifact} title="Descargar artifact"><Download size={13} /></button>
          <button onClick={() => activeArtifact && onAttachToPrompt(activeArtifact)} disabled={!activeArtifact} title="Adjuntar al siguiente prompt"><Paperclip size={13} /></button>
          {activeArtifact && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react" || activeArtifact.kind === "code") && (
            <button onClick={() => setShowConsole((s) => !s)} className={showConsole ? "active" : ""} title={`Console${consoleEntries.length > 0 ? ` (${consoleEntries.length})` : ""}`}>
              <Code2 size={13} />{consoleEntries.length > 0 && <span className="console-count">{consoleEntries.length}</span>}
            </button>
          )}
          {isRunnable && (
            <button onClick={() => { setIsEditMode((m) => { if (!m) setEditableCode(activeArtifact?.content ?? ""); return !m; }); }} className={isEditMode ? "active" : ""} title={isEditMode ? "Switch to preview" : "Edit code"}>
              <RefreshCw size={13} />
            </button>
          )}
          {isRunnable && (
            <button onClick={() => void handleRun()} disabled={runRunning} className="artifact-run-btn" title={`Run ${activeArtifact?.language ?? "code"}`}>
              <Zap size={13} />{runRunning ? "…" : ""}
            </button>
          )}
          {onClearAll && artifacts.length > 0 && (
            <button className="danger" onClick={() => { if (window.confirm("¿Limpiar todos los artifacts guardados?")) onClearAll(); }} title="Limpiar todos los artifacts">
              <Trash2 size={13} />
            </button>
          )}
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
            {filteredArtifacts.map((artifact) => {
              const isLive = artifact.id === "plan-live-preview";
              return (
                <button key={artifact.id} className={`${artifact.id === activeArtifact?.id ? "active" : ""}${isLive ? " artifact-tab-live" : ""}`} onClick={() => onSelect(artifact.id)} title={artifact.title}>
                  {isLive && <span className="artifact-live-badge">LIVE</span>}
                  <span>{artifact.version ? `v${artifact.version} · ${artifact.kind}` : artifact.kind}</span>
                  <strong>{artifact.title}</strong>
                  <small>{isLive ? "generating…" : artifact.favorite ? "Pinned" : `#${shortArtifactHash(artifact)}`}</small>
                </button>
              );
            })}
            {filteredArtifacts.length === 0 && <div className="artifact-library-empty">No artifacts match this view.</div>}
          </div>
          {versionSiblings.length > 1 && <div className="artifact-version-strip" aria-label="Versiones del artifact activo">
            {versionSiblings.map((artifact) => <button key={artifact.id} className={artifact.id === activeArtifact?.id ? "active" : ""} onClick={() => onSelect(artifact.id)} title={`${artifact.title} v${artifact.version ?? 1}`}>v{artifact.version ?? 1}</button>)}
          </div>}
        </div>
        {activeArtifact && <div className="artifact-canvas">
          {/* Compact canvas bar — only the view switch + small meta pills + fullscreen.
              Title/kind/icon were moved up to the header; version/hash/time now live
              inline as small pills so the bar fits in one row instead of two. */}
          <div className="artifact-canvas-bar artifact-canvas-bar-compact">
            <div className="artifact-view-switch" aria-label="Modo de artifact">
              {viewOptions.map((option) => <button key={option.mode} className={viewMode === option.mode ? "active" : ""} onClick={() => setViewMode(option.mode)} disabled={option.disabled} title={option.label}>{option.icon}<span>{option.label}</span></button>)}
            </div>
            <div className="artifact-canvas-meta">
              <span title="Versión">{activeArtifact.version ? `v${activeArtifact.version}` : "draft"}</span>
              <span title="Hash" className="canvas-meta-hash">#{shortArtifactHash(activeArtifact)}</span>
              <span title="Actualizado">{formatArtifactTime(activeArtifact.updatedAt ?? activeArtifact.createdAt)}</span>
            </div>
            <div className="artifact-library-actions" aria-label="Acciones de biblioteca">
              <button onClick={() => onToggleFavorite(activeArtifact.id)} title={activeArtifact.favorite ? "Quitar pin" : "Fijar artifact"}><Pin size={12} /></button>
              <button className="danger" onClick={() => onRemoveArtifact(activeArtifact.id)} title="Eliminar artifact del registry"><Trash2 size={12} /></button>
              <FullscreenButton stageRef={stageRef} />
            </div>
          </div>
          <div ref={stageRef} className={`artifact-stage ${viewMode}`}>
            {viewMode === "preview" && activeArtifact.kind === "browser" ? (
              <BrowserLiveView content={activeArtifact.content} />
            ) : viewMode === "preview" && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react") ? (
              <iframe
                key={`artifact-iframe-${activeArtifact.id}`}
                title={activeArtifact.title}
                sandbox="allow-scripts"
                srcDoc={activeArtifact.kind === "html" ? debouncedSrcDoc : artifactSrcDoc(activeArtifact)}
              />
            ) : viewMode === "preview" && activeArtifact.kind === "code" && isEditMode ? (
              <textarea
                className="artifact-code-editor"
                value={editableCode ?? activeArtifact.content}
                onChange={(e) => setEditableCode(e.target.value)}
                spellCheck={false}
                aria-label="Edit code artifact"
              />
            ) : viewMode === "preview" && activeArtifact.kind === "code" ? (
              <iframe title={activeArtifact.title} sandbox="allow-scripts" srcDoc={codeHighlightSrcDoc(activeArtifact)} />
            ) : viewMode === "preview" && (activeArtifact.kind === "image" || activeArtifact.kind === "video" || activeArtifact.kind === "audio") ? (
              <MediaPreview artifact={activeArtifact} />
            ) : viewMode === "preview" && activeArtifact.kind === "markdown" ? (
              <div className="artifact-markdown">{renderMarkdown(activeArtifact.content)}</div>
            ) : viewMode === "preview" && activeArtifact.kind === "mermaid" ? (
              <MermaidPreview content={activeArtifact.content} />
            ) : viewMode === "preview" && activeArtifact.kind === "image_request" ? (
              <ImageRequestPreview artifact={activeArtifact} />
            ) : viewMode === "preview" && activeArtifact.kind === "json" ? (
              <JsonExplorer content={activeArtifact.content} />
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
          {runOutput && <RunOutputPanel output={runOutput} onClear={() => setRunOutput(null)} />}
          {showConsole && (activeArtifact.kind === "html" || activeArtifact.kind === "svg" || activeArtifact.kind === "react" || activeArtifact.kind === "code") && (
            <ConsolePanel entries={consoleEntries} onClear={() => setConsoleEntries([])} onClose={() => setShowConsole(false)} />
          )}
        </div>}
      </> : <ArtifactStandbyCanvas />}
    </aside>
  );
}
