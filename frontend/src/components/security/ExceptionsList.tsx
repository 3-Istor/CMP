"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { approveSecurityException, revokeSecurityException } from "@/lib/api";
import type { SecurityException } from "@/types";
import { Loader2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { errorMessage } from "./labels";
import { useFindingText } from "./findingText";

function date(iso: string, locale: string): string {
  return new Date(
    iso.length === 10 ? `${iso}T00:00:00Z` : `${iso}Z`,
  ).toLocaleDateString(locale);
}

const FILTERS = ["pending", "active", "expired"] as const;
type Filter = (typeof FILTERS)[number];

// Expired wins over pending: an expired request can no longer be approved.
const stateOf = (e: SecurityException): Filter =>
  e.expired ? "expired" : e.pending_approval ? "pending" : "active";

export function ExceptionsList({
  exceptions,
  canAdmin,
  canReview,
  username,
  onChanged,
}: {
  exceptions: SecurityException[];
  canAdmin: boolean;
  /** Platform admin or auditor: may also accept a critical finding. */
  canReview: boolean;
  username: string;
  onChanged: () => void;
}) {
  const t = useTranslations("security");
  const locale = useLocale();
  const text = useFindingText();
  const [busy, setBusy] = useState<number | null>(null);
  const count = (f: Filter) =>
    exceptions.filter((e) => stateOf(e) === f).length;
  const [picked, setPicked] = useState<Filter | null>(null);
  const filter = picked ?? FILTERS.find((f) => count(f) > 0) ?? "pending";
  const shown = exceptions.filter((e) => stateOf(e) === filter);

  async function run(id: number, action: () => Promise<unknown>, done: string) {
    setBusy(id);
    try {
      await action();
      toast.success(done);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (exceptions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">{t("exceptions.empty")}</p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setPicked(f)}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
              filter === f
                ? "bg-primary/10 font-medium text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            {t(`exceptions.filters.${f}`)}
            <Badge variant="outline" className="h-5 px-1.5 text-[11px]">
              {count(f)}
            </Badge>
          </button>
        ))}
      </div>
      {shown.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("exceptions.empty")}</p>
      )}
      <ul className="space-y-2">
        {shown.map((e) => (
          <li key={e.id} className="rounded-lg border bg-card p-3 text-sm">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="font-medium">
                  {text(e, "title") || e.rule}
                  <span className="font-normal text-muted-foreground">
                    {" "}
                    · {e.app ?? t("common.project")}
                  </span>
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="outline">
                    {t(`exceptionStatus.${e.status}`)}
                  </Badge>
                  {e.justification && (
                    <Badge variant="outline">
                      {t(`justifications.${e.justification}`)}
                    </Badge>
                  )}
                  {e.expired && (
                    <Badge variant="secondary">{t("exceptions.expired")}</Badge>
                  )}
                  {!e.expired && e.pending_approval && (
                    <Badge
                      variant="outline"
                      className="border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                    >
                      {t(
                        e.critical
                          ? "exceptions.awaitingCore"
                          : "exceptions.awaitingProject",
                      )}
                    </Badge>
                  )}
                </div>
                <p>{e.statement}</p>
                <p className="text-xs text-muted-foreground">
                  {t("exceptions.byOn", {
                    author: e.author,
                    date: date(e.created_at, locale),
                  })}
                  {e.approved_by &&
                    `, ${t("exceptions.approvedBy", { approver: e.approved_by })}`}{" "}
                  ·{" "}
                  {t("exceptions.expiresOn", {
                    date: date(e.expires_on, locale),
                  })}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {!e.expired &&
                  e.pending_approval &&
                  e.author !== username &&
                  (canReview || (canAdmin && !e.critical)) && (
                    <Button
                      size="sm"
                      disabled={busy === e.id}
                      onClick={() =>
                        run(
                          e.id,
                          () => approveSecurityException(e.id),
                          t("exceptions.approved"),
                        )
                      }
                    >
                      {busy === e.id && (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      )}
                      {t("exceptions.approve")}
                    </Button>
                  )}
                {!e.expired && (e.author === username || canAdmin || canReview) && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === e.id}
                    onClick={() =>
                      run(
                        e.id,
                        () => revokeSecurityException(e.id),
                        t("exceptions.reactivated"),
                      )
                    }
                  >
                    {t("exceptions.reactivate")}
                  </Button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
