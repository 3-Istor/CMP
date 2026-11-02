"use client";

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
  const [open, setOpen] = useState(false);

  useEffect(() => {
    getDeniedFlows(project, 1).then(setFlows, () => setFlows([]));
  }, [project]);

  const total = flows?.reduce((a, f) => a + f.count, 0) ?? 0;

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
        <div className="overflow-x-auto border-t">
          {flows && flows.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-1.5 font-medium">{t("network.table.source")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("network.table.destination")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("network.table.port")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("network.table.direction")}</th>
                  <th className="px-3 py-1.5 text-right font-medium">{t("network.table.count")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("network.table.last")}</th>
                </tr>
              </thead>
              <tbody className="font-mono text-xs">
                {flows.map((f) => (
                  <tr
                    key={`${f.source}|${f.destination}|${f.port}|${f.direction}`}
                    className="border-t"
                  >
                    <td className="px-3 py-1.5">{f.source}</td>
                    <td className="px-3 py-1.5">{f.destination}</td>
                    <td className="px-3 py-1.5">
                      {f.port ?? t("network.noPort")} {f.protocol}
                    </td>
                    <td className="px-3 py-1.5">
                      {t(`network.direction.${f.direction === "ingress" ? "ingress" : "egress"}`)}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {f.count}
                    </td>
                    <td className="px-3 py-1.5 font-sans">
                      {timeLabel(t, locale, f.last)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              {t("network.empty")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
