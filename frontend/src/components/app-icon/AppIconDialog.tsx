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
import {
  ApiError,
  resetAppIcon,
  setBuiltinAppIcon,
  uploadAppIcon,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Deployment } from "@/types";
import { Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppIcon } from "./AppIcon";
import {
  ACCEPTED_TYPES,
  BUILTIN_ICONS,
  BUILTIN_ICON_NAMES,
  COLOR_SWATCH,
  ICON_COLORS,
  MAX_UPLOAD_BYTES,
  type BuiltinIconName,
  type IconColor,
} from "./catalog";

type Draft =
  | { kind: "builtin"; name: BuiltinIconName; color: IconColor }
  | { kind: "upload"; file: File; previewUrl: string }
  | null;

function arrowTarget(key: string, index: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (index + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

function initialBuiltin(deployment: Deployment) {
  const icon = deployment.icon;
  if (icon?.kind === "builtin") {
    return {
      name: icon.name as BuiltinIconName,
      color: icon.color as IconColor,
    };
  }
  return { name: "Package" as BuiltinIconName, color: "indigo" as IconColor };
}

function AppIconDialogBody({
  deployment,
  open,
  onOpenChange,
  onSaved,
}: {
  deployment: Deployment;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const t = useTranslations("appIcon");
  const [draft, setDraft] = useState<Draft>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const iconRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const colorRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (draft?.kind !== "upload") return;
    const url = draft.previewUrl;
    return () => URL.revokeObjectURL(url);
  }, [draft]);

  const builtin =
    draft?.kind === "builtin" ? draft : initialBuiltin(deployment);
  const selectedName =
    draft?.kind === "builtin" ||
    (draft === null && deployment.icon?.kind === "builtin")
      ? builtin.name
      : null;

  const pickBuiltin = (
    next: Partial<{ name: BuiltinIconName; color: IconColor }>,
  ) => {
    setError(null);
    setDraft({
      kind: "builtin",
      name: next.name ?? builtin.name,
      color: next.color ?? builtin.color,
    });
  };

  const pickFile = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      if (!ACCEPTED_TYPES.includes(file.type)) {
        setError(t("errors.wrongType"));
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        setError(t("errors.tooBig"));
        return;
      }
      setError(null);
      setDraft({ kind: "upload", file, previewUrl: URL.createObjectURL(file) });
    },
    [t],
  );

  const failure = (err: unknown): string => {
    if (err instanceof ApiError) {
      if (err.status === 415) return t("errors.wrongType");
      if (err.status === 413) return t("errors.tooBig");
      if (err.status === 503) return t("errors.storage");
      if (err.status === 403) return t("errors.forbidden");
    }
    return t("errors.generic");
  };

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(failure(err));
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    if (!draft) return;
    void run(() =>
      draft.kind === "builtin"
        ? setBuiltinAppIcon(deployment.id, {
            name: draft.name,
            color: draft.color,
          })
        : uploadAppIcon(deployment.id, draft.file),
    );
  };

  const moveFocus = (
    event: React.KeyboardEvent,
    index: number,
    count: number,
    refs: React.RefObject<(HTMLButtonElement | null)[]>,
    choose: (target: number) => void,
  ) => {
    const target = arrowTarget(event.key, index, count);
    if (target === null) return;
    event.preventDefault();
    choose(target);
    refs.current[target]?.focus();
  };

  const hasCustomIcon = Boolean(deployment.icon);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t("description", { name: deployment.name })}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-lg border bg-muted/40">
            {draft?.kind === "upload" ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={draft.previewUrl}
                alt=""
                className="h-10 w-10 rounded-md object-cover"
              />
            ) : draft?.kind === "builtin" ? (
              <AppIcon
                appId={deployment.id}
                icon={{ kind: "builtin", name: draft.name, color: draft.color }}
                className="h-8 w-8"
              />
            ) : (
              <AppIcon
                appId={deployment.id}
                templateIcon={deployment.template_icon}
                icon={deployment.icon}
                className="h-8 w-8"
              />
            )}
          </div>
          <p className="text-sm text-muted-foreground">{t("preview")}</p>
        </div>

        <div className="space-y-2">
          <p id="app-icon-shape" className="text-sm font-medium">
            {t("shape")}
          </p>
          <div
            role="radiogroup"
            aria-labelledby="app-icon-shape"
            className="grid grid-cols-6 gap-1.5"
          >
            {BUILTIN_ICON_NAMES.map((name, index) => {
              const Icon = BUILTIN_ICONS[name];
              const checked = selectedName === name;
              const tabbable = selectedName ? checked : index === 0;
              return (
                <button
                  key={name}
                  ref={(node) => {
                    iconRefs.current[index] = node;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  aria-label={t(`icons.${name}`)}
                  tabIndex={tabbable ? 0 : -1}
                  onClick={() => pickBuiltin({ name })}
                  onKeyDown={(event) =>
                    moveFocus(
                      event,
                      index,
                      BUILTIN_ICON_NAMES.length,
                      iconRefs,
                      (target) =>
                        pickBuiltin({ name: BUILTIN_ICON_NAMES[target] }),
                    )
                  }
                  className={cn(
                    "flex h-10 items-center justify-center rounded-md border text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50",
                    checked && "border-primary bg-muted ring-1 ring-primary",
                  )}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-2">
          <p id="app-icon-color" className="text-sm font-medium">
            {t("color")}
          </p>
          <div
            role="radiogroup"
            aria-labelledby="app-icon-color"
            className="flex gap-2"
          >
            {ICON_COLORS.map((color, index) => {
              const checked = builtin.color === color;
              return (
                <button
                  key={color}
                  ref={(node) => {
                    colorRefs.current[index] = node;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  aria-label={t(`colors.${color}`)}
                  tabIndex={checked ? 0 : -1}
                  onClick={() => pickBuiltin({ color })}
                  onKeyDown={(event) =>
                    moveFocus(
                      event,
                      index,
                      ICON_COLORS.length,
                      colorRefs,
                      (target) => pickBuiltin({ color: ICON_COLORS[target] }),
                    )
                  }
                  className={cn(
                    "h-7 w-7 rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                    COLOR_SWATCH[color],
                    checked &&
                      "ring-2 ring-foreground ring-offset-2 ring-offset-background",
                  )}
                />
              );
            })}
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">{t("upload.title")}</p>
          <label
            htmlFor="app-icon-file"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              pickFile(event.dataTransfer.files[0]);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed p-4 text-center text-sm focus-within:ring-3 focus-within:ring-ring/50",
              dragging && "border-primary bg-muted",
            )}
          >
            <Upload
              className="h-5 w-5 text-muted-foreground"
              aria-hidden="true"
            />
            <span className="font-medium">{t("upload.choose")}</span>
            <span
              id="app-icon-limits"
              className="text-xs text-muted-foreground"
            >
              {t("upload.limits")}
            </span>
            <input
              id="app-icon-file"
              ref={fileInput}
              type="file"
              accept={ACCEPTED_TYPES.join(",")}
              aria-describedby="app-icon-limits"
              className="sr-only"
              onChange={(event) => {
                pickFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </label>
          {draft?.kind === "upload" && (
            <p className="text-xs text-muted-foreground">
              {t("upload.selected", { name: draft.file.name })}
            </p>
          )}
        </div>

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || !hasCustomIcon}
            onClick={() => void run(() => resetAppIcon(deployment.id))}
          >
            {t("reset")}
          </Button>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t("cancel")}
            </Button>
            <Button type="button" disabled={busy || !draft} onClick={save}>
              {t("save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AppIconDialog(props: Parameters<typeof AppIconDialogBody>[0]) {
  return props.open ? <AppIconDialogBody {...props} /> : null;
}
