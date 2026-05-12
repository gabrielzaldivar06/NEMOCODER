export type GeneratedArtifactKind = "html" | "svg" | "markdown" | "json" | "mermaid" | "react" | "image_request" | "code";

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

export type ArtifactMessageSource = {
  id: string;
  role: string;
  content: string;
};

export type ArtifactDiffRow = {
  kind: "same" | "added" | "removed" | "changed";
  leftLine?: number;
  rightLine?: number;
  left?: string;
  right?: string;
};

export type ArtifactDiffResult = {
  rows: ArtifactDiffRow[];
  added: number;
  removed: number;
  changed: number;
  omitted: number;
};

const ARTIFACT_TITLE_REGEX = /<!--\s*ARTIFACT:([^:]+):([a-zA-Z0-9_-]+)\s*-->/;

function estimateArtifactTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

export function artifactKindFromBlock(language: string, content: string): GeneratedArtifactKind {
  const normalized = language.toLowerCase().trim();
  const trimmed = content.trimStart().toLowerCase();
  if (["html", "htm", "html_artifact"].includes(normalized) || trimmed.startsWith("<!doctype html") || trimmed.startsWith("<html")) return "html";
  if (["svg", "svg_artifact"].includes(normalized) || trimmed.startsWith("<svg")) return "svg";
  if (["md", "markdown", "mdx"].includes(normalized)) return "markdown";
  if (["json", "jsonc"].includes(normalized)) return "json";
  if (normalized === "mermaid") return "mermaid";
  if (["react_artifact", "jsx", "tsx"].includes(normalized)) return "react";
  if (normalized === "image_request") return "image_request";
  return "code";
}

function artifactTitle(kind: GeneratedArtifactKind, language: string, content: string, index: number): string {
  const titleMatch = content.match(ARTIFACT_TITLE_REGEX);
  if (titleMatch?.[1]) return titleMatch[1].trim();
  const label = kind === "html" ? "Interactive HTML" : kind === "svg" ? "SVG Scene" : kind === "markdown" ? "Document" : kind === "json" ? "Data" : kind === "mermaid" ? "Diagram" : kind === "react" ? "React Component" : kind === "image_request" ? "Image Request" : "Code";
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
        title: artifactTitle(kind, language, content, localIndex),
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
    return ["html", "svg", "markdown", "json", "mermaid", "react", "image_request", "code"].includes(kind) ? "" : block;
  }).trim();
  return stripped || "Artifact generado.";
}

export function buildArtifactLineDiff(previous: GeneratedArtifact, current: GeneratedArtifact, maxRows = 180): ArtifactDiffResult {
  const allPreviousLines = previous.content.split(/\r?\n/);
  const allCurrentLines = current.content.split(/\r?\n/);
  const previousLines = allPreviousLines.slice(0, maxRows);
  const currentLines = allCurrentLines.slice(0, maxRows);
  const lcsTable = Array.from({ length: previousLines.length + 1 }, () => Array(currentLines.length + 1).fill(0) as number[]);

  for (let previousIndex = previousLines.length - 1; previousIndex >= 0; previousIndex -= 1) {
    for (let currentIndex = currentLines.length - 1; currentIndex >= 0; currentIndex -= 1) {
      lcsTable[previousIndex][currentIndex] = previousLines[previousIndex] === currentLines[currentIndex]
        ? lcsTable[previousIndex + 1][currentIndex + 1] + 1
        : Math.max(lcsTable[previousIndex + 1][currentIndex], lcsTable[previousIndex][currentIndex + 1]);
    }
  }

  const rawRows: ArtifactDiffRow[] = [];
  let previousIndex = 0;
  let currentIndex = 0;

  while (previousIndex < previousLines.length && currentIndex < currentLines.length) {
    if (previousLines[previousIndex] === currentLines[currentIndex]) {
      rawRows.push({ kind: "same", leftLine: previousIndex + 1, rightLine: currentIndex + 1, left: previousLines[previousIndex], right: currentLines[currentIndex] });
      previousIndex += 1;
      currentIndex += 1;
    } else if (lcsTable[previousIndex + 1][currentIndex] >= lcsTable[previousIndex][currentIndex + 1]) {
      rawRows.push({ kind: "removed", leftLine: previousIndex + 1, left: previousLines[previousIndex] });
      previousIndex += 1;
    } else {
      rawRows.push({ kind: "added", rightLine: currentIndex + 1, right: currentLines[currentIndex] });
      currentIndex += 1;
    }
  }

  while (previousIndex < previousLines.length) {
    rawRows.push({ kind: "removed", leftLine: previousIndex + 1, left: previousLines[previousIndex] });
    previousIndex += 1;
  }
  while (currentIndex < currentLines.length) {
    rawRows.push({ kind: "added", rightLine: currentIndex + 1, right: currentLines[currentIndex] });
    currentIndex += 1;
  }

  const rows: ArtifactDiffRow[] = [];
  let added = 0;
  let removed = 0;
  let changed = 0;

  for (let rowIndex = 0; rowIndex < rawRows.length; rowIndex += 1) {
    const row = rawRows[rowIndex];
    const nextRow = rawRows[rowIndex + 1];
    if (row.kind === "removed" && nextRow?.kind === "added") {
      rows.push({ kind: "changed", leftLine: row.leftLine, rightLine: nextRow.rightLine, left: row.left, right: nextRow.right });
      changed += 1;
      rowIndex += 1;
    } else {
      rows.push(row);
      if (row.kind === "added") added += 1;
      if (row.kind === "removed") removed += 1;
    }
  }

  return {
    rows,
    added,
    removed,
    changed,
    omitted: Math.max(0, Math.max(allPreviousLines.length, allCurrentLines.length) - Math.max(previousLines.length, currentLines.length)),
  };
}
