import type { ReactNode } from "react";
import { stripGeneratedArtifactBlocks } from "./ArtifactWorkbench";

export type MissionTimelineToolCall = {
  id?: string;
  name: string;
  tool_name?: string;
  alias_name?: string;
  status: string;
  summary?: string;
};

export type MissionTimelineMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: MissionTimelineToolCall[];
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
};

export function MissionTimeline({ messages, running, queuedPrompt, cleanAssistantContent, renderRichText, renderMcpEvidence, liveStatus, commandDock }: MissionTimelineProps) {
  const visibleMessages = messages.slice(-8);

  return (
    <section className="mission-timeline" aria-label="Mission timeline">
      <div className="mission-timeline-tabs">
        <span className="active">Conversation</span>
        <span>{running ? "Live" : "Ready"}</span>
        <span>{visibleMessages.length} events</span>
      </div>
      <div className="mission-events">
        {visibleMessages.length === 0 ? <span className="empty-inline">Sin eventos de misión todavía.</span> : visibleMessages.map((message) => {
          const parsedContent = message.role === "assistant" ? cleanAssistantContent(message.content) : message.content;
          const renderedContent = message.role === "assistant" ? stripGeneratedArtifactBlocks(parsedContent) : parsedContent;
          return <article className={`mission-event ${message.role}`} key={message.id}>
            <div className="mission-event-node"><span /></div>
            <div className="mission-event-card">
              <header><strong>{message.role === "assistant" ? "Space Code" : "USER"}</strong><small>{message.role === "assistant" ? "system" : "operator"}</small></header>
              {renderRichText(renderedContent)}
              {message.role === "assistant" && renderMcpEvidence(message.tool_calls ?? [])}
            </div>
          </article>;
        })}
        {(running || queuedPrompt) && <div className="home-inline-status">{liveStatus}</div>}
      </div>
      {commandDock}
    </section>
  );
}
