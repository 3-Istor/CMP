"use client";

import { DatabaseBackup, EyeOff, Lightbulb, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { OverviewCard } from "@/components/app-overview/OverviewCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { dismissRecommendation, getManagedServices, restoreRecommendation } from "@/lib/api";
import { importRefusal } from "@/lib/importRefusal";
import { useCardData } from "@/lib/useCardData";
import type { ManagedServicesView, PlanRecommendation } from "@/types";

/**
 * One recommendation of the analyzer (CNP-305). Advice, never an obligation:
 * an engine may have been chosen for good reasons, hence "ignore".
 */
export function RecommendationItem({
  item,
  app,
  onDismiss,
  onRestore,
  dismissed = false,
}: {
  item: PlanRecommendation;
  app: string;
  onDismiss?: () => Promise<void>;
  onRestore?: () => Promise<void>;
  dismissed?: boolean;
}) {
  const t = useTranslations("imports.recommendations");
  const [busy, setBusy] = useState(false);
  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (err) {
      toast.error(t("failed", { message: importRefusal(err).message }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="space-y-1.5 py-2 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-medium">{app}</span>
        <span className="text-sm">{t(`target.${item.target}`)}</span>
        <Badge variant={item.level === "advised" ? "default" : "secondary"}>{t(`level.${item.level}`)}</Badge>
        <Badge variant="outline">{t(`effort.${item.effort}`)}</Badge>
        {item.gain_mem_mib !== null && (
          <Badge variant="outline">{t("gain", { mib: item.gain_mem_mib })}</Badge>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{item.reason}</p>
      {item.evidence.length > 0 && (
        <p className="font-mono text-xs text-muted-foreground">
          {item.evidence.map((e) => `${e.file}:${e.line}`).join(", ")}
        </p>
      )}
      {dismissed
        ? onRestore && (
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => void act(onRestore)}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {t("restore")}
            </Button>
          )
        : onDismiss && (
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => void act(onDismiss)}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <EyeOff aria-hidden />}
              {t("dismiss")}
            </Button>
          )}
    </li>
  );
}

/** What a backing container lacks: no backup, and the managed service that has one (CNP-302). */
export function NotBackedUp({ backing }: { backing: ManagedServicesView["backing"][number] }) {
  const t = useTranslations("imports.recommendations");
  return (
    <div role="note" className="flex items-start gap-2 rounded-lg border border-amber-600/40 p-3 text-sm">
      <DatabaseBackup className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
      <div className="space-y-0.5">
        <p className="font-medium">{t("notBackedUp")}</p>
        <p className="text-muted-foreground">
          {backing.managed_alternative
            ? t("alternative", { target: t(`target.${backing.managed_alternative}`) })
            : t("alternativeGeneric")}
        </p>
      </div>
    </div>
  );
}

/** The app page panel of an imported app: its recommendations and backup state. */
export function ManagedServicesCard({
  projectName,
  app,
  canManage,
}: {
  projectName: string;
  app: string;
  canManage: boolean;
}) {
  const t = useTranslations("imports.recommendations");
  const data = useCardData(`managed-${projectName}-${app}`, () => getManagedServices(projectName, app));
  const view = data.data;
  const active = view?.recommendations.filter((r) => !r.dismissed) ?? [];
  const dismissed = view?.recommendations.filter((r) => r.dismissed) ?? [];
  const backing = view?.backing[0];

  return (
    <OverviewCard
      title={
        <span className="flex items-center gap-2">
          <Lightbulb className="h-4 w-4" aria-hidden />
          {t("title")}
        </span>
      }
      state={{
        loading: data.loading,
        error: data.error,
        forbidden: data.forbidden,
        hasData: view !== null,
        empty: view !== null && !backing && view.recommendations.length === 0,
        retry: data.retry,
      }}
      emptyText={t("none")}
      skeletonClassName="h-20 w-full"
    >
      <div className="space-y-3">
        {backing && !backing.backed_up && <NotBackedUp backing={backing} />}
        {active.length > 0 && (
          <ul className="divide-y">
            {active.map((r) => (
              <RecommendationItem
                key={r.id}
                item={r}
                app={r.app}
                onDismiss={
                  canManage
                    ? async () => {
                        await dismissRecommendation(projectName, r.id);
                        data.retry();
                      }
                    : undefined
                }
              />
            ))}
          </ul>
        )}
        {dismissed.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              {t("dismissedCount", { count: dismissed.length })}
            </summary>
            <ul className="mt-2 divide-y">
              {dismissed.map((r) => (
                <RecommendationItem
                  key={r.id}
                  item={r}
                  app={r.app}
                  dismissed
                  onRestore={
                    canManage
                      ? async () => {
                          await restoreRecommendation(projectName, r.id);
                          data.retry();
                        }
                      : undefined
                  }
                />
              ))}
            </ul>
          </details>
        )}
      </div>
    </OverviewCard>
  );
}
