"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Button } from "@/components/ui/button";
import {
  edgeActions,
  INTERNET_ID,
  isPlatformHostname,
  shortDigest,
  type EdgeAction,
  type Graph,
  type GraphEdge,
  type GraphNode,
} from "@/lib/topology";
import { EdgeSample } from "./TopologyLegend";

type NodeRow = GraphNode;

/** The map as two tables: what the canvas shows, readable without it. */
export function TopologyTable({
  graph,
  projectName,
  canEdit,
  onEdgeAction,
}: {
  graph: Graph | null;
  projectName: string;
  canEdit: boolean;
  onEdgeAction?: (edge: GraphEdge, action: EdgeAction) => void;
}) {
  const t = useTranslations("topology");
  const nodes: NodeRow[] = (graph?.nodes ?? []).filter((n) => n.id !== INTERNET_ID);
  const byId = new Map((graph?.nodes ?? []).map((n) => [n.id, n]));
  const edges = (graph?.edges ?? []).filter((e) => e.state !== "entry");
  const nameOf = (id: string) => byId.get(id)?.name ?? id;

  const nodeColumns: DataColumn<NodeRow>[] = [
    {
      key: "name",
      header: t("table.name"),
      sortable: true,
      className: "align-top font-medium",
      cell: (node) => (
        <>
          {node.deploymentId ? (
            <Link
              href={`/projects/${encodeURIComponent(projectName)}/apps/${node.deploymentId}`}
              className="text-primary hover:underline"
            >
              {node.name}
            </Link>
          ) : (
            node.name
          )}
          {node.imported && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">{t("imported")}</span>
          )}
        </>
      ),
    },
    {
      key: "kind",
      header: t("table.kind"),
      sortable: true,
      sortValue: (node) => node.backingKind ?? t(`kind.${node.kind}`),
      className: "align-top",
      cell: (node) => node.backingKind ?? t(`kind.${node.kind}`),
    },
    {
      key: "health",
      header: t("table.health"),
      className: "align-top",
      cell: (node) => (node.asleep ? t("asleep") : t(`health.${node.health ?? "unknown"}`)),
    },
    {
      key: "exposure",
      header: t("table.exposure"),
      className: "align-top",
      cell: (node) =>
        isPlatformHostname(node.hostname) ? (
          <a
            href={`https://${node.hostname}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {node.hostname}
          </a>
        ) : (
          (node.hostname ?? t("internal"))
        ),
    },
    {
      key: "image",
      header: t("table.image"),
      className: "align-top font-mono text-xs",
      cell: (node) => shortDigest(node.digest) ?? "-",
    },
  ];

  const edgeColumns: DataColumn<GraphEdge>[] = [
    {
      key: "source",
      header: t("table.from"),
      sortable: true,
      sortValue: (edge) => nameOf(edge.source),
      className: "align-top",
      cell: (edge) => nameOf(edge.source),
    },
    {
      key: "target",
      header: t("table.to"),
      sortable: true,
      sortValue: (edge) => nameOf(edge.target),
      className: "align-top",
      cell: (edge) => nameOf(edge.target),
    },
    {
      key: "alias",
      header: t("table.target"),
      className: "align-top font-mono text-xs",
      cell: (edge) => (edge.alias ? `${edge.alias}:${edge.port}` : "-"),
    },
    {
      key: "state",
      header: t("table.state"),
      className: "align-top",
      cell: (edge) => (
        <span className="inline-flex items-center gap-1.5">
          <EdgeSample state={edge.state} />
          {t(`edgeState.${edge.state}`)}
        </span>
      ),
    },
    {
      key: "rps",
      header: t("table.traffic"),
      sortable: true,
      className: "align-top",
      cell: (edge) => (edge.rps !== null ? t("rps", { value: edge.rps }) : "-"),
    },
    ...(canEdit
      ? [
          {
            key: "actions",
            header: t("table.actions"),
            className: "align-top",
            cell: (edge: GraphEdge) => (
              <div className="flex flex-wrap gap-1">
                {edgeActions(edge, byId, canEdit).map((action) => (
                  <Button
                    key={action}
                    size="sm"
                    variant="outline"
                    onClick={() => onEdgeAction?.(edge, action)}
                    aria-label={t(`action.${action}Of`, {
                      from: nameOf(edge.source),
                      to: nameOf(edge.target),
                    })}
                  >
                    {t(`action.${action}`)}
                  </Button>
                ))}
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h3 className="px-1 text-sm font-medium">{t("table.apps")}</h3>
        <DataTable
          caption={t("table.apps")}
          columns={nodeColumns}
          rows={nodes}
          rowKey={(node) => node.id}
          loading={!graph}
          storageKey="topology.apps"
        />
      </section>
      <section className="space-y-2">
        <h3 className="px-1 text-sm font-medium">{t("table.links")}</h3>
        <DataTable
          caption={t("table.links")}
          columns={edgeColumns}
          rows={edges}
          rowKey={(edge) => edge.id}
          loading={!graph}
          storageKey="topology.links"
          empty={{ title: t("table.noLinks") }}
        />
      </section>
    </div>
  );
}
