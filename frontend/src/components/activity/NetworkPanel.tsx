"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Badge } from "@/components/ui/badge";
import { getDeniedFlows } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DeniedFlow } from "@/types";
import { ChevronDown, ShieldBan } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { timeLabel } from "./labels";

/** Flows a network policy dropped: "why can't my backend reach X?". */
export function NetworkPanel({ project }: { project: string }) {
  const t = useTranslations("activity");
  const locale = useLocale();
  const [flows, setFlows] = useState<DeniedFlow[] | null>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    getDeniedFlows(project, 1).then(setFlows, () => setFlows([]));
  }, [project]);

  const total = flows?.reduce((a, f) => a + f.count, 0) ?? 0;

  const columns: DataColumn<DeniedFlow>[] = [
    { key: "source", header: t("network.table.source"), sortable: true, cell: (f) => f.source },
    { key: "destination", header: t("network.table.destination"), sortable: true, cell: (f) => f.destination },
    {
      key: "port",
      header: t("network.table.port"),
      cell: (f) => `${f.port ?? t("network.noPort")} ${f.protocol}`,
    },
    {
      key: "direction",
      header: t("network.table.direction"),
      cell: (f) => t(`network.direction.${f.direction === "ingress" ? "ingress" : "egress"}`),
    },
    { key: "count", header: t("network.table.count"), align: "right", sortable: true, cell: (f) => f.count },
    {
      key: "last",
      header: t("network.table.last"),
      sortable: true,
      className: "font-sans",
      cell: (f) => timeLabel(t, locale, f.last),
    },
  ];

  return (
    <div className="rounded-xl border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <ShieldBan className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{t("network.title")}</span>
        <Badge
          variant="outline"
          className={cn(total > 0 && "border-amber-500/40 bg-amber-500/15")}
        >
          {flows === null ? "…" : total}
        </Badge>
        <span className="ml-2 text-muted-foreground">
          {t("network.description")}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="border-t p-2">
          <DataTable
            columns={columns}
            rows={flows ?? []}
            rowKey={(f) => `${f.source}|${f.destination}|${f.port}|${f.direction}`}
            loading={flows === null}
            storageKey="activity.network"
            initialSort={{ key: "count", direction: "desc" }}
            empty={{ title: t("network.empty") }}
            className="font-mono text-xs"
          />
        </div>
      )}
    </div>
  );
}
