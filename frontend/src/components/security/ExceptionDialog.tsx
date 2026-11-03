"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createSecurityException } from "@/lib/api";
import type { SecurityException, SecurityFinding } from "@/types";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage, JUSTIFICATIONS } from "./labels";

type Status = SecurityException["status"];

function statusesFor(finding: SecurityFinding): Status[] {
  if (finding.category === "leaks") return ["false_positive", "revoked"];
  if (finding.category === "dependencies")
    return ["not_affected", "false_positive", "accepted_risk"];
  return ["false_positive", "accepted_risk"];
}

function maxDays(status: Status): number {
  return status === "accepted_risk" ? 90 : 365;
}

function isoIn(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export function ExceptionDialog({
  finding,
  project,
  onClose,
  onDone,
}: {
  finding: SecurityFinding | null;
  project: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("security");
  const statuses = finding ? statusesFor(finding) : [];
  const [status, setStatus] = useState<Status>(statuses[0] ?? "false_positive");
  const [justification, setJustification] = useState(
    "vulnerable_code_not_in_execute_path",
  );
  const [statement, setStatement] = useState("");
  const [expires, setExpires] = useState("");
  const [saving, setSaving] = useState(false);

  if (!finding) return null;
  const limit = maxDays(status);
  const needsApproval =
    status === "accepted_risk" || finding.tier === "core";

  async function submit() {
    if (!finding) return;
    setSaving(true);
    try {
      await createSecurityException(finding.fingerprint, {
        project,
        status,
        justification: status === "not_affected" ? justification : null,
        statement,
        expires_on: expires || null,
      });
      toast.success(
        t(needsApproval ? "exception.saved" : "exception.ignored"),
      );
      onDone();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("exception.title")}</DialogTitle>
          <DialogDescription>{finding.title}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t("exception.reason")}</Label>
            <Select value={status} onValueChange={(v) => setStatus(v as Status)}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v) => t(`exceptionStatus.${String(v)}`)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {statuses.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(`exceptionStatus.${s}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {t(`exception.hints.${status}`)}
            </p>
          </div>

          {status === "not_affected" && (
            <div className="space-y-1.5">
              <Label>{t("exception.why")}</Label>
              <Select
                value={justification}
                onValueChange={(v) => setJustification(String(v))}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>{(v) => t(`justifications.${String(v)}`)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {JUSTIFICATIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`justifications.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="exception-statement">{t("exception.explanation")}</Label>
            <textarea
              id="exception-statement"
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              rows={3}
              placeholder={t("exception.explanationPlaceholder")}
              className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exception-expires">{t("exception.expiresOn")}</Label>
            <Input
              id="exception-expires"
              type="date"
              value={expires}
              min={isoIn(1)}
              max={isoIn(limit)}
              onChange={(e) => setExpires(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t("exception.expiryHint", { date: isoIn(limit) })}
            </p>
          </div>

          {needsApproval && (
            <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
              {t("exception.approvalNeeded")}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={saving || statement.trim().length < 10}>
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {t("exception.ignore")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
