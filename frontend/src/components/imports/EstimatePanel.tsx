"use client";

import { Calculator, Loader2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { estimateImport } from "@/lib/api";
import { useDebounced } from "@/lib/useDebounced";
import { cn } from "@/lib/utils";
import type { ImportEstimate, ImportPlanEdits, ResourceAmount } from "@/types";

const DEBOUNCE_MS = 400;

function cpu(m: number) {
  return m >= 1000 ? `${(m / 1000).toLocaleString()} vCPU` : `${m}m`;
}

function Amount({ value }: { value: ResourceAmount }) {
  return (
    <span className="font-mono text-xs">
      {value.mem_mib} Mio · {cpu(value.cpu_m)}
    </span>
  );
}

/**
 * What the edited plan will request and cost, against the project quota and
 * the free room of the cluster, and on a re-import what changes (CNP-307).
 */
export function EstimatePanel({
  projectName,
  runId,
  edits,
  initial,
}: {
  projectName: string;
  runId: string;
  edits: ImportPlanEdits;
  initial: ImportEstimate | null;
}) {
  const t = useTranslations("imports.estimate");
  const locale = useLocale();
  const debounced = useDebounced(edits, DEBOUNCE_MS);
  const [estimate, setEstimate] = useState<ImportEstimate | null>(initial);
  const [settled, setSettled] = useState<ImportPlanEdits | null>(null);
  const loading = settled !== debounced;

  useEffect(() => {
    let current = true;
    estimateImport(projectName, runId, debounced)
      .then((found) => {
        if (current) setEstimate(found);
      })
      .catch(() => undefined)
      .finally(() => {
        if (current) setSettled(debounced);
      });
    return () => {
      current = false;
    };
  }, [projectName, runId, debounced]);

  if (!estimate) {
    return (
      <section className="rounded-xl border p-4 text-sm text-muted-foreground">
        <Loader2 className="inline h-4 w-4 animate-spin" aria-hidden /> {t("loading")}
      </section>
    );
  }

  const eur = (value: number | null) =>
    value === null
      ? "-"
      : value.toLocaleString(locale, { style: "currency", currency: "EUR" });
  const { total, quota, cluster, diff } = estimate;
  const projectUsed = total.requests.mem_mib + quota.other_imports_mib;
  const overQuota = quota.problems.length > 0;
  const overCluster =
    cluster !== null &&
    (total.requests.mem_mib > cluster.free.mem_mib || total.requests.cpu_m > cluster.free.cpu_m);

  return (
    <section
      aria-busy={loading}
      className={cn("space-y-3 rounded-xl border p-4", (overQuota || overCluster) && "border-destructive/50")}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Calculator className="h-4 w-4" aria-hidden />
        {t("title")}
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />}
      </h3>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-medium">{t("app")}</th>
              <th className="py-1 pr-3 font-medium">{t("requests")}</th>
              <th className="py-1 pr-3 font-medium">{t("limits")}</th>
              <th className="py-1 pr-3 font-medium">{t("volume")}</th>
              <th className="py-1 text-right font-medium">{t("monthly")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {estimate.apps.map((row) => (
              <tr key={row.app}>
                <td className="py-1 pr-3 font-mono text-xs">
                  {row.app}
                  {row.estimated && (
                    <span className="ml-1 text-muted-foreground" title={t("estimatedHelp")}>
                      ≈
                    </span>
                  )}
                </td>
                <td className="py-1 pr-3">
                  <Amount value={row.requests} />
                  {row.database_mem_mib && (
                    <span className="block text-xs text-muted-foreground">
                      {t("database", { mib: row.database_mem_mib })}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-3">
                  <Amount value={row.limits} />
                </td>
                <td className="py-1 pr-3 font-mono text-xs">{row.volume_gib ? `${row.volume_gib} Gio` : "-"}</td>
                <td className="py-1 text-right font-mono text-xs">{eur(row.monthly_eur)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t font-medium">
            <tr>
              <td className="py-1 pr-3">{t("total")}</td>
              <td className="py-1 pr-3">
                <Amount value={total.requests} />
              </td>
              <td className="py-1 pr-3">
                <Amount value={total.limits} />
              </td>
              <td className="py-1 pr-3 font-mono text-xs">{total.volume_gib} Gio</td>
              <td className="py-1 text-right font-mono text-xs">{eur(total.monthly_eur)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        {estimate.priced ? t("costBasis", { cloud: estimate.cloud }) : t("noPrices")}
        {estimate.rate_estimated && ` ${t("rateEstimated")}`}
      </p>

      <p className={cn("text-sm", overQuota && "font-medium text-destructive")}>
        {t("quota", { used: projectUsed, max: quota.project_max_mib, other: quota.other_imports_mib })}
      </p>
      {quota.problems.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-destructive">
          {quota.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <p className={cn("text-sm", overCluster && "font-medium text-destructive")}>
        {cluster
          ? t("cluster", { mem: cluster.free.mem_mib, cpu: cpu(cluster.free.cpu_m) })
          : t("clusterUnknown")}
      </p>
      {estimate.recommendations_gain_mib > 0 && (
        <p className="text-sm text-muted-foreground">
          {t("gain", { mib: estimate.recommendations_gain_mib })}
        </p>
      )}

      {diff && (
        <div className="space-y-1 border-t pt-3 text-sm">
          <p className="font-medium">{t("diffTitle")}</p>
          {diff.added.length + diff.removed.length + diff.changed.length === 0 ? (
            <p className="text-muted-foreground">{t("diffEmpty")}</p>
          ) : (
            <ul className="space-y-0.5">
              {diff.added.map((app) => (
                <li key={`+${app}`}>{t("added", { app })}</li>
              ))}
              {diff.removed.map((app) => (
                <li key={`-${app}`}>{t("removed", { app })}</li>
              ))}
              {diff.changed.map((c) => (
                <li key={`${c.app}-${c.field}`} className="font-mono text-xs">
                  {c.app} · {c.field}: {JSON.stringify(c.before)} {"->"} {JSON.stringify(c.after)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
