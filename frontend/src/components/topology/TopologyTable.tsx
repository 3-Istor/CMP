"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  edgeActions,
  INTERNET_ID,
  shortDigest,
  type EdgeAction,
  type Graph,
  type GraphEdge,
} from "@/lib/topology";
import { EdgeSample } from "./TopologyLegend";

const TH = "px-3 py-2 text-left text-xs font-medium text-muted-foreground";
const TD = "px-3 py-2 align-top";

/** The map as two tables: what the canvas shows, readable without it. */
export function TopologyTable({
  graph,
  projectName,
  canEdit,
  onEdgeAction,
}: {
  graph: Graph;
  projectName: string;
  canEdit: boolean;
  onEdgeAction?: (edge: GraphEdge, action: EdgeAction) => void;
}) {
  const t = useTranslations("topology");
  const nodes = graph.nodes.filter((n) => n.id !== INTERNET_ID);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const edges = graph.edges.filter((e) => e.state !== "entry");

  return (
    <div className="space-y-6">
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <caption className="px-3 py-2 text-left font-medium">{t("table.apps")}</caption>
          <thead className="border-y bg-muted/40">
            <tr>
              <th scope="col" className={TH}>{t("table.name")}</th>
              <th scope="col" className={TH}>{t("table.kind")}</th>
              <th scope="col" className={TH}>{t("table.health")}</th>
              <th scope="col" className={TH}>{t("table.exposure")}</th>
              <th scope="col" className={TH}>{t("table.image")}</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.id} className="border-b last:border-0">
                <th scope="row" className={`${TD} text-left font-medium`}>
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
                </th>
                <td className={TD}>{node.backingKind ?? t(`kind.${node.kind}`)}</td>
                <td className={TD}>
                  {node.asleep ? t("asleep") : t(`health.${node.health ?? "unknown"}`)}
                </td>
                <td className={TD}>
                  {node.hostname ? (
                    <a
                      href={`https://${node.hostname}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      {node.hostname}
                    </a>
                  ) : (
                    t("internal")
                  )}
                </td>
                <td className={`${TD} font-mono text-xs`}>{shortDigest(node.digest) ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <caption className="px-3 py-2 text-left font-medium">{t("table.links")}</caption>
          <thead className="border-y bg-muted/40">
            <tr>
              <th scope="col" className={TH}>{t("table.from")}</th>
              <th scope="col" className={TH}>{t("table.to")}</th>
              <th scope="col" className={TH}>{t("table.target")}</th>
              <th scope="col" className={TH}>{t("table.state")}</th>
              <th scope="col" className={TH}>{t("table.traffic")}</th>
              {canEdit && <th scope="col" className={TH}>{t("table.actions")}</th>}
            </tr>
          </thead>
          <tbody>
            {edges.length === 0 && (
              <tr>
                <td colSpan={canEdit ? 6 : 5} className={`${TD} text-muted-foreground`}>
                  {t("table.noLinks")}
                </td>
              </tr>
            )}
            {edges.map((edge) => {
              const actions = edgeActions(edge, byId, canEdit);
              return (
                <tr key={edge.id} className="border-b last:border-0">
                  <td className={TD}>{byId.get(edge.source)?.name ?? edge.source}</td>
                  <td className={TD}>{byId.get(edge.target)?.name ?? edge.target}</td>
                  <td className={`${TD} font-mono text-xs`}>
                    {edge.alias ? `${edge.alias}:${edge.port}` : "-"}
                  </td>
                  <td className={TD}>
                    <span className="inline-flex items-center gap-1.5">
                      <EdgeSample state={edge.state} />
                      {t(`edgeState.${edge.state}`)}
                    </span>
                  </td>
                  <td className={TD}>{edge.rps !== null ? t("rps", { value: edge.rps }) : "-"}</td>
                  {canEdit && (
                    <td className={TD}>
                      <div className="flex flex-wrap gap-1">
                        {actions.map((action) => (
                          <Button
                            key={action}
                            size="sm"
                            variant="outline"
                            onClick={() => onEdgeAction?.(edge, action)}
                            aria-label={t(`action.${action}Of`, {
                              from: byId.get(edge.source)?.name ?? edge.source,
                              to: byId.get(edge.target)?.name ?? edge.target,
                            })}
                          >
                            {t(`action.${action}`)}
                          </Button>
                        ))}
                      </div>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
