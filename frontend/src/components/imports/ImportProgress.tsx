"use client";

import { FileText, Network } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { blockingCount } from "@/lib/importFailure";
import { serviceStages, STAGES, type Stage, type StageState } from "@/lib/importPlan";
import type { ImportRunDetail, ImportServiceView, ImportSourceView } from "@/types";
import { ImportFailurePanel } from "./ImportFailurePanel";
import { RebuildPendingNotice } from "./RebuildPendingNotice";
import { StageIcon } from "./StageIcon";

const TH = "px-3 py-2 text-left text-xs font-medium text-muted-foreground";
const TD = "px-3 py-2 align-top";

function failedStage(stages: Record<Stage, StageState>): Stage | null {
  return STAGES.find((s) => stages[s] === "failed") ?? null;
}

export function ImportProgress({
  projectName,
  run,
  source,
  canManage,
  onRebuild,
  onReanalyze,
}: {
  projectName: string;
  run: ImportRunDetail;
  source: ImportSourceView | null;
  canManage: boolean;
  onRebuild: () => void;
  onReanalyze: () => void;
}) {
  const t = useTranslations("imports.progress");
  const tStage = useTranslations("imports.stage");
  const base = `/projects/${encodeURIComponent(projectName)}`;
  const finished = run.status === "succeeded" || run.status === "failed";
  const services = [...run.services].sort(
    (a, b) => Number(a.kind === "backing") - Number(b.kind === "backing") || a.app.localeCompare(b.app),
  );

  const actions = (service: ImportServiceView, failed: Stage | null) => {
    if (!failed || !finished) return null;
    const logs = service.deployment_id ? `${base}/apps/${service.deployment_id}/logs` : null;
    if (failed === "build" || failed === "scan" || failed === "publish") {
      const blocking = blockingCount(run.failure, service.app);
      if (blocking > 0) return <p className="text-xs text-destructive">{t("blocking", { count: blocking })}</p>;
      return run.failure?.apps.includes(service.app) ? (
        <p className="text-xs text-destructive">{t("concerned")}</p>
      ) : null;
    }
    return (
      <div className="flex flex-wrap gap-1">
        {logs && (
          <Link href={logs} className={buttonVariants({ size: "sm", variant: "outline" })}>
            <FileText aria-hidden />
            {t("logs", { app: service.app })}
          </Link>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {t(`status.${run.status}`, { sha: run.sha.slice(0, 12) })}
      </p>
      {source?.rebuild_pending && (
        <RebuildPendingNotice source={source} canManage={canManage} onRebuild={onRebuild} />
      )}
      {run.failure ? (
        <ImportFailurePanel
          runId={run.id}
          kind={run.kind}
          failure={run.failure}
          onRebuild={onRebuild}
          onAnalyze={onReanalyze}
        />
      ) : (
        run.report?.error && (
          <div role="alert" className="rounded-lg border border-destructive bg-destructive/5 p-3 text-sm">
            <p className="font-medium text-destructive">
              {t("failedAt", { stage: run.report.stage ?? "?" })}
            </p>
            <pre className="mt-1 whitespace-pre-wrap text-xs">{run.report.error}</pre>
          </div>
        )
      )}
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-sm">
          <caption className="sr-only">{t("caption")}</caption>
          <thead className="border-b bg-muted/40">
            <tr>
              <th scope="col" className={TH}>{t("app")}</th>
              {STAGES.map((stage) => (
                <th key={stage} scope="col" className={`${TH} text-center`}>
                  {tStage(stage)}
                </th>
              ))}
              <th scope="col" className={TH}>{t("next")}</th>
            </tr>
          </thead>
          <tbody>
            {services.map((service) => {
              const stages = serviceStages(run, service);
              const failed = failedStage(stages);
              return (
                <tr key={service.app} className="border-b last:border-0">
                  <th scope="row" className={`${TD} text-left font-medium`}>
                    {service.app}
                    {service.message && (
                      <p className="text-xs font-normal text-destructive">{service.message}</p>
                    )}
                    {service.warnings.map((w) => (
                      <p key={w} className="text-xs font-normal text-muted-foreground">
                        {w}
                      </p>
                    ))}
                  </th>
                  {STAGES.map((stage) => (
                    <td key={stage} className={`${TD} text-center`}>
                      <StageIcon state={stages[stage]} />
                    </td>
                  ))}
                  <td className={TD}>{actions(service, failed)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {run.status === "succeeded" && (
        <Link href={`${base}/topology`} className={buttonVariants({ size: "sm" })}>
          <Network aria-hidden />
          {t("seeTopology")}
        </Link>
      )}
    </div>
  );
}
