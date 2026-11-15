"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { searchObsLogs } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import { Search, Waypoints, X } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useId, useState } from "react";
import { TopErrors } from "./OverviewObservability";
import { CardState, RangeSelect, SELECT_CLASS, Section, useObsFormat } from "./shared";

const RANGES = ["15m", "1h", "6h", "24h", "7d"] as const;
const LEVELS = ["error", "warn", "info", "debug"] as const;
const LIMITS = [50, 100, 200, 500];
const LEVEL_CLASS: Record<string, string> = {
  error: "text-red-700 dark:text-red-300",
  err: "text-red-700 dark:text-red-300",
  fatal: "text-red-700 dark:text-red-300",
  critical: "text-red-700 dark:text-red-300",
  warn: "text-amber-800 dark:text-amber-200",
  warning: "text-amber-800 dark:text-amber-200",
};

/**
 * The app's logs from VictoriaLogs: top errors by pattern, then a search
 * by level and phrase. `?trace=<id>` shows the lines of one trace.
 */
export function LogsExplorer({ appId, base }: { appId: number; base: string }) {
  const t = useTranslations("obs.logs");
  const { clock, time } = useObsFormat();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const traceId = params.get("trace");
  const ids = { level: useId(), phrase: useId(), limit: useId() };

  const [range, setRange] = useState("1h");
  const [level, setLevel] = useState("");
  const [phrase, setPhrase] = useState("");
  const [limit, setLimit] = useState(100);
  const [query, setQuery] = useState({ range: "1h", level: "", q: "", limit: 100 });

  const lines = useCardData(
    `obs-logs-${appId}-${JSON.stringify(query)}-${traceId ?? ""}`,
    () =>
      searchObsLogs(appId, {
        range: traceId ? "24h" : query.range,
        level: query.level || undefined,
        q: query.q || undefined,
        trace_id: traceId ?? undefined,
        limit: query.limit,
      }),
  );

  const clearTrace = () => {
    const next = new URLSearchParams(params.toString());
    next.delete("trace");
    const text = next.toString();
    router.replace(text ? `${pathname}?${text}` : pathname);
  };

  return (
    <div className="space-y-6">
      <TopErrors
        appId={appId}
        base={base}
        range={range}
        actions={<RangeSelect value={range} options={RANGES} onChange={setRange} />}
        onPick={(row) => {
          const words = row.pattern.split("<N>")[0].trim().slice(0, 200);
          setPhrase(words);
          setLevel("error");
          setQuery({ range, level: "error", q: words, limit });
        }}
      />

      <Section title={t("title")} description={t("untrusted")}>
        {traceId ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span>{t("traceFilter", { id: traceId.slice(0, 12) })}</span>
            <Button size="sm" variant="outline" onClick={clearTrace}>
              <X aria-hidden />
              {t("clearTrace")}
            </Button>
          </div>
        ) : (
          <form
            role="search"
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery({ range, level, q: phrase.trim(), limit });
            }}
          >
            <RangeSelect value={range} options={RANGES} onChange={setRange} />
            <div className="flex items-center gap-2">
              <label htmlFor={ids.level} className="text-xs text-muted-foreground">
                {t("level")}
              </label>
              <select
                id={ids.level}
                className={SELECT_CLASS}
                value={level}
                onChange={(e) => setLevel(e.target.value)}
              >
                <option value="">{t("anyLevel")}</option>
                {LEVELS.map((l) => (
                  <option key={l} value={l}>
                    {t(`levels.${l}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-56 flex-1 items-center gap-2">
              <label htmlFor={ids.phrase} className="text-xs text-muted-foreground">
                {t("phrase")}
              </label>
              <Input
                id={ids.phrase}
                className="h-8"
                maxLength={200}
                value={phrase}
                placeholder={t("phrasePlaceholder")}
                onChange={(e) => setPhrase(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <label htmlFor={ids.limit} className="text-xs text-muted-foreground">
                {t("limit")}
              </label>
              <select
                id={ids.limit}
                className={SELECT_CLASS}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              >
                {LIMITS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" size="sm">
              <Search aria-hidden />
              {t("search")}
            </Button>
          </form>
        )}

        <CardState card={lines} signal="logs">
          {(rows) =>
            rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("none")}</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-left text-muted-foreground">
                    <tr>
                      <th scope="col" className="px-2 py-1.5 font-medium">{t("time")}</th>
                      <th scope="col" className="px-2 py-1.5 font-medium">{t("level")}</th>
                      <th scope="col" className="px-2 py-1.5 font-medium">{t("message")}</th>
                      <th scope="col" className="px-2 py-1.5 font-medium">{t("trace")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y font-mono">
                    {rows.map((r, i) => (
                      <tr key={`${r.time}-${i}`} className="align-top">
                        <td className="whitespace-nowrap px-2 py-1 text-muted-foreground" title={`${time(r.time)} ${r.pod}`}>
                          {clock(r.time)}
                        </td>
                        <td className={cn("px-2 py-1", r.level && LEVEL_CLASS[r.level])}>
                          {r.level ?? ""}
                        </td>
                        <td className="whitespace-pre-wrap break-all px-2 py-1">{r.message}</td>
                        <td className="px-2 py-1">
                          {r.trace_id && (
                            <Link
                              href={`${base}/traces?trace=${r.trace_id}`}
                              className="inline-flex items-center gap-1 text-primary hover:underline"
                              aria-label={`${t("trace")} ${r.trace_id}`}
                            >
                              <Waypoints className="h-3.5 w-3.5" aria-hidden />
                              {r.trace_id.slice(0, 8)}
                            </Link>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </CardState>
      </Section>
    </div>
  );
}
