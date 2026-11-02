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
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useRef, useState } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  level: 1 | 2;
  title: string;
  description: string;
  consequences?: string[];
  expectedName?: string;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => Promise<void>;
}

function messageOf(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const detail = JSON.parse(raw)?.detail;
    if (typeof detail === "string") return detail;
  } catch {
    // not JSON: the raw message is the best we have
  }
  return raw;
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  const { open, onOpenChange } = props;
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <ConfirmBody {...props} onBusyChange={setBusy} />
    </Dialog>
  );
}

function ConfirmBody({
  onBusyChange,
  onOpenChange,
  level,
  title,
  description,
  consequences,
  expectedName,
  confirmLabel,
  destructive,
  onConfirm,
}: ConfirmDialogProps & { onBusyChange: (busy: boolean) => void }) {
  const t = useTranslations("confirm");
  const inputId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsName = level === 2 && expectedName !== undefined;
  const matches = !needsName || typed === expectedName;

  const confirm = async () => {
    if (!matches || pending) return;
    setPending(true);
    onBusyChange(true);
    setError(null);
    try {
      await onConfirm();
      onBusyChange(false);
      onOpenChange(false);
    } catch (err) {
      setError(messageOf(err));
      setPending(false);
      onBusyChange(false);
    }
  };

  return (
    <DialogContent
      className="sm:max-w-md"
      showCloseButton={!pending}
      initialFocus={needsName ? inputRef : cancelRef}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>

      {level === 2 && consequences && consequences.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">{t("consequencesLabel")}</p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {consequences.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {needsName && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void confirm();
          }}
        >
          <Label htmlFor={inputId}>
            {t("typeToConfirm", { name: expectedName })}
          </Label>
          <Input
            id={inputId}
            ref={inputRef}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder={expectedName}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={pending}
            aria-describedby={error ? errorId : undefined}
          />
        </form>
      )}

      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {t("failed", { message: error })}
        </p>
      )}

      <DialogFooter>
        <Button
          ref={cancelRef}
          variant="outline"
          onClick={() => onOpenChange(false)}
          disabled={pending}
        >
          {t("cancel")}
        </Button>
        <Button
          variant={destructive ? "destructive" : "default"}
          onClick={() => void confirm()}
          disabled={!matches || pending}
        >
          {pending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("pending")}
            </>
          ) : (
            confirmLabel
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
