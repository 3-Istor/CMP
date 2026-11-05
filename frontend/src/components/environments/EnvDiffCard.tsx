"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { EnvDiff, EnvDiffField } from "@/types";
import { CheckCircle2, ChevronRight, Copy, GitCompareArrows } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage, TONE } from "../release/format";

const MAX_CONSEQUENCES = 10;
const NONE = "\u2014";

type Translate = (key: string, values?: Record<string, string | number>) => string;

function fieldLabel(t: Translate, key: string): string {
  const dot = key.lastIndexOf(".");
  if (dot === -1) return t(`fields.${key}`);
  return `${t(`components.${key.slice(0, dot)}`)} / ${t(`fields.${key.slice(dot + 1)}`)}`;
}

export function diffConsequences(t: Translate, diff: EnvDiff | null): string[] {
  if (!diff?.stagingAvailable) return [];
  // A "differs" field has a value on both sides.
  const rows = diff.fields
    .filter((f) => f.status === "differs")
    .map((f) => t("confirm.row", { label: fieldLabel(t, f.key), staging: f.staging ?? NONE, prod: f.prod ?? NONE }));
  const shown = rows.slice(0, MAX_CONSEQUENCES);
  if (rows.length > MAX_CONSEQUENCES) {
    shown.push(t("confirm.more", { count: rows.length - MAX_CONSEQUENCES }));
  }
  return shown;
}

function Value({ value, label, t }: { value: string | null; label: string; t: Translate }) {
  if (!value) return <span className="text-muted-foreground">{NONE}</span>;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("copied"));
    } catch {
      // Clipboard refused (insecure context or permission): no toast, nothing was copied.
    }
  };
  return (
    <span className="group/value inline-flex items-center gap-1">
      <span className="font-mono break-all">{value}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        onClick={copy}
        aria-label={`${t("copy")} (${label})`}
        title={t("copy")}
        className="opacity-0 transition-opacity group-hover/value:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100"
      >
        <Copy aria-hidden />
      </Button>
    </span>
  );
}

function Row({ field, asleep, t }: { field: EnvDiffField; asleep: boolean; t: Translate }) {
  const label = fieldLabel(t, field.key);
  return (
    <tr className={field.status === "differs" ? "border-t bg-amber-500/5" : "border-t"}>
      <td className="py-1.5 pr-3 font-medium">{label}</td>
      <td className="py-1.5 pr-3">
        {asleep ? (
          <span className="text-muted-foreground">{t("asleep")}</span>
        ) : (
          <Value value={field.staging} label={`${label}, ${t("columns.staging")}`} t={t} />
        )}
      </td>
      <td className="py-1.5 pr-3">
        <Value value={field.prod} label={`${label}, ${t("columns.prod")}`} t={t} />
      </td>
      <td className="py-1.5">
        {field.status === "differs" ? (
          <Badge variant="outline" className={TONE.warn}>
            <GitCompareArrows className="mr-1 h-3 w-3" aria-hidden />
            {t("differs")}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">
            {field.status === "same" ? t("same") : NONE}
          </span>
        )}
      </td>
    </tr>
  );
}

export function EnvDiffCard({ diff, error }: { diff: EnvDiff | null; error: unknown }) {
  const t = useTranslations("envDiff") as unknown as Translate;
  const [open, setOpen] = useState(false);
  const count = diff?.fields.filter((f) => f.status === "differs").length ?? 0;
  // "(0)" must only mean "compared, nothing differs": never when asleep or nothing was comparable.
  const comparable = diff?.fields.some((f) => f.status !== "unknown");
  const summary = diff?.stagingAsleep
    ? t("titleAsleep")
    : comparable
      ? t("titleCount", { count })
      : t("title");
  const identical = diff?.fields.every((f) => f.status === "same");

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {diff?.stagingAvailable ? (
            <button
              type="button"
              aria-expanded={open}
              title={open ? t("collapse") : t("expand")}
              onClick={() => setOpen(!open)}
              className="flex w-full items-center gap-1.5 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ChevronRight
                className={cn("h-4 w-4 shrink-0 transition-transform", open && "rotate-90")}
                aria-hidden
              />
              {summary}
            </button>
          ) : (
            t("title")
          )}
        </CardTitle>
      </CardHeader>
      {(open || !diff?.stagingAvailable) && (
        <CardContent className="space-y-4">
          {error != null && !diff && (
            <p className={`rounded-md border px-3 py-2 text-sm ${TONE.bad}`}>
              {t("loadFailed", { message: errorMessage(error) })}
            </p>
          )}
          {error == null && !diff && <Skeleton className="h-24 w-full" aria-label={t("loading")} />}
          {diff && !diff.stagingAvailable && (
            <p className="text-sm text-muted-foreground">{t("stagingOff")}</p>
          )}
          {diff?.stagingAvailable && identical && (
            <p className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />
              {t("identical")}
            </p>
          )}
          {diff?.stagingAvailable && !identical && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="pb-1.5 pr-3 font-medium">{t("columns.field")}</th>
                    <th className="pb-1.5 pr-3 font-medium">{t("columns.staging")}</th>
                    <th className="pb-1.5 pr-3 font-medium">{t("columns.prod")}</th>
                    <th className="pb-1.5 font-medium">{t("columns.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.fields.map((field) => (
                    <Row key={field.key} field={field} asleep={diff.stagingAsleep} t={t} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
