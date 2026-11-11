"use client";

import { Check, Loader2, Repeat, UserMinus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/components/activity/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { decideAccessReview } from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import type { AccessReviewItem, ReviewDecision } from "@/types/accessReview";

const PROJECT_ROLES = ["admin", "member", "guest"];

const SELECT =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const OUTCOME_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  unchanged: "secondary",
  applied: "default",
  applying: "outline",
  failed: "destructive",
  terraform_task: "outline",
};

/** "Keep", "Remove", "Role: guest", as a short label. */
export function DecisionBadge({ item }: { item: AccessReviewItem }) {
  const t = useTranslations("accessReviews");
  if (!item.decision) {
    return <Badge variant="outline">{t("decision.pending")}</Badge>;
  }
  const label =
    item.decision === "change_role"
      ? t("decision.changedTo", { role: item.new_role ?? "" })
      : t(`decision.${item.decision}`);
  return (
    <Badge variant={OUTCOME_VARIANT[item.outcome ?? ""] ?? "secondary"}>
      {label}
    </Badge>
  );
}

export function LastLogin({ value }: { value: string | null }) {
  const t = useTranslations("accessReviews");
  const format = useFormat();
  if (!value) {
    return <span className="text-muted-foreground">{t("lastLogin.none")}</span>;
  }
  return (
    <span title={format.dateTime(value)}>{format.relative(value)}</span>
  );
}

export function DecisionControls({
  item,
  me,
  onDone,
}: {
  item: AccessReviewItem;
  me: string;
  onDone: () => void;
}) {
  const t = useTranslations("accessReviews");
  const id = useId();
  const [mode, setMode] = useState<"idle" | "remove" | "role">("idle");
  const [role, setRole] = useState(
    PROJECT_ROLES.find((r) => r !== item.role) ?? "member",
  );
  const [busy, setBusy] = useState(false);

  if (item.decision) return <DecisionBadge item={item} />;
  if (item.username === me) {
    return (
      <span className="text-xs text-muted-foreground">{t("decision.self")}</span>
    );
  }
  if (!item.reviewers.includes(me)) {
    return <DecisionBadge item={item} />;
  }

  const send = async (decision: ReviewDecision) => {
    setBusy(true);
    try {
      await decideAccessReview(item.id, {
        decision,
        role: decision === "change_role" ? role : undefined,
      });
      toast.success(t(`toasts.${decision}`, { user: item.username }));
      setMode("idle");
      onDone();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (mode === "remove") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs">
          {item.scope === "platform"
            ? t("decision.confirmPlatformRemove", { user: item.username })
            : t("decision.confirmRemove", { user: item.username })}
        </span>
        <Button
          size="sm"
          variant="destructive"
          disabled={busy}
          onClick={() => send("remove")}
        >
          {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
          {t("actions.confirmRemove")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>
          {t("actions.cancel")}
        </Button>
      </div>
    );
  }

  if (mode === "role") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${id}-role`} className="sr-only">
          {t("decision.newRole")}
        </label>
        <select
          id={`${id}-role`}
          className={SELECT}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          {PROJECT_ROLES.filter((r) => r !== item.role).map((r) => (
            <option key={r} value={r}>
              {t(`roles.${r}`)}
            </option>
          ))}
        </select>
        <Button size="sm" disabled={busy} onClick={() => send("change_role")}>
          {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
          {t("actions.confirmRole")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>
          {t("actions.cancel")}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() => send("keep")}
        aria-label={t("actions.keepUser", { user: item.username })}
      >
        <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
        {t("actions.keep")}
      </Button>
      {item.scope === "project" && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setMode("role")}
          aria-label={t("actions.roleUser", { user: item.username })}
        >
          <Repeat className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("actions.role")}
        </Button>
      )}
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() => setMode("remove")}
        aria-label={t("actions.removeUser", { user: item.username })}
      >
        <UserMinus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
        {t("actions.remove")}
      </Button>
    </div>
  );
}

export function ReviewTable({
  items,
  me,
  onDone,
  showSubject = false,
}: {
  items: AccessReviewItem[];
  me: string;
  onDone: () => void;
  showSubject?: boolean;
}) {
  const t = useTranslations("accessReviews");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            {showSubject && (
              <th scope="col" className="py-2 pr-3 font-medium">
                {t("table.subject")}
              </th>
            )}
            <th scope="col" className="py-2 pr-3 font-medium">
              {t("table.member")}
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              {t("table.role")}
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              {t("table.lastLogin")}
            </th>
            <th scope="col" className="py-2 font-medium">
              {t("table.decision")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => (
            <tr key={item.id} className="align-top">
              {showSubject && <td className="py-2 pr-3">{item.subject}</td>}
              <td className="py-2 pr-3 font-medium">{item.username}</td>
              <td className="py-2 pr-3">
                {item.scope === "project" ? t(`roles.${item.role}`) : "-"}
              </td>
              <td className="whitespace-nowrap py-2 pr-3">
                <LastLogin value={item.last_login_at} />
              </td>
              <td className="py-2">
                <DecisionControls item={item} me={me} onDone={onDone} />
                {item.decided_by && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("decision.by", { user: item.decided_by })}
                  </p>
                )}
                {item.outcome === "failed" && item.outcome_detail && (
                  <p className="mt-1 text-xs text-destructive">
                    {item.outcome_detail}
                  </p>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
