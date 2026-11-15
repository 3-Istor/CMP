"use client";

import { RotateCw, ShieldAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { ImportSourceView } from "@/types";

/** A push waiting for an admin, why, and the rebuild that settles it. */
export function RebuildPendingNotice({
  source,
  canManage,
  onRebuild,
}: {
  source: ImportSourceView;
  canManage: boolean;
  onRebuild: () => void;
}) {
  const t = useTranslations("imports.list");
  const pending = source.rebuild_pending;
  if (!pending) return null;
  return (
    <div
      role="status"
      className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
    >
      <div className="flex flex-wrap items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400" aria-hidden />
        <span className="flex-1">
          {t("pending", {
            repo: source.repo_full_name,
            reason: t(`pendingReason.${pending.reason}`, { user: pending.user }),
          })}
        </span>
        {canManage && (
          <Button size="sm" variant="outline" onClick={onRebuild}>
            <RotateCw aria-hidden />
            {t("confirmRebuild")}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {t("pendingHelp")}
        {pending.reason === "repo_access" && (
          <>
            {" "}
            <Link href="/account" className="text-primary hover:underline">
              {t("linkGithub")}
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
