"use client";

import { GitMerge, Link2, List, Network, RefreshCw, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { Button, buttonVariants } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { getTopology, linkApps, unlinkApps } from "@/lib/api";
import { cn } from "@/lib/utils";
import { liveGraph, type EdgeAction, type GraphEdge, type GraphNode } from "@/lib/topology";
import type { Topology } from "@/types";
import { LinkDialog, type LinkRequest } from "./LinkDialog";
import { TopologyCanvas } from "./TopologyCanvas";
import { TopologyLegend } from "./TopologyLegend";
import { TopologyTable } from "./TopologyTable";

const POLL_MS = 30_000;

export function TopologyView() {
  const t = useTranslations("topology");
  const { projectName, can } = useProjectContext();
  const canEdit = can("topology.link");
  const [topology, setTopology] = useState<Topology | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<"map" | "list">("map");
  const [linkMode, setLinkMode] = useState(false);
  const [linking, setLinking] = useState<{ from?: string; to?: string } | null>(null);
  const [unlinking, setUnlinking] = useState<GraphEdge | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setTopology(await getTopology(projectName));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, [projectName]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const graph = useMemo(() => (topology ? liveGraph(topology) : null), [topology]);
  const linkable = useMemo(() => graph?.nodes.filter((n) => n.linkable) ?? [], [graph]);

  const onEdgeAction = useCallback((edge: GraphEdge, action: EdgeAction) => {
    if (action === "declare") setLinking({ from: edge.source, to: edge.target });
    if (action === "unlink") setUnlinking(edge);
  }, []);

  const onConnect = useCallback((source: GraphNode, target: GraphNode) => {
    setLinking({ from: source.id, to: target.id });
  }, []);

  const submitLink = async (link: LinkRequest) => {
    await linkApps(projectName, link);
    toast.success(t("linked", { from: link.from, to: link.to }));
    void refresh();
  };

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <TopologyLegend observedAvailable={topology?.observed_available ?? true} />
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label={t("viewLabel")} className="inline-flex rounded-lg border p-0.5">
          <Button
            size="sm"
            variant={view === "map" ? "secondary" : "ghost"}
            aria-pressed={view === "map"}
            onClick={() => setView("map")}
          >
            <Network aria-hidden />
            {t("mapView")}
          </Button>
          <Button
            size="sm"
            variant={view === "list" ? "secondary" : "ghost"}
            aria-pressed={view === "list"}
            onClick={() => setView("list")}
          >
            <List aria-hidden />
            {t("listView")}
          </Button>
        </div>
        {canEdit && linkable.length > 1 && (
          <>
            <Button
              size="sm"
              variant={linkMode ? "default" : "outline"}
              aria-pressed={linkMode}
              onClick={() => setLinkMode((v) => !v)}
            >
              <GitMerge aria-hidden />
              {t("linkMode")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setLinking({})}>
              <Link2 aria-hidden />
              {t("linkDialog")}
            </Button>
          </>
        )}
        <Button
          size="icon"
          variant="ghost"
          onClick={() => void refresh()}
          disabled={refreshing}
          aria-label={t("refresh")}
          title={t("refresh")}
        >
          <RefreshCw className={refreshing ? "animate-spin" : undefined} aria-hidden />
        </Button>
      </div>
    </div>
  );

  if (!topology && !error) {
    return <Skeleton className="h-[420px] w-full rounded-xl" />;
  }

  if (!graph) {
    return (
      <div role="alert" className="space-y-2 rounded-xl border border-destructive bg-destructive/5 p-6 text-destructive">
        <p className="font-medium">{t("loadFailed")}</p>
        <p className="text-sm">{error}</p>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          {t("retry")}
        </Button>
      </div>
    );
  }

  if (topology && topology.nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-center text-muted-foreground">
        <Network className="mb-3 h-9 w-9" aria-hidden />
        <p className="font-medium text-foreground">{t("empty")}</p>
        <p className="mt-1 max-w-md text-sm">{t("emptyBody")}</p>
        {can("import.manage") && (
          <Link
            href={`/projects/${encodeURIComponent(projectName)}/import`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-4")}
          >
            <Upload aria-hidden />
            {t("importRepo")}
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {header}
      {error && (
        <p role="status" className="text-sm text-destructive">
          {t("staleData")}
        </p>
      )}
      {linkMode && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("linkModeHelp")}
        </p>
      )}
      {view === "map" ? (
        <>
          <TopologyCanvas
            graph={graph}
            ariaLabel={t("canvasLabel", { project: projectName })}
            canEdit={canEdit}
            linkMode={linkMode}
            onConnect={onConnect}
            onEdgeAction={onEdgeAction}
          />
          <p className="text-xs text-muted-foreground">{t("canvasHelp")}</p>
        </>
      ) : (
        <TopologyTable graph={graph} projectName={projectName} canEdit={canEdit} onEdgeAction={onEdgeAction} />
      )}

      <LinkDialog
        open={linking !== null}
        onOpenChange={(open) => !open && setLinking(null)}
        nodes={linkable}
        initialFrom={linking?.from}
        initialTo={linking?.to}
        onSubmit={submitLink}
      />
      <ConfirmDialog
        open={unlinking !== null}
        onOpenChange={(open) => !open && setUnlinking(null)}
        level={1}
        title={t("unlink.title", {
          from: unlinking?.source ?? "",
          to: unlinking?.target ?? "",
        })}
        description={t("unlink.description", { alias: unlinking?.alias ?? "" })}
        confirmLabel={t("unlink.confirm")}
        destructive
        onConfirm={async () => {
          if (!unlinking) return;
          await unlinkApps(projectName, {
            from: unlinking.source,
            to: unlinking.target,
            alias: unlinking.alias,
          });
          toast.success(t("unlinked"));
          void refresh();
        }}
      />
    </div>
  );
}
