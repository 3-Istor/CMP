import type { ElkNode } from "elkjs/lib/elk-api";
import ELK from "elkjs/lib/elk.bundled.js";
import type { Graph } from "@/lib/topology";

export const NODE_WIDTH = 232;
export const NODE_HEIGHT = 92;
export const INTERNET_SIZE = 64;

let elk: InstanceType<typeof ELK> | null = null;

/** Positions by node id: layered, left to right, one partition per rank. */
export async function layoutGraph(
  graph: Graph,
): Promise<Map<string, { x: number; y: number }>> {
  elk ??= new ELK();
  const ids = new Set(graph.nodes.map((n) => n.id));
  const root: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      "elk.layered.spacing.nodeNodeBetweenLayers": "150",
      "elk.spacing.nodeNode": "40",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    },
    children: graph.nodes.map((node) => {
      const size = node.kind === "internet" ? INTERNET_SIZE : null;
      return {
        id: node.id,
        width: size ?? NODE_WIDTH,
        height: size ?? NODE_HEIGHT,
        layoutOptions: { "elk.partitioning.partition": String(node.rank) },
      };
    }),
    edges: graph.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((edge) => ({
        id: edge.id,
        sources: [edge.source],
        targets: [edge.target],
      })),
  };
  const laid = await elk.layout(root);
  return new Map(
    (laid.children ?? []).map((child) => [
      child.id,
      { x: child.x ?? 0, y: child.y ?? 0 },
    ]),
  );
}
