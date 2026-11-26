"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { CLOUD_META } from "@/components/finops/format";
import { Badge } from "@/components/ui/badge";
import type { EmergencyMove } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

const cloudLabel = (cloud: string) =>
  CLOUD_META[cloud as keyof typeof CLOUD_META]?.label ?? cloud;

const MOVE_STATUS: Record<string, string> = {
  failed: "bg-destructive/10 text-destructive",
  cancelling: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  running: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
};

/** Active, failed and recent project moves, automatic ones highlighted. */
export function MovesTable({ moves, loading }: { moves: EmergencyMove[]; loading: boolean }) {
  const t = useTranslations("emergency");
  const tSteps = useTranslations("projectSettings.location.steps");
  const columns: DataColumn<EmergencyMove>[] = [
    { key: "project", header: t("project"), sortable: true, className: "font-semibold", cell: (m) => m.project },
    {
      key: "route",
      header: t("route"),
      className: "whitespace-nowrap",
      cell: (m) => `${cloudLabel(m.source)} → ${cloudLabel(m.target)}`,
    },
    {
      key: "step",
      header: t("step"),
      cell: (m) => (m.step && tSteps.has(m.step) ? tSteps(m.step) : (m.step ?? "")),
    },
    {
      key: "status",
      header: t("status"),
      sortable: true,
      cell: (m) => (
        <span
          className={cn(
            "inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium",
            MOVE_STATUS[m.status] ?? "bg-muted text-muted-foreground",
          )}
        >
          {m.status}
        </span>
      ),
    },
    {
      key: "started_at",
      header: t("started"),
      sortable: true,
      sortValue: (m) => Date.parse(m.started_at),
      className: "whitespace-nowrap tabular-nums",
      cell: (m) => new Date(m.started_at).toLocaleString(),
    },
    {
      key: "trigger",
      header: t("trigger"),
      cell: (m) => <Badge variant={m.auto ? "default" : "outline"}>{m.auto ? t("auto") : t("manual")}</Badge>,
    },
  ];

  return (
    <section id="moves" aria-labelledby="moves-title" className="scroll-mt-6 space-y-2">
      <h2 id="moves-title" className="text-sm font-semibold">
        {t("movesTitle")}
      </h2>
      <DataTable
        columns={columns}
        rows={moves}
        rowKey={(m) => m.id}
        loading={loading}
        storageKey="emergency.moves"
        rowClassName={(m) => (m.auto ? "bg-amber-500/10" : undefined)}
        empty={{ title: t("movesEmpty") }}
      />
    </section>
  );
}
