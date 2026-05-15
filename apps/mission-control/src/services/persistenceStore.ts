const CHAT_SESSION_KEY = "mission-control-chat-session-v1";
const CHAT_ARCHIVE_KEY = "mission-control-chat-archives-v1";
export const ARTIFACT_TOMBSTONES_KEY = "mission-control-artifact-tombstones-v1";

// ── Chat session ──────────────────────────────────────────────

export function clearChatSession(): void {
  try { window.localStorage.removeItem(CHAT_SESSION_KEY); } catch {}
}

export function archiveChatSession(
  messages: Array<{ id: string; role: string; content: string }>,
  title?: string
): void {
  try {
    const raw = window.localStorage.getItem(CHAT_ARCHIVE_KEY);
    const existing = raw ? (JSON.parse(raw) as unknown[]) : [];
    const entry = {
      id: `chat-${Date.now()}`,
      created_at: new Date().toISOString(),
      title: title ?? "Chat archivado",
      messages,
    };
    const nextArchives = [entry, ...(Array.isArray(existing) ? existing : [])].slice(0, 20);
    window.localStorage.setItem(CHAT_ARCHIVE_KEY, JSON.stringify(nextArchives));
    clearChatSession();
  } catch {}
}

// ── Artifact tombstones ───────────────────────────────────────

export function loadArtifactTombstones(): Set<string> {
  try {
    const raw = window.localStorage.getItem(ARTIFACT_TOMBSTONES_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed as string[]) : new Set();
  } catch {
    return new Set();
  }
}

export function addArtifactTombstone(registryId: string): void {
  try {
    const tombstones = loadArtifactTombstones();
    tombstones.add(registryId);
    window.localStorage.setItem(ARTIFACT_TOMBSTONES_KEY, JSON.stringify([...tombstones]));
  } catch {}
}

export function isArtifactTombstoned(registryId: string): boolean {
  return loadArtifactTombstones().has(registryId);
}

export function clearArtifactTombstones(): void {
  try { window.localStorage.removeItem(ARTIFACT_TOMBSTONES_KEY); } catch {}
}
