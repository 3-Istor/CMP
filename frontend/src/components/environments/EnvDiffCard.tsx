"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { EnvDiff, EnvDiffField } from "@/types";
import { CheckCircle2, GitCompareArrows } from "lucide-react";
import { useTranslations } from "next-intl";
import { errorMessage, TONE } from "../release/format";

const MAX_CONSEQUENCES = 10;

type Translate = (key: string, values?: Record<string, string | number>) => string;

function fieldLabel(t: Translate, key: string): string {
  const dot = key.lastIndexOf(".");
  if (dot === -1) return t(`fields.${key}`);
  return `${t(`components.${key.slice(0, dot)}`)} / ${t(`fields.${key.slice(dot + 1)}`)}`;
}

function displayValue(t: Translate, value: unknown): string {
  if (value === null || value === undefined) return t("absent");
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function diffConsequences(t: Translate, diff: EnvDiff | null): string[] {
  if (!diff?.stagingAvailable) return [];
  const rows = [
    ...diff.fields
      .filter((f) => f.differs)
      .map((f) => ({
        label: fieldLabel(t, f.key),
        staging: displayValue(t, f.staging),
        prod: displayValue(t, f.prod),
      })),
    ...diff.values.map((v) => ({
      label: v.path,
      staging: displayValue(t, v.staging),
      prod: displayValue(t, v.prod),
    })),
  ];
  const shown = rows.slice(0, MAX_CONSEQUENCES).map((r) => t("confirm.row", r));
  if (rows.length > MAX_CONSEQUENCES) {
    shown.push(t("confirm.more", { count: rows.length - MAX_CONSEQUENCES }));
  }
  return shown;
}

function Row({ field, t }: { field: EnvDiffField; t: Translate }) {
  return (
    <tr className={field.differs ? "border-t bg-amber-500/5" : "border-t"}>
      <td className="py-1.5 pr-3 font-medium">{fieldLabel(t, field.key)}</td>
      <td className="py-1.5 pr-3 font-mono break-all">{field.staging ?? t("unknown")}</td>
      <td className="py-1.5 pr-3 font-mono break-all">{field.prod ?? t("unknown")}</td>
      <td className="py-1.5">
        {field.differs ? (
          <Badge variant="outline" className={TONE.warn}>
            <GitCompareArrows className="mr-1 h-3 w-3" aria-hidden />
            {t("differs")}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">{t("same")}</span>
        )}
      </td>
    </tr>
  );
}

export function EnvDiffCard({ diff, error }: { diff: EnvDiff | null; error: unknown }) {
  const t = useTranslations("envDiff") as unknown as Translate;
  const identical = diff?.stagingAvailable && !diff.fields.some((f) => f.differs) && diff.values.length === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
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
        {identical && (
          <p className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />
            {t("identical")}
          </p>
        )}
        {diff?.stagingAvailable && !identical && (
          <>
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
                    <Row key={field.key} field={field} t={t} />
                  ))}
                </tbody>
              </table>
            </div>
            {diff.values.length > 0 && (
              <div className="space-y-1.5">
                <h3 className="text-sm font-medium">{t("valuesTitle")}</h3>
                <p className="text-xs text-muted-foreground">{t("valuesHint")}</p>
                <ul className="space-y-1 text-xs">
                  {diff.values.map((value) => (
                    <li key={value.path} className="flex flex-wrap gap-x-3 gap-y-0.5">
                      <span className="font-mono font-medium">{value.path}</span>
                      <span className="font-mono text-muted-foreground">
                        {t("columns.staging")}: {displayValue(t, value.staging)}
                      </span>
                      <span className="font-mono text-muted-foreground">
                        {t("columns.prod")}: {displayValue(t, value.prod)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
