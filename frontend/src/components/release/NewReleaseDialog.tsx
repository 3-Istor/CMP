"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, createReleaseTag, getReleaseChoices } from "@/lib/api";
import type { ReleaseChoices } from "@/types";
import { Loader2, Tag } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./format";

export function NewReleaseButton({ deploymentId }: { deploymentId: number }) {
  const t = useTranslations("release");
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Tag className="h-4 w-4" aria-hidden />
        {t("new.button")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <NewReleaseBody deploymentId={deploymentId} onClose={() => setOpen(false)} />
        </DialogContent>
      </Dialog>
    </>
  );
}

// Mounted only while the dialog is open, so the choices are read fresh each time.
function NewReleaseBody({
  deploymentId,
  onClose,
}: {
  deploymentId: number;
  onClose: () => void;
}) {
  const t = useTranslations("release");
  const [choices, setChoices] = useState<ReleaseChoices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);

  // Bumped to read the choices again after a conflict.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let stale = false;
    getReleaseChoices(deploymentId)
      .then((data) => {
        if (stale) return;
        setChoices(data);
        setSelected(data.choices[0]?.tag ?? "");
        setError(null);
      })
      .catch((e) => {
        if (!stale) setError(errorMessage(e));
      });
    return () => {
      stale = true;
    };
  }, [deploymentId, attempt]);

  const create = async () => {
    if (!choices || !selected) return;
    setBusy(true);
    try {
      const created = await createReleaseTag(deploymentId, selected, choices.commitSha);
      toast.success(t("new.created", { tag: created.tag }), {
        action: {
          label: t("new.viewRun"),
          onClick: () => window.open(created.runsUrl, "_blank", "noopener,noreferrer"),
        },
      });
      onClose();
    } catch (e) {
      toast.error(errorMessage(e));
      // A stale tag or a moved main: show what is offered now.
      if (e instanceof ApiError && e.status === 409) {
        setChoices(null);
        setAttempt((n) => n + 1);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("new.title")}</DialogTitle>
      </DialogHeader>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {t("new.loadFailed", { error })}
        </p>
      ) : !choices ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <>
          <p className="truncate text-sm">
            <code className="mr-2 font-mono text-xs">{choices.commitSha.slice(0, 7)}</code>
            {choices.commitMessage}
          </p>
          <div role="radiogroup" aria-label={t("new.title")} className="space-y-2">
            {choices.choices.map((choice) => (
              <label key={choice.tag} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="release-choice"
                  checked={selected === choice.tag}
                  onChange={() => setSelected(choice.tag)}
                  disabled={busy}
                />
                <span className="font-mono">{choice.tag}</span>
                <span className="text-xs text-muted-foreground">
                  {t(`new.kind.${choice.kind}`)}
                </span>
              </label>
            ))}
          </div>
        </>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={busy}>
          {t("dialog.cancel")}
        </Button>
        <Button onClick={() => void create()} disabled={!choices || !selected || busy}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          {t("new.create")}
        </Button>
      </DialogFooter>
    </>
  );
}
