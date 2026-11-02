"use client";

import { cn } from "@/lib/utils";
import type { AppLogLevel, AppLogLine } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import { clockTime } from "./format";

const LEVEL_STYLE: Record<AppLogLevel, string> = {
  error: "font-semibold text-red-700 dark:text-red-400",
  warn: "font-semibold text-amber-700 dark:text-amber-400",
  info: "text-muted-foreground",
  debug: "text-muted-foreground",
};

export function LogList({ lines }: { lines: AppLogLine[] }) {
  const t = useTranslations("appPage.logs.levels");
  const locale = useLocale();
  return (
    <>
      {lines.map((l, i) => (
        <div
          key={`${l.time}-${l.pod}-${i}`}
          className="flex gap-2 whitespace-pre-wrap break-all font-mono text-xs leading-relaxed"
        >
          <span className="shrink-0 text-muted-foreground tabular-nums">
            {clockTime(l.time, locale)}
          </span>
          <span
            className={cn(
              "w-14 shrink-0 uppercase",
              l.level ? LEVEL_STYLE[l.level] : "",
            )}
          >
            {l.level ? t(l.level) : ""}
          </span>
          {l.component && (
            <span className="shrink-0 text-muted-foreground">{l.component}</span>
          )}
          <span>{l.line}</span>
        </div>
      ))}
    </>
  );
}
