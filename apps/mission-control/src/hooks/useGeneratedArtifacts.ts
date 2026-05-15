import { useEffect, useMemo, useState } from "react";
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

export function useGeneratedArtifacts({ messages, draft, onDraftChange }: UseGeneratedArtifactsOptions) {
  const generatedArtifacts = useMemo(() => collectGeneratedArtifacts(messages), [messages]);
  const [artifacts, setArtifacts] = useState<PersistedGeneratedArtifact[]>(() => loadArtifactRegistry());
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);

  useEffect(() => {
    setArtifacts(mergeArtifactsIntoRegistry(generatedArtifacts, undefined, {
      isTombstoned: isArtifactTombstoned,
    }));
  }, [generatedArtifacts]);

  useEffect(() => {
    if (artifacts.length === 0) {
      setActiveArtifactId(null);
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
    setArtifacts((current) => removeArtifactFromRegistry(artifactId, current));
  };

  const toggleFavorite = (artifactId: string) => {
    setArtifacts((current) => toggleArtifactFavorite(artifactId, current));
  };

  return {
    artifacts,
    activeArtifactId,
    setActiveArtifactId,
    attachArtifactToDraft,
    removeArtifact,
    toggleFavorite,
  };
}
