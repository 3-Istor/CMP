"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Box,
  Database,
  ExternalLink,
  FileCode2,
  Globe,
  Layers,
  Moon,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import {
  isPlatformHostname,
  shortDigest,
  type GraphNode,
  type GraphNodeKind,
} from "@/lib/topology";
import { INTERNET_SIZE, NODE_HEIGHT, NODE_WIDTH } from "./layout";

export const KIND_ICON: Record<GraphNodeKind, LucideIcon> = {
  internet: Globe,
  static: FileCode2,
  fullstack: Layers,
  service: Box,
  backing: Database,
};

const HEALTH_DOT: Record<string, string> = {
  healthy: "bg-green-500",
  progressing: "bg-amber-400",
  suspended: "bg-muted-foreground",
  unknown: "bg-muted-foreground",
  missing: "bg-orange-500",
  degraded: "bg-red-500",
};

const CONFIDENCE_CHIP: Record<string, string> = {
  high: "border-green-600/40 text-green-700 dark:text-green-400",
  medium: "border-amber-600/40 text-amber-800 dark:text-amber-300",
  low: "border-red-600/40 text-red-700 dark:text-red-400",
};

export interface AppNodeData extends Record<string, unknown> {
  node: GraphNode;
  dimmed: boolean;
  connectable: boolean;
}

export type AppFlowNode = Node<AppNodeData, "app">;

const handleClass = (visible: boolean) =>
  cn(
    "!h-3 !w-3 !border-2 !border-background",
    visible ? "!bg-primary" : "!pointer-events-none !opacity-0",
  );

export function AppNode({ data, selected }: NodeProps<AppFlowNode>) {
  const t = useTranslations("topology");
  const { node, dimmed, connectable } = data;
  const Icon = KIND_ICON[node.kind];

  if (node.kind === "internet") {
    return (
      <div
        className="grid place-items-center rounded-full border bg-card text-muted-foreground"
        style={{ width: INTERNET_SIZE, height: INTERNET_SIZE }}
        title={t("internet")}
      >
        <Globe className="h-6 w-6" aria-hidden />
        <span className="sr-only">{t("internet")}</span>
        <Handle type="source" position={Position.Right} isConnectable={false} className={handleClass(false)} />
      </div>
    );
  }

  const digest = shortDigest(node.digest);
  const health = node.asleep ? null : node.health;
  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-lg border bg-card px-3 py-2 text-card-foreground shadow-sm",
        node.draft && "border-2 border-dashed border-primary/60",
        node.conflict && "border-destructive",
        selected && "ring-2 ring-ring",
        dimmed && "opacity-60",
      )}
      style={{ width: NODE_WIDTH, minHeight: NODE_HEIGHT }}
    >
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={connectable}
        className={handleClass(connectable)}
      />
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate font-medium" title={node.name}>
          {node.name}
        </span>
        {health && (
          <span
            role="img"
            aria-label={t(`health.${health in HEALTH_DOT ? health : "unknown"}`)}
            title={t(`health.${health in HEALTH_DOT ? health : "unknown"}`)}
            className={cn("h-2.5 w-2.5 shrink-0 rounded-full", HEALTH_DOT[health] ?? HEALTH_DOT.unknown)}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[11px] leading-4">
        <span className="text-muted-foreground">
          {node.backingKind ?? t(`kind.${node.kind}`)}
        </span>
        {node.asleep && (
          <span className="inline-flex items-center gap-0.5 rounded border px-1 text-muted-foreground">
            <Moon className="h-3 w-3" aria-hidden />
            {t("asleep")}
          </span>
        )}
        {node.imported && (
          <span className="rounded border px-1 text-muted-foreground">{t("imported")}</span>
        )}
        {node.draft && (
          <span className="rounded border border-primary/50 px-1 text-primary">{t("staged")}</span>
        )}
        {node.draft && node.confidence && (
          <span className={cn("rounded border px-1", CONFIDENCE_CHIP[node.confidence])}>
            {t(`confidence.${node.confidence}`)}
          </span>
        )}
        {node.unsupported && (
          <span className="rounded border border-red-600/40 px-1 text-red-700 dark:text-red-400">
            {t("unsupported")}
          </span>
        )}
        {node.conflict && (
          <span className="rounded border border-red-600/40 px-1 text-red-700 dark:text-red-400">
            {t("nameTaken")}
          </span>
        )}
        {digest && (
          <span className="font-mono text-muted-foreground" title={node.digest ?? undefined}>
            {digest}
          </span>
        )}
      </div>
      {node.hostname && !isPlatformHostname(node.hostname) && (
        <span className="truncate text-[11px] text-muted-foreground">{node.hostname}</span>
      )}
      {isPlatformHostname(node.hostname) && (
        <a
          href={`https://${node.hostname}`}
          target="_blank"
          rel="noopener noreferrer"
          className="nodrag nopan inline-flex min-w-0 items-center gap-1 text-[11px] text-primary hover:underline"
        >
          <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate">{node.hostname}</span>
        </a>
      )}
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={connectable}
        className={handleClass(connectable)}
      />
    </div>
  );
}
