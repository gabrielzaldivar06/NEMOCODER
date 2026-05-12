import type { GeneratedArtifact } from "./artifactUtils";

const ARTIFACT_REGISTRY_STORAGE_KEY = "mission-control-artifact-registry-v1";
const MAX_STORED_ARTIFACTS = 24;

export type PersistedGeneratedArtifact = GeneratedArtifact & {
  registryId: string;
  contentHash: string;
  version: number;
  versionGroup: string;
  createdAt: string;
  updatedAt: string;
  favorite?: boolean;
  persisted: true;
};

function hashText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function artifactVersionGroup(artifact: GeneratedArtifact): string {
  return hashText(`${artifact.kind}\n${artifact.language}\n${artifact.title.toLowerCase()}`);
}

function artifactRegistryId(artifact: GeneratedArtifact, versionGroup: string, contentHash: string): string {
  return `artifact-${versionGroup}-${contentHash}`;
}

export function loadArtifactRegistry(): PersistedGeneratedArtifact[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(ARTIFACT_REGISTRY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PersistedGeneratedArtifact[];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is PersistedGeneratedArtifact => Boolean(
      item &&
      typeof item === "object" &&
      typeof item.registryId === "string" &&
      typeof item.contentHash === "string" &&
      typeof item.versionGroup === "string" &&
      typeof item.content === "string" &&
      typeof item.title === "string"
    )).slice(0, MAX_STORED_ARTIFACTS);
  } catch {
    return [];
  }
}

export function saveArtifactRegistry(artifacts: PersistedGeneratedArtifact[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ARTIFACT_REGISTRY_STORAGE_KEY, JSON.stringify(artifacts.slice(0, MAX_STORED_ARTIFACTS)));
  } catch {
    // Keep the current session usable if storage quota is unavailable.
  }
}

export function mergeArtifactsIntoRegistry(generatedArtifacts: GeneratedArtifact[], currentRegistry = loadArtifactRegistry()): PersistedGeneratedArtifact[] {
  const registryById = new Map(currentRegistry.map((artifact) => [artifact.registryId, artifact]));
  const now = new Date().toISOString();

  for (const artifact of generatedArtifacts.slice().reverse()) {
    const contentHash = hashText(`${artifact.kind}\n${artifact.language}\n${artifact.content}`);
    const versionGroup = artifactVersionGroup(artifact);
    const registryId = artifactRegistryId(artifact, versionGroup, contentHash);
    const existing = registryById.get(registryId);

    if (existing) {
      registryById.set(registryId, {
        ...existing,
        ...artifact,
        id: registryId,
        registryId,
        contentHash,
        versionGroup,
        updatedAt: now,
        persisted: true,
      });
      continue;
    }

    const existingVersions = Array.from(registryById.values()).filter((item) => item.versionGroup === versionGroup);
    const version = existingVersions.reduce((maximum, item) => Math.max(maximum, item.version), 0) + 1;
    registryById.set(registryId, {
      ...artifact,
      id: registryId,
      registryId,
      contentHash,
      versionGroup,
      version,
      createdAt: now,
      updatedAt: now,
      persisted: true,
    });
  }

  const nextRegistry = Array.from(registryById.values())
    .sort((left, right) => Number(Boolean(right.favorite)) - Number(Boolean(left.favorite)) || right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, MAX_STORED_ARTIFACTS);
  saveArtifactRegistry(nextRegistry);
  return nextRegistry;
}

export function removeArtifactFromRegistry(registryId: string, currentRegistry = loadArtifactRegistry()): PersistedGeneratedArtifact[] {
  const nextRegistry = currentRegistry.filter((artifact) => artifact.registryId !== registryId);
  saveArtifactRegistry(nextRegistry);
  return nextRegistry;
}

export function toggleArtifactFavorite(registryId: string, currentRegistry = loadArtifactRegistry()): PersistedGeneratedArtifact[] {
  const now = new Date().toISOString();
  const nextRegistry = currentRegistry
    .map((artifact) => artifact.registryId === registryId ? { ...artifact, favorite: !artifact.favorite, updatedAt: now } : artifact)
    .sort((left, right) => Number(Boolean(right.favorite)) - Number(Boolean(left.favorite)) || right.updatedAt.localeCompare(left.updatedAt));
  saveArtifactRegistry(nextRegistry);
  return nextRegistry;
}
