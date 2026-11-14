"use client";

import { LifeBuoy, RotateCw, ScanSearch, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { failurePlan } from "@/lib/importFailure";
import type { ImportFailure } from "@/types";

const TH = "px-2 py-1 text-left text-xs font-medium text-muted-foreground";
const TD = "px-2 py-1 align-top";

/** Why a run failed, in the user's words, and the actions that can fix it. */
export function ImportFailurePanel({
  runId,
  kind,
  failure,
  onRebuild,
  onAnalyze,
  extra,
}: {
  runId: string;
  kind: "analyze" | "build";
  failure: ImportFailure;
  onRebuild: () => void;
  onAnalyze: () => void;
  extra?: React.ReactNode;
}) {
  const t = useTranslations("imports.failure");
  const plan = failurePlan(failure, kind);

  return (
    <div role="alert" className="space-y-3 rounded-lg border border-destructive bg-destructive/5 p-4 text-sm">
      <div className="space-y-1">
        <p className="font-medium text-destructive">{t(`cause.${failure.cause}.title`)}</p>
        <p>{t(`cause.${failure.cause}.explain`)}</p>
        {failure.apps.length > 0 && (
          <p className="text-muted-foreground">{t("apps", { apps: failure.apps.join(", ") })}</p>
        )}
      </div>
      {failure.upgrades.length > 0 && (
        <div className="overflow-x-auto rounded-md border bg-background">
          <table className="w-full text-xs">
            <caption className="sr-only">{t("upgradesCaption")}</caption>
            <thead className="border-b bg-muted/40">
              <tr>
                <th scope="col" className={TH}>{t("package")}</th>
                <th scope="col" className={TH}>{t("installed")}</th>
                <th scope="col" className={TH}>{t("fixed")}</th>
                <th scope="col" className={TH}>{t("appsColumn")}</th>
                <th scope="col" className={TH}>{t("ids")}</th>
              </tr>
            </thead>
            <tbody>
              {failure.upgrades.map((line) => (
                <tr key={`${line.package}@${line.installed}`} className="border-b last:border-0">
                  <th scope="row" className={`${TD} text-left font-mono font-normal`}>{line.package}</th>
                  <td className={`${TD} font-mono`}>{line.installed}</td>
                  <td className={`${TD} font-mono font-medium`}>{line.fixed}</td>
                  <td className={TD}>{line.apps.join(", ")}</td>
                  <td className={`${TD} font-mono`}>{line.ids.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {plan.hints.includes("fix_in_repo") && (
        <p className="flex items-start gap-2">
          <Wrench className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            {t(failure.upgrades.length > 0 ? "fixUpgrades" : "fixRepo")}{" "}
            {plan.hints.includes("push_rebuilds") ? t("pushRebuilds") : t("thenRebuild")}
          </span>
        </p>
      )}
      {plan.hints.includes("contact_platform") && (
        <p className="flex items-start gap-2">
          <LifeBuoy className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{t("contactPlatform", { runId })}</span>
        </p>
      )}
      {failure.message && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">{t("message")}</summary>
          <pre className="mt-1 whitespace-pre-wrap text-xs">{failure.message}</pre>
        </details>
      )}
      {failure.log_excerpt && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">{t("log")}</summary>
          <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-muted/60 p-2 text-xs">
            {failure.log_excerpt}
          </pre>
        </details>
      )}
      <div className="flex flex-wrap gap-2">
        {plan.buttons.map((button) =>
          button === "rebuild" ? (
            <Button key={button} size="sm" onClick={onRebuild}>
              <RotateCw aria-hidden />
              {t(plan.hints.includes("fix_in_repo") ? "rebuildAfterFix" : "rebuild")}
            </Button>
          ) : (
            <Button key={button} size="sm" variant={plan.buttons[0] === button ? "default" : "outline"} onClick={onAnalyze}>
              <ScanSearch aria-hidden />
              {t(kind === "analyze" ? "analyzeAgain" : "reanalyze")}
            </Button>
          ),
        )}
        {extra}
      </div>
    </div>
  );
}
