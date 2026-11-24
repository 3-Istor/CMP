"use client";

import { imageTag } from "@/components/release/format";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { rollbackTarget, type SummaryDeploy } from "@/lib/appHealth";
import { startPromotion } from "@/lib/api";
import { useProdReleases } from "@/lib/appSummary";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

/** Roll back prod through the promotion API, the same call as the Deployments tab. */
export function useRollback(appId: number, deploys: SummaryDeploy[], allowed: boolean) {
  const t = useTranslations("appPage.deployments");
  const releases = useProdReleases(appId, allowed);
  const candidates = (releases.data ?? []).map((e) => ({
    version: imageTag(e.image),
    promotionId: e.rollback_promotion_id,
  }));
  const target = allowed ? rollbackTarget(deploys, candidates) : null;
  const promotionFor = (version: string) =>
    allowed ? (candidates.find((c) => c.version === version && c.promotionId != null)?.promotionId ?? null) : null;
  const [pending, setPending] = useState<{ version: string; promotionId: number } | null>(null);

  const confirm = async () => {
    // pending is already null while the dialog closes: without an id this would be a normal promotion.
    if (!pending) return;
    await startPromotion(appId, { rollbackTo: pending.promotionId });
    toast.success(t("rollbackStarted", { version: pending.version }));
    releases.retry();
  };

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(open) => !open && setPending(null)}
      level={1}
      title={t("rollbackTitle", { version: pending?.version ?? "" })}
      description={t("rollbackDescription")}
      confirmLabel={t("rollback")}
      onConfirm={confirm}
    />
  );
  return { target, promotionFor, ask: setPending, dialog };
}
