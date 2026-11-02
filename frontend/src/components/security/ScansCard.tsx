"use client";

import { Button } from "@/components/ui/button";
import { requestSecurityScan } from "@/lib/api";
import type { SecurityScan } from "@/types";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage, relativeTime } from "./labels";

const RESTARTABLE = new Set<SecurityScan["source"]>(["ci", "trivy-operator"]);
const ORDER: SecurityScan["source"][] = ["trivy-operator", "ci", "kyverno", "cnpg"];

export function ScansCard({
  project,
  scans,
  onRequested,
}: {
  project: string;
  scans: SecurityScan[];
  onRequested: () => void;
}) {
  const t = useTranslations("security");
  const locale = useLocale();
  const [busy, setBusy] = useState<string | null>(null);

  function when(scan: SecurityScan): string {
    if (scan.requested_at) return t("scans.requested");
    if (scan.status === "unavailable" || scan.status === "missing")
      return scan.message;
    const lastRun = t("scans.lastRun", {
      when: relativeTime(scan.last_run_at, locale) ?? t("common.never"),
    });
    if (!scan.next_run_at) return lastRun;
    const nextRun = t("scans.nextRun", {
      when: relativeTime(scan.next_run_at, locale) ?? t("common.never"),
    });
    return `${lastRun} · ${nextRun}`;
  }

  const rows = scans
    .filter((s) => ORDER.includes(s.source))
    .sort(
      (a, b) =>
        ORDER.indexOf(a.source) - ORDER.indexOf(b.source) ||
        (a.app ?? "").localeCompare(b.app ?? ""),
    );

  async function restart(scan: SecurityScan) {
    if (!scan.app) return;
    const key = `${scan.app}/${scan.source}`;
    setBusy(key);
    try {
      await requestSecurityScan(project, scan.app, scan.source as "ci" | "trivy-operator");
      toast.success(t("scans.started"));
      onRequested();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("scans.firstRun")}
      </p>
    );
  }

  return (
    <ul className="space-y-2.5">
      {rows.map((scan) => {
        const key = `${scan.app}/${scan.source}`;
        return (
          <li key={key} className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">
                {t(`scanSources.${scan.source}`)}
                {scan.app && (
                  <span className="text-muted-foreground"> · {scan.app}</span>
                )}
              </p>
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                {scan.status === "error" && (
                  <AlertCircle className="h-3 w-3 text-destructive" />
                )}
                {scan.status === "error" ? t("scans.readError") : when(scan)}
              </p>
            </div>
            {RESTARTABLE.has(scan.source) && scan.app && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy === key || !!scan.requested_at}
                onClick={() => restart(scan)}
              >
                {busy === key ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                {t("scans.rerun")}
              </Button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
