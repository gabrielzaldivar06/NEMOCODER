import "./NemoMemoryOrbitCopy.css";
import type React from "react";

export type NemoMemoryOrbitNode = {
  id: string;
  mtype: string;
  importance: number;
  color: string;
  title: string;
  summary?: string;
  excerpt?: string;
  tags?: string[];
  created?: string;
  content?: string;
};

export type NemoMemoryOrbitEdge = {
  source: string;
  target: string;
  similarity: number;
  color?: string;
  width?: number;
  particles?: number;
};

type NemoMemoryOrbitCopyProps = {
  nodes: NemoMemoryOrbitNode[];
  edges: NemoMemoryOrbitEdge[];
  status?: string;
  onSelectNode?: (node: NemoMemoryOrbitNode) => void;
};

const TYPE_BADGES: Record<string, string> = {
  fact: "FA",
  insight: "IN",
  preference: "PR",
  procedure: "PR",
  correction: "CO",
  episodic: "EP",
  intent_anchor: "IA",
  development_checkpoint: "DC",
  project_decision: "PD",
  configuration: "CF",
};

function badgeFor(type: string): string {
  return TYPE_BADGES[type] || type.slice(0, 2).toUpperCase() || "ME";
}

function boundedNodes(nodes: NemoMemoryOrbitNode[]): NemoMemoryOrbitNode[] {
  return [...nodes].sort((left, right) => right.importance - left.importance).slice(0, 18);
}

function typeCounts(nodes: NemoMemoryOrbitNode[]): Array<[string, number, string]> {
  const counts = new Map<string, { count: number; color: string }>();
  nodes.forEach((node) => {
    const previous = counts.get(node.mtype);
    counts.set(node.mtype, { count: (previous?.count || 0) + 1, color: previous?.color || node.color });
  });
  return [...counts]
    .map(([type, value]): [string, number, string] => [type, value.count, value.color])
    .sort((left, right) => right[1] - left[1]);
}

export function NemoMemoryOrbitCopy({ nodes, edges, status = "snapshot", onSelectNode }: NemoMemoryOrbitCopyProps) {
  const visibleNodes = boundedNodes(nodes);
  const nodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)).slice(0, 24);
  const counts = typeCounts(visibleNodes);

  return (
    <section className="nemo-orbit-copy" aria-label="NEMO memory orbit copy">
      <header className="nemo-orbit-copy__hud">
        <span className="nemo-orbit-copy__live-dot" aria-hidden="true" />
        <span className="nemo-orbit-copy__title">NEMO orbit copy</span>
        <span className="nemo-orbit-copy__stats" aria-label={`${visibleNodes.length} memories and ${visibleEdges.length} links`}>
          <span>{status}</span>
          <span>{visibleNodes.length} memories</span>
          <span>{visibleEdges.length} links</span>
        </span>
      </header>

      <div className="nemo-orbit-copy__stage">
        <div className="nemo-orbit-copy__core" aria-hidden="true" />
        {visibleEdges.map((edge, index) => {
          const angle = (index * 37 + Math.round(edge.similarity * 100)) % 180;
          const length = 58 + Math.min(72, Math.round(edge.similarity * 76));
          return (
            <span
              aria-hidden="true"
              className="nemo-orbit-copy__edge"
              key={`${edge.source}-${edge.target}-${index}`}
              style={{
                "--edge-angle": `${angle}deg`,
                "--edge-length": `${length}px`,
                "--edge-offset": `${(index % 7) * 9 - 27}px`,
                "--edge-alpha": `${Math.max(0.16, Math.min(0.72, edge.similarity - 0.32))}`,
              } as React.CSSProperties}
            />
          );
        })}
        {visibleNodes.map((node, index) => {
          const angle = (index / Math.max(visibleNodes.length, 1)) * 360;
          const radius = 54 + (index % 5) * 14;
          const nodeSize = 22 + Math.min(18, node.importance * 1.8);
          return (
            <button
              aria-label={`${node.mtype} memory, importance ${node.importance}: ${node.title}`}
              className="nemo-orbit-copy__node"
              key={node.id}
              onClick={() => onSelectNode?.(node)}
              title={node.summary || node.excerpt || node.title}
              type="button"
              style={{
                "--angle": `${angle}deg`,
                "--breath-speed": `${4.8 + (index % 4) * 0.6}s`,
                "--node-color": node.color,
                "--node-size": `${nodeSize}px`,
                "--radius": `${radius}px`,
              } as React.CSSProperties}
            >
              <span>{badgeFor(node.mtype)}</span>
            </button>
          );
        })}
      </div>

      <div className="nemo-orbit-copy__legend" aria-label="Memory type legend">
        {counts.map(([type, count, color]) => (
          <span className="nemo-orbit-copy__chip" key={type}>
            <i style={{ "--chip-color": color } as React.CSSProperties} />
            {type} {count}
          </span>
        ))}
      </div>
    </section>
  );
}