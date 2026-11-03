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
import { requestSecurityRestore, type SecurityBackup } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./labels";

function utc(iso: string): Date {
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
}

function localInput(date: Date): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

/** Pick how far to restore a backup, review the commit, then confirm it. */
export function RestoreDialog({
  project,
  app,
  backup,
  onClose,
  onRestored,
}: {
  project: string;
  app: string;
  backup: SecurityBackup;
  onClose: () => void;
  onRestored: () => void;
}) {
  const t = useTranslations("security");
  const locale = useLocale();
  const stoppedAt = utc(backup.stopped_at!);
  const [mode, setMode] = useState<"backup" | "time">("backup");
  const [time, setTime] = useState(localInput(new Date()));
  const [preview, setPreview] = useState<{ message: string; diff?: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  async function run(dryRun: boolean) {
    setBusy(true);
    try {
      const result = await requestSecurityRestore(
        {
          project,
          app,
          backup: backup.name,
          target_time: mode === "time" ? new Date(time).toISOString() : undefined,
        },
        dryRun,
      );
      if (dryRun) {
        setPreview(result);
      } else {
        toast.success(t("restore.committed"));
        onRestored();
        onClose();
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("restore.title", { database: backup.database })}</DialogTitle>
          <DialogDescription>{t("restore.description")}</DialogDescription>
        </DialogHeader>

        {preview ? (
          <>
            <p className="text-sm">{preview.message}</p>
            <pre className="max-h-[40vh] overflow-auto rounded-md bg-muted p-3 font-mono text-[11px]">
              {preview.diff || t("common.noChange")}
            </pre>
          </>
        ) : (
          <div className="space-y-3 text-sm">
            <label className="flex items-start gap-2">
              <input
                type="radio"
                className="mt-1"
                checked={mode === "backup"}
                onChange={() => setMode("backup")}
              />
              <span>
                {t("restore.backupState")}
                <span className="block text-xs text-muted-foreground">
                  {stoppedAt.toLocaleString(locale)}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                className="mt-1"
                checked={mode === "time"}
                onChange={() => setMode("time")}
              />
              <span className="flex-1 space-y-1.5">
                {t("restore.pointInTime")}
                <Input
                  type="datetime-local"
                  value={time}
                  min={localInput(stoppedAt)}
                  max={localInput(new Date())}
                  disabled={mode !== "time"}
                  onChange={(e) => setTime(e.target.value)}
                />
                <span className="block text-xs text-muted-foreground">
                  {t("restore.pointHint")}
                </span>
              </span>
            </label>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          {preview ? (
            <Button variant="destructive" onClick={() => run(false)} disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("restore.confirm")}
            </Button>
          ) : (
            <Button onClick={() => run(true)} disabled={busy || (mode === "time" && !time)}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("restore.preview")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
