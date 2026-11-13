"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  MarkerType,
  Panel,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnSelectionChangeParams,
} from "@xyflow/react";
import { Link2Off, Plus, Undo2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/lib/theme";
import {
  edgeActions,
  edgeVisual,
  planLinkKey,
  type EdgeAction,
  type Graph,
  type GraphEdge,
  type GraphNode,
} from "@/lib/topology";
import { AppNode, type AppFlowNode } from "./AppNode";
import { layoutGraph } from "./layout";
import { StateEdge, type StateFlowEdge } from "./StateEdge";

const nodeTypes = { app: AppNode };
const edgeTypes = { state: StateEdge };

export interface TopologyCanvasProps {
  graph: Graph;
  ariaLabel: string;
  /** Admins and owners: edge actions and, in link mode, node handles. */
  canEdit: boolean;
  linkMode?: boolean;
  dimmedNodes?: Set<string>;
  /** Plan links the user removed, by planLinkKey. */
  removedPlanLinks?: Set<string>;
  onConnect?: (source: GraphNode, target: GraphNode) => void;
  onEdgeAction?: (edge: GraphEdge, action: EdgeAction) => void;
  onSelectNode?: (node: GraphNode | null) => void;
  className?: string;
}

const ACTION_ICON = { declare: Plus, unlink: Link2Off, remove: X, restore: Undo2 };

function structureKey(graph: Graph): string {
  return [
    ...graph.nodes.map((n) => n.id).sort(),
    "|",
    ...graph.edges.map((e) => e.id).sort(),
  ].join(",");
}

export function TopologyCanvas({
  graph,
  ariaLabel,
  canEdit,
  linkMode = false,
  dimmedNodes,
  removedPlanLinks,
  onConnect,
  onEdgeAction,
  onSelectNode,
  className,
}: TopologyCanvasProps) {
  const t = useTranslations("topology");
  const [theme] = useTheme();
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const [nodes, setNodes] = useState<AppFlowNode[]>([]);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const laidOut = useRef<string>("");

  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const removed = useMemo(() => removedPlanLinks ?? new Set<string>(), [removedPlanLinks]);
  const key = structureKey(graph);

  useEffect(() => {
    if (laidOut.current === key) return;
    let cancelled = false;
    layoutGraph(graph)
      .then((next) => {
        if (!cancelled) {
          laidOut.current = key;
          setPositions(next);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [graph, key]);

  useEffect(() => {
    setNodes((current) => {
      const dragged = new Map(current.map((n) => [n.id, n.position]));
      return graph.nodes
        .filter((node) => positions.has(node.id))
        .map((node) => ({
          id: node.id,
          type: "app" as const,
          position: dragged.get(node.id) ?? positions.get(node.id)!,
          ariaLabel: t("nodeLabel", {
            name: node.name,
            kind: t(`kind.${node.kind}`),
            health: node.asleep ? t("asleep") : t(`health.${node.health ?? "unknown"}`),
          }),
          connectable: linkMode && canEdit && node.linkable,
          selectable: node.kind !== "internet",
          data: {
            node,
            dimmed: dimmedNodes?.has(node.id) ?? false,
            connectable: linkMode && canEdit && node.linkable,
          },
        }));
    });
  }, [graph.nodes, positions, linkMode, canEdit, dimmedNodes, t]);

  const edges: StateFlowEdge[] = useMemo(
    () =>
      graph.edges
        .filter((e) => byId.has(e.source) && byId.has(e.target))
        .map((edge) => {
          const visual = edgeVisual(edge.state);
          return {
            id: edge.id,
            type: "state" as const,
            source: edge.source,
            target: edge.target,
            selected: edge.id === selectedEdge,
            focusable: edge.state !== "entry",
            selectable: edge.state !== "entry",
            ariaLabel: t("edgeLabel", {
              from: byId.get(edge.source)?.name ?? edge.source,
              to: byId.get(edge.target)?.name ?? edge.target,
              state: t(`edgeState.${edge.state}`),
            }),
            markerEnd: { type: MarkerType.ArrowClosed, color: visual.stroke, width: 16, height: 16 },
            data: {
              edge,
              dimmed: edge.planLink ? removed.has(planLinkKey(edge.planLink)) : false,
              actions: edgeActions(edge, byId, canEdit, removed),
              onAction: onEdgeAction,
            },
          };
        }),
    [graph.edges, byId, selectedEdge, canEdit, removed, onEdgeAction, t],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<AppFlowNode>[]) => setNodes((current) => applyNodeChanges(changes, current)),
    [],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const source = byId.get(connection.source);
      const target = byId.get(connection.target);
      if (source && target && source.id !== target.id) onConnect?.(source, target);
    },
    [byId, onConnect],
  );

  // Edges are derived from the graph, not stored: only their selection is.
  const onEdgesChange = useCallback((changes: EdgeChange<StateFlowEdge>[]) => {
    for (const change of changes) {
      if (change.type !== "select") continue;
      setSelectedEdge((current) =>
        change.selected ? change.id : current === change.id ? null : current,
      );
    }
  }, []);

  const handleSelection = useCallback(
    ({ nodes: picked }: OnSelectionChangeParams) => {
      onSelectNode?.(picked[0] ? (byId.get(picked[0].id) ?? null) : null);
    },
    [byId, onSelectNode],
  );

  const current = graph.edges.find((e) => e.id === selectedEdge) ?? null;
  const currentActions = current ? edgeActions(current, byId, canEdit, removed) : [];

  const ariaLabelConfig = useMemo(
    () => ({
      "node.a11yDescription.default": t("a11y.node"),
      "node.a11yDescription.keyboardDisabled": t("a11y.node"),
      "edge.a11yDescription.default": t("a11y.edge"),
      "controls.ariaLabel": t("a11y.controls"),
      "controls.zoomIn.ariaLabel": t("a11y.zoomIn"),
      "controls.zoomOut.ariaLabel": t("a11y.zoomOut"),
      "controls.fitView.ariaLabel": t("a11y.fitView"),
      "controls.interactive.ariaLabel": t("a11y.interactive"),
      "handle.ariaLabel": t("a11y.handle"),
    }),
    [t],
  );

  return (
    <div
      role="region"
      aria-label={ariaLabel}
      className={className ?? "h-[min(70vh,720px)] min-h-[420px] w-full overflow-hidden rounded-xl border bg-muted/30"}
    >
      <ReactFlow<AppFlowNode, StateFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        onSelectionChange={handleSelection}
        nodesConnectable={linkMode && canEdit}
        nodesFocusable
        edgesFocusable
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.2}
        colorMode={theme}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={null}
        ariaLabelConfig={ariaLabelConfig}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
        {current && (
          <Panel position="top-right">
            <div className="w-64 space-y-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-md">
              <div className="flex items-start justify-between gap-2">
                <p className="font-medium">
                  {byId.get(current.source)?.name} {"->"} {byId.get(current.target)?.name}
                </p>
                <button
                  type="button"
                  onClick={() => setSelectedEdge(null)}
                  aria-label={t("closeDetails")}
                  className="rounded p-0.5 text-muted-foreground hover:bg-accent"
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
              <p className="text-muted-foreground">{t(`edgeState.${current.state}`)}</p>
              {current.alias && (
                <p className="font-mono text-xs">
                  {current.alias}:{current.port}
                </p>
              )}
              {current.rps !== null && (
                <p className="text-xs text-muted-foreground">{t("rps", { value: current.rps })}</p>
              )}
              {currentActions.length > 0 && onEdgeAction && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {currentActions.map((action) => {
                    const Icon = ACTION_ICON[action];
                    return (
                      <Button
                        key={action}
                        size="sm"
                        variant={action === "unlink" || action === "remove" ? "outline" : "default"}
                        onClick={() => onEdgeAction(current, action)}
                      >
                        <Icon aria-hidden />
                        {t(`action.${action}`)}
                      </Button>
                    );
                  })}
                </div>
              )}
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}
