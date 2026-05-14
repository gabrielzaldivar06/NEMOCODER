import type { ReactNode } from "react";
import { stripGeneratedArtifactBlocks } from "../services/artifactUtils";

export type MissionTimelineToolCall = {
  id?: string;
  name: string;
  tool_name?: string;
  alias_name?: string;
  status: string;
  summary?: string;
};

export type MissionTimelineAction = {
  id: string;
  kind: string;
  label: string;
  summary: string;
  payload?: Record<string, unknown>;
};

export type MissionTimelineMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: MissionTimelineToolCall[];
  actions?: MissionTimelineAction[];
};

type MissionTimelineProps = {
  messages: MissionTimelineMessage[];
  running: boolean;
  queuedPrompt: string | null;
  cleanAssistantContent: (content: string) => string;
  renderRichText: (content: string) => ReactNode;
  renderMcpEvidence: (tools: MissionTimelineToolCall[]) => ReactNode;
  liveStatus: ReactNode;
  commandDock: ReactNode;
  onRunAction?: (action: MissionTimelineAction) => void;
};

function countArtifactBlocks(content: string): number {
  const artifactFenceMatches = content.match(/```(?:html|html_artifact|svg|react_artifact|jsx|tsx|mermaid|image|video|audio|json|markdown|md)\b/gi);
  return artifactFenceMatches?.length ?? 0;
}

function eventIntent(message: MissionTimelineMessage, renderedContent: string): { label: string; tone: string } {
  const content = renderedContent.toLowerCase();
  if (message.role === "user") return { label: "Directive", tone: "operator" };
  if ((message.tool_calls ?? []).length > 0 || content.includes("nemo") || content.includes("evidence")) return { label: "Evidence", tone: "evidence" };
  if (content.includes("plan") || content.includes("checklist") || content.includes("sprint")) return { label: "Plan trace", tone: "plan" };
  if (countArtifactBlocks(message.content) > 0) return { label: "Artifact", tone: "artifact" };
  return { label: "System", tone: "system" };
}

function wordCount(content: string): number {
  return content.trim().split(/\s+/).filter(Boolean).length;
}

export function MissionTimeline({ messages, running, queuedPrompt, cleanAssistantContent, renderRichText, renderMcpEvidence, liveStatus, commandDock, onRunAction }: MissionTimelineProps) {
  const visibleMessages = messages.slice(-8);
  const activeEventId = visibleMessages.at(-1)?.id ?? null;

  return (
    <section className="mission-timeline" aria-label="Mission timeline">
      <div className="mission-timeline-tabs">
        <span className="active">Conversation</span>
        <span>{running ? "Live" : "Ready"}</span>
        <span>{visibleMessages.length} events</span>
      </div>
      <div className="mission-events">
        {visibleMessages.length === 0 ? <span className="empty-inline">Sin eventos de misión todavía.</span> : visibleMessages.map((message, index) => {
          const parsedContent = message.role === "assistant" ? cleanAssistantContent(message.content) : message.content;
          const renderedContent = message.role === "assistant" ? stripGeneratedArtifactBlocks(parsedContent) : parsedContent;
          const isActiveEvent = message.id === activeEventId;
          const eventState = isActiveEvent && running ? "active" : isActiveEvent ? "latest" : "settled";
          const eventLabel = `${message.role === "assistant" ? "Space Code" : "User"} event ${index + 1} of ${visibleMessages.length}`;
          const intent = eventIntent(message, renderedContent);
          const artifactCount = countArtifactBlocks(message.content);
          const toolCount = message.tool_calls?.length ?? 0;
          return <article className={`mission-event ${message.role}`} data-state={eventState} data-intent={intent.tone} aria-label={eventLabel} key={message.id}>
            <div className="mission-event-node" aria-hidden="true"><span /><em>{index + 1}</em></div>
            <div className="mission-event-card">
              <header>
                <div><strong>{message.role === "assistant" ? "Space Code" : "USER"}</strong><b>{intent.label}</b></div>
                <small>{eventState === "active" ? "active" : message.role === "assistant" ? "system" : "operator"}</small>
              </header>
              <div className="mission-event-signals" aria-label={`${intent.label} event signals`}>
                <span>{wordCount(renderedContent)} words</span>
                {toolCount > 0 && <span>{toolCount} tool calls</span>}
                {artifactCount > 0 && <span>{artifactCount} artifact block{artifactCount === 1 ? "" : "s"}</span>}
                {eventState === "active" && <i className="mission-event-wave" aria-label="Streaming signal"><em /><em /><em /><em /><em /></i>}
              </div>
              {renderRichText(renderedContent)}
              {message.role === "assistant" && renderMcpEvidence(message.tool_calls ?? [])}
              {message.role === "assistant" && onRunAction && (message.actions ?? []).length > 0 && (
                <div className="mission-event-actions">
                  {(message.actions ?? []).map((action) => (
                    <button
                      key={action.id}
                      className={`mission-action-btn ${action.kind}`}
                      title={action.summary}
                      onClick={() => onRunAction(action)}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </article>;
        })}
        {(running || queuedPrompt) && <div className="home-inline-status">{liveStatus}</div>}
      </div>
      {commandDock}
    </section>
  );
}
