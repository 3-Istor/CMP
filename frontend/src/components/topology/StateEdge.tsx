"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { useTranslations } from "next-intl";

import { roundedPath, routeLabelPoint, type Point } from "@/lib/edgeRoute";
import { cn } from "@/lib/utils";
import { edgeVisual, type EdgeAction, type GraphEdge } from "@/lib/topology";

export interface StateEdgeData extends Record<string, unknown> {
  edge: GraphEdge;
  dimmed: boolean;
  /** Another node is hovered and this edge does not touch it. */
  faded: boolean;
  /** The layout route, null once an end node was dragged away from it. */
  route: Point[] | null;
  /** The label repeats what the target node shows: drawn only when selected. */
  labelImplied: boolean;
  actions: EdgeAction[];
  onAction?: (edge: GraphEdge, action: EdgeAction) => void;
}

export type StateFlowEdge = Edge<StateEdgeData, "state">;

function formatRps(rps: number): string {
  return rps >= 10 ? rps.toFixed(0) : rps.toFixed(2);
}

export function StateEdge({
  id,
  data,
  selected,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
}: EdgeProps<StateFlowEdge>) {
  const t = useTranslations("topology");
  if (!data) return null;
  const { edge, dimmed, faded, route, labelImplied, actions, onAction } = data;
  let path: string;
  let labelX: number;
  let labelY: number;
  if (route) {
    path = roundedPath(route);
    ({ x: labelX, y: labelY } = routeLabelPoint(route));
  } else {
    [path, labelX, labelY] = getSmoothStepPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
      borderRadius: 8,
    });
  }
  const visual = edgeVisual(edge.state);
  const label = [
    edge.alias && edge.port ? `${edge.alias}:${edge.port}` : edge.port ? String(edge.port) : null,
    edge.rps ? t("rps", { value: formatRps(edge.rps) }) : null,
  ]
    .filter(Boolean)
    .join(" ");
  const declare = actions.includes("declare");
  const showLabel = Boolean(label) && (selected || !labelImplied);

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: visual.stroke,
          strokeWidth: selected ? visual.width + 1.5 : visual.width,
          strokeDasharray: dimmed ? "2 6" : visual.dash,
          opacity: faded ? 0.15 : dimmed ? 0.5 : 1,
          transition: "opacity 120ms",
        }}
      />
      {(showLabel || declare) && !faded && edge.state !== "entry" && (edge.state !== "draft" || selected) && (
        <EdgeLabelRenderer>
          <div
            className={cn(
              "nodrag nopan absolute flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] leading-4 text-foreground shadow-sm",
              dimmed && "line-through",
            )}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "all",
            }}
          >
            {showLabel && <span className="font-mono">{label}</span>}
            {declare && onAction && (
              <button
                type="button"
                onClick={() => onAction(edge, "declare")}
                className="rounded border border-amber-600/50 px-1 font-medium text-amber-800 hover:bg-amber-50 focus-visible:ring-2 focus-visible:ring-ring dark:text-amber-300 dark:hover:bg-amber-950"
              >
                {t("declare")}
              </button>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
