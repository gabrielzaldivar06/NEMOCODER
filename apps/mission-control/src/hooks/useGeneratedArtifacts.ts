import { useEffect, useMemo, useState } from "react";
import { collectGeneratedArtifacts, type GeneratedArtifact } from "../components/ArtifactWorkbench";
import { loadArtifactRegistry, mergeArtifactsIntoRegistry, type PersistedGeneratedArtifact } from "../services/artifactRegistry";

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

export function useGeneratedArtifacts({ messages, draft, onDraftChange }: UseGeneratedArtifactsOptions) {
  const generatedArtifacts = useMemo(() => collectGeneratedArtifacts(messages), [messages]);
  const [artifacts, setArtifacts] = useState<PersistedGeneratedArtifact[]>(() => loadArtifactRegistry());
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);

  useEffect(() => {
    setArtifacts(mergeArtifactsIntoRegistry(generatedArtifacts));
  }, [generatedArtifacts]);

  useEffect(() => {
    if (artifacts.length === 0) {
      setActiveArtifactId(null);
      return;
    }
    setActiveArtifactId((current) => current && artifacts.some((artifact) => artifact.id === current) ? current : artifacts[0].id);
  }, [artifacts]);

  const attachArtifactToDraft = (artifact: GeneratedArtifact | PersistedGeneratedArtifact) => {
    const versionLabel = "version" in artifact ? ` v${artifact.version}` : "";
    const reference = `Itera sobre el artifact "${artifact.title}"${versionLabel} (${artifact.kind}/${artifact.language}, ${artifact.tokenEstimate} tokens).`;
    onDraftChange(draft.trim() ? `${draft.trim()}\n\n${reference}` : reference);
  };

  return {
    artifacts,
    activeArtifactId,
    setActiveArtifactId,
    attachArtifactToDraft,
  };
}
