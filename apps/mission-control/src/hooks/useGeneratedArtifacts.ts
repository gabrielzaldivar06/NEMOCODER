import { useEffect, useMemo, useRef, useState } from "react";
import { collectGeneratedArtifacts, type GeneratedArtifact } from "../services/artifactUtils";
import { loadArtifactRegistry, mergeArtifactsIntoRegistry, removeArtifactFromRegistry, toggleArtifactFavorite, type PersistedGeneratedArtifact } from "../services/artifactRegistry";
import { isArtifactTombstoned, addArtifactTombstone } from "../services/persistenceStore";

type ArtifactMessage = {
  id: string;
  role: string;
  content: string;
};

type UseGeneratedArtifactsOptions = {
  messages: ArtifactMessage[];
  draft: string;
  onDraftChange: (objective: string) => void;
  repoPath?: string;
};

const MAX_ATTACHED_ARTIFACT_CHARS = 24000;

function artifactVersionLabel(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  return "version" in artifact && artifact.version ? `v${artifact.version}` : "draft";
}

function artifactReferenceId(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  return "registryId" in artifact && artifact.registryId ? artifact.registryId : artifact.id;
}

function artifactReferenceHash(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  return "contentHash" in artifact && artifact.contentHash ? artifact.contentHash : "unindexed";
}

function artifactUpdatedAt(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  return "updatedAt" in artifact && artifact.updatedAt ? artifact.updatedAt : "session";
}

function artifactFenceLanguage(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  return artifact.language || artifact.kind || "text";
}

export function buildArtifactPromptAttachment(artifact: GeneratedArtifact | PersistedGeneratedArtifact): string {
  const content = artifact.content.length > MAX_ATTACHED_ARTIFACT_CHARS
    ? `${artifact.content.slice(0, MAX_ATTACHED_ARTIFACT_CHARS)}\n\n[artifact truncated at ${MAX_ATTACHED_ARTIFACT_CHARS.toLocaleString()} chars; ask to narrow the edit if needed]`
    : artifact.content;

  return [
    `Itera sobre este artifact de Space Code usando el contenido actual como fuente exacta.`,
    `Artifact: ${artifact.title}`,
    `Tipo: ${artifact.kind}/${artifact.language}`,
    `Version: ${artifactVersionLabel(artifact)}`,
    `Stable ID: ${artifactReferenceId(artifact)}`,
    `Hash: ${artifactReferenceHash(artifact)}`,
    `Tokens estimados: ${artifact.tokenEstimate}`,
    `Actualizado: ${artifactUpdatedAt(artifact)}`,
    "",
    "Contenido actual del artifact:",
    `\`\`\`\`${artifactFenceLanguage(artifact)}`,
    content,
    "````",
  ].join("\n");
}

export function useGeneratedArtifacts({ messages, draft, onDraftChange, repoPath }: UseGeneratedArtifactsOptions) {
  const generatedArtifacts = useMemo(() => collectGeneratedArtifacts(messages), [messages]);
  const [artifacts, setArtifacts] = useState<PersistedGeneratedArtifact[]>(() => loadArtifactRegistry(repoPath));
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);

  // Keep a ref to the latest repoPath so the merge effect always writes to the correct workspace
  // without needing repoPath in its deps (which would cause a race where old artifacts get merged
  // under the new workspace key before messages have updated).
  const repoPathRef = useRef(repoPath ?? "");
  useEffect(() => { repoPathRef.current = repoPath ?? ""; });

  // Reset registry when workspace changes.
  useEffect(() => {
    setArtifacts(loadArtifactRegistry(repoPath));
    setActiveArtifactId(null);
  }, [repoPath]);

  // Only runs when generatedArtifacts (i.e. messages) change — NOT when repoPath changes.
  // repoPath is read from the ref, which is always current by the time this effect fires.
  useEffect(() => {
    setArtifacts((current) => mergeArtifactsIntoRegistry(generatedArtifacts, current, {
      isTombstoned: isArtifactTombstoned,
    }, repoPathRef.current));
  }, [generatedArtifacts]);

  useEffect(() => {
    if (artifacts.length === 0) {
      setActiveArtifactId(null);
      return;
    }
    // While a new artifact is still streaming, focus on it so the user sees the
    // Claude-Code-style live preview render. Otherwise keep the user's current
    // selection if still present; fall back to the most recent entry.
    const streamingArtifact = artifacts.find((artifact) => artifact.id.startsWith("streaming-"));
    if (streamingArtifact) {
      setActiveArtifactId(streamingArtifact.id);
      return;
    }
    setActiveArtifactId((current) => current && artifacts.some((artifact) => artifact.id === current) ? current : artifacts[0].id);
  }, [artifacts]);

  const attachArtifactToDraft = (artifact: GeneratedArtifact | PersistedGeneratedArtifact) => {
    const reference = buildArtifactPromptAttachment(artifact);
    onDraftChange(draft.trim() ? `${draft.trim()}\n\n${reference}` : reference);
  };

  const removeArtifact = (artifactId: string) => {
    addArtifactTombstone(artifactId);
    setArtifacts((current) => removeArtifactFromRegistry(artifactId, current, repoPath));
  };

  const toggleFavorite = (artifactId: string) => {
    setArtifacts((current) => toggleArtifactFavorite(artifactId, current, repoPath));
  };

  const clearArtifacts = () => {
    setArtifacts([]);
    setActiveArtifactId(null);
  };

  return {
    artifacts,
    activeArtifactId,
    setActiveArtifactId,
    attachArtifactToDraft,
    removeArtifact,
    toggleFavorite,
    clearArtifacts,
  };
}
