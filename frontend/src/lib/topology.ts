/**
 * The project map as the canvas draws it (CNP-242). Pure functions, tested in
 * tests/topology.test.mjs: no React, no fetch.
 *
 * Edge states follow cnp-docs 09-import-microservices/02-architecture.md.
 */

import type {
  Confidence,
  ImportPlan,
  ImportPlanEdits,
  Topology,
  TopologyEdge,
  TopologyEdgeState,
  TopologyNodeKind,
  TopologyPort,
} from "@/types";

export type GraphEdgeState = TopologyEdgeState | "draft" | "entry";
export type GraphNodeKind = TopologyNodeKind | "internet";
export type EdgeAction = "declare" | "unlink" | "remove" | "restore";

export const INTERNET_ID = "internet";
export const PLAN_PREFIX = "plan:";
const MAX_ALIAS = 63;
const DNS_1035 = /^[a-z]([-a-z0-9]*[a-z0-9])?$/;
// Same list as the CMP, cnp-projects validate-registry.py and infra-templates.
export const RESERVED_ALIASES = ["kubernetes", "default", "vault", "keycloak", "argocd"];
const OWN_SERVICE_SUFFIXES = ["", "-stable", "-canary"];

export interface GraphNode {
  id: string;
  name: string;
  kind: GraphNodeKind;
  rank: number;
  draft: boolean;
  health: string | null;
  sync: string | null;
  asleep: boolean | null;
  public: boolean;
  hostname: string | null;
  imported: boolean;
  digest: string | null;
  backingKind: string | null;
  linkable: boolean;
  ports: TopologyPort[];
  deploymentId: number | null;
  confidence: Confidence | null;
  unsupported: boolean;
  conflict: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  state: GraphEdgeState;
  port: number | null;
  alias: string | null;
  declared: boolean;
  rps: number | null;
  /** The plan link this edge draws, by plan names. */
  planLink: { from: string; to: string } | null;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface EdgeVisual {
  /** A CSS colour, from the --edge-* tokens of globals.css. */
  stroke: string;
  dash: string | undefined;
  width: number;
  /** Key under topology.edgeState in messages/*.json. */
  labelKey: GraphEdgeState;
}

const VISUALS: Record<GraphEdgeState, Omit<EdgeVisual, "labelKey">> = {
  declared_observed: { stroke: "var(--edge-observed)", dash: undefined, width: 2 },
  declared_only: { stroke: "var(--edge-idle)", dash: "6 4", width: 1.5 },
  observed_only: { stroke: "var(--edge-undeclared)", dash: undefined, width: 2 },
  denied: { stroke: "var(--edge-denied)", dash: undefined, width: 2.5 },
  draft: { stroke: "var(--edge-draft)", dash: "4 4", width: 1.5 },
  entry: { stroke: "var(--edge-idle)", dash: "2 4", width: 1 },
};

export function edgeVisual(state: GraphEdgeState): EdgeVisual {
  return { ...VISUALS[state], labelKey: state };
}

/**
 * What a user may do on an edge. Declaring needs two imported apps (the
 * registry refuses dependsOn on template apps); a denied flow is not fixed
 * by a declaration in v1 (no per-link network rule yet), so it offers none.
 */
export function edgeActions(
  edge: GraphEdge,
  nodes: Map<string, GraphNode>,
  canEdit: boolean,
  removedPlanLinks: Set<string> = new Set(),
): EdgeAction[] {
  if (!canEdit) return [];
  if (edge.state === "draft" && edge.planLink) {
    return [removedPlanLinks.has(planLinkKey(edge.planLink)) ? "restore" : "remove"];
  }
  if (edge.state === "entry") return [];
  if (edge.declared) return ["unlink"];
  const source = nodes.get(edge.source);
  const target = nodes.get(edge.target);
  if (edge.state === "observed_only" && source?.linkable && target?.linkable) {
    return ["declare"];
  }
  return [];
}

/** Columns, left to right: public entry, frontends, services, backing. */
export function nodeRank(kind: GraphNodeKind, isPublic: boolean): number {
  if (kind === "internet") return 0;
  if (kind === "static" || kind === "fullstack") return 1;
  if (kind === "service") return isPublic ? 1 : 2;
  return 3;
}

export function shortDigest(digest: string | null): string | null {
  if (!digest) return null;
  const hex = digest.replace(/^sha256:/, "");
  return hex.slice(0, 12);
}

/** A key of messages topology.alias, or null when the alias is fine. */
export function aliasProblem(
  alias: string,
  source: string,
): "format" | "reserved" | "own" | null {
  if (alias.length === 0 || alias.length > MAX_ALIAS || !DNS_1035.test(alias)) {
    return "format";
  }
  if (RESERVED_ALIASES.includes(alias)) return "reserved";
  if (OWN_SERVICE_SUFFIXES.some((suffix) => source + suffix === alias)) {
    return "own";
  }
  return null;
}

export function planLinkKey(link: { from: string; to: string }): string {
  return `${link.from}>${link.to}`;
}

function internetNode(): GraphNode {
  return {
    id: INTERNET_ID,
    name: INTERNET_ID,
    kind: "internet",
    rank: 0,
    draft: false,
    health: null,
    sync: null,
    asleep: null,
    public: true,
    hostname: null,
    imported: false,
    digest: null,
    backingKind: null,
    linkable: false,
    ports: [],
    deploymentId: null,
    confidence: null,
    unsupported: false,
    conflict: false,
  };
}

function entryEdge(target: string): GraphEdge {
  return {
    id: `${INTERNET_ID}->${target}`,
    source: INTERNET_ID,
    target,
    state: "entry",
    port: null,
    alias: null,
    declared: false,
    rps: null,
    planLink: null,
  };
}

function liveEdge(edge: TopologyEdge): GraphEdge {
  return {
    id: edge.id,
    source: edge.from,
    target: edge.to,
    state: edge.state,
    port: edge.port,
    alias: edge.alias,
    declared: edge.declared,
    rps: edge.observed?.rps ?? null,
    planLink: null,
  };
}

export function liveGraph(topology: Topology): Graph {
  const nodes: GraphNode[] = topology.nodes.map((node) => ({
    id: node.id,
    name: node.app,
    kind: node.kind,
    rank: nodeRank(node.kind, node.public),
    draft: false,
    health: node.health,
    sync: node.sync,
    asleep: node.asleep,
    public: node.public,
    hostname: node.hostname,
    imported: node.imported,
    digest: node.digest,
    backingKind: node.backing_kind,
    linkable: node.linkable,
    ports: node.ports,
    deploymentId: node.deployment_id,
    confidence: null,
    unsupported: false,
    conflict: false,
  }));
  const edges = topology.edges.map(liveEdge);
  const exposed = nodes.filter((n) => n.public);
  if (exposed.length > 0) {
    nodes.unshift(internetNode());
    edges.unshift(...exposed.map((n) => entryEdge(n.id)));
  }
  return { nodes, edges };
}

/**
 * The plan staged on top of what the project already runs. Plan nodes keep
 * their plan name in the id, so a rename never moves them on the canvas.
 * Dropped links and excluded apps stay drawn (greyed by the caller) so they
 * can be restored. An imported app the plan deploys again is drawn once, as
 * staged; a template app with a staged app's name is a conflict.
 */
export function draftGraph(
  plan: ImportPlan,
  edits: ImportPlanEdits,
  live: Topology | null,
): Graph {
  const finalName = (name: string) => edits.rename[name] || name;
  const staged = new Set(
    [...plan.services, ...plan.backing].map((app) => finalName(app.name)),
  );
  const full: Graph = live ? liveGraph(live) : { nodes: [], edges: [] };
  const replaced = new Set(
    full.nodes.filter((n) => n.imported && staged.has(n.name)).map((n) => n.id),
  );
  const base: Graph = {
    nodes: full.nodes.filter((n) => !replaced.has(n.id)),
    edges: full.edges.filter(
      (e) => !replaced.has(e.source) && !replaced.has(e.target),
    ),
  };
  const existing = new Set(
    base.nodes.filter((n) => n.kind !== "internet").map((n) => n.name),
  );
  const planNodes: GraphNode[] = [];

  for (const service of plan.services) {
    const name = finalName(service.name);
    const isPublic = edits.public[name] ?? service.public;
    planNodes.push({
      ...internetNode(),
      id: PLAN_PREFIX + service.name,
      name,
      kind: "service",
      rank: nodeRank("service", isPublic),
      draft: true,
      public: isPublic,
      asleep: null,
      ports: service.ports,
      confidence: service.confidence.overall,
      conflict: existing.has(name),
    });
  }
  for (const backing of plan.backing) {
    const name = finalName(backing.name);
    planNodes.push({
      ...internetNode(),
      id: PLAN_PREFIX + backing.name,
      name,
      kind: "backing",
      rank: nodeRank("backing", false),
      draft: true,
      public: false,
      backingKind: backing.kind,
      ports: backing.port ? [{ name: "main", port: backing.port }] : [],
      confidence: backing.supported ? "high" : null,
      unsupported: !backing.supported,
      conflict: existing.has(name),
    });
  }

  const planEdges: GraphEdge[] = plan.links.map((link) => ({
    id: `${PLAN_PREFIX}${planLinkKey(link)}`,
    source: PLAN_PREFIX + link.from,
    target: PLAN_PREFIX + link.to,
    state: "draft",
    port: edits.port[finalName(link.to)] ?? link.port,
    alias: link.alias,
    declared: false,
    rps: null,
    planLink: { from: link.from, to: link.to },
  }));

  const nodes = [...base.nodes, ...planNodes];
  const edges = [...base.edges, ...planEdges];
  const hasInternet = nodes.some((n) => n.id === INTERNET_ID);
  const exposedDrafts = planNodes.filter((n) => n.public);
  if (exposedDrafts.length > 0) {
    if (!hasInternet) nodes.unshift(internetNode());
    edges.push(...exposedDrafts.map((n) => entryEdge(n.id)));
  }
  return { nodes, edges };
}
