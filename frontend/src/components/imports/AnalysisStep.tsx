"use client";

import { RotateCw } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import type { StageState } from "@/lib/importPlan";
import type { ImportRun } from "@/types";
import { ImportFailurePanel } from "./ImportFailurePanel";
import { StageIcon } from "./StageIcon";

const STEPS = ["queued", "fetch", "analyze", "plan"] as const;

/** Where an analysis run stands: the workflow reports only claim and plan. */
function analysisStages(run: ImportRun): Record<(typeof STEPS)[number], StageState> {
  const failedAt = run.report?.stage;
  switch (run.status) {
    case "queued":
      return { queued: "active", fetch: "pending", analyze: "pending", plan: "pending" };
    case "claimed":
    case "running":
      return { queued: "done", fetch: "done", analyze: "active", plan: "pending" };
    case "succeeded":
      return { queued: "done", fetch: "done", analyze: "done", plan: "done" };
    default:
      if (failedAt === "dispatch" || !failedAt || failedAt === "claim") {
        return { queued: "failed", fetch: "pending", analyze: "pending", plan: "pending" };
      }
      if (failedAt === "fetch") {
        return { queued: "done", fetch: "failed", analyze: "pending", plan: "pending" };
      }
      return { queued: "done", fetch: "done", analyze: "failed", plan: "pending" };
  }
}

export function AnalysisStep({
  run,
  repo,
  onRetry,
  onRestart,
}: {
  run: ImportRun;
  repo: string | null;
  onRetry: () => void;
  onRestart: () => void;
}) {
  const t = useTranslations("imports.analysis");
  const stages = analysisStages(run);
  const failed = run.status === "failed" || run.status === "cancelled";
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {t("intro", { repo: repo ?? "", sha: run.sha.slice(0, 12) })}
      </p>
      <ol className="space-y-2" aria-live="polite">
        {STEPS.map((step) => (
          <li key={step} className="flex items-center gap-2 text-sm">
            <StageIcon state={stages[step]} />
            <span className={stages[step] === "pending" ? "text-muted-foreground" : undefined}>
              {t(`stage.${step}`)}
            </span>
          </li>
        ))}
      </ol>
      {failed && run.failure && (
        <ImportFailurePanel
          runId={run.id}
          kind="analyze"
          failure={run.failure}
          onRebuild={onRetry}
          onAnalyze={onRetry}
          extra={
            <Button size="sm" variant="outline" onClick={onRestart}>
              {t("otherRepo")}
            </Button>
          }
        />
      )}
      {failed && !run.failure && (
        <div role="alert" className="space-y-2 rounded-lg border border-destructive bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">{t("failed")}</p>
          {run.report?.error && <pre className="whitespace-pre-wrap text-xs">{run.report.error}</pre>}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={onRetry}>
              <RotateCw aria-hidden />
              {t("retry")}
            </Button>
            <Button size="sm" variant="outline" onClick={onRestart}>
              {t("otherRepo")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
