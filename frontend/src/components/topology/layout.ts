import type { ElkNode } from "elkjs/lib/elk-api";
import ELK from "elkjs/lib/elk.bundled.js";
import type { Point } from "@/lib/edgeRoute";
import type { Graph } from "@/lib/topology";

export const NODE_WIDTH = 232;
export const NODE_HEIGHT = 92;
export const INTERNET_SIZE = 64;

export interface GraphLayout {
  positions: Map<string, Point>;
  /** Orthogonal route of each edge, from the source border to the target border. */
  routes: Map<string, Point[]>;
}

let elk: InstanceType<typeof ELK> | null = null;

/**
 * Layered, left to right, with orthogonal edge routing. The internet is the
 * first column and backing services the last; ELK orders the services
 * between them by who calls whom.
 */
export async function layoutGraph(graph: Graph): Promise<GraphLayout> {
  elk ??= new ELK();
  const ids = new Set(graph.nodes.map((n) => n.id));
  const root: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.layering.strategy": "LONGEST_PATH_SOURCE",
      "elk.layered.spacing.nodeNodeBetweenLayers": "110",
      "elk.layered.spacing.edgeNodeBetweenLayers": "20",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
      "elk.spacing.edgeEdge": "14",
      "elk.spacing.nodeNode": "36",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.thoroughness": "50",
    },
    children: graph.nodes.map((node) => {
      const size = node.kind === "internet" ? INTERNET_SIZE : null;
      const constraint = node.rank === 0 ? "FIRST_SEPARATE" : node.rank === 3 ? "LAST_SEPARATE" : null;
      return {
        id: node.id,
        width: size ?? NODE_WIDTH,
        height: size ?? NODE_HEIGHT,
        layoutOptions: {
          "elk.portConstraints": "FREE",
          ...(constraint ? { "elk.layered.layering.layerConstraint": constraint } : {}),
        },
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
  const routes = new Map<string, Point[]>();
  for (const edge of laid.edges ?? []) {
    const section = edge.sections?.[0];
    if (section) routes.set(edge.id, [section.startPoint, ...(section.bendPoints ?? []), section.endPoint]);
  }
  return {
    positions: new Map(
      (laid.children ?? []).map((child) => [child.id, { x: child.x ?? 0, y: child.y ?? 0 }]),
    ),
    routes,
  };
}
