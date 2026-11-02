"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { getAppLogs } from "@/lib/api";
import { useAppHealth } from "@/lib/hooks";
import { useCardData } from "@/lib/useCardData";
import { AlertTriangle, Lock, Pause, Play } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { LogList } from "./LogList";

const POLL_MS = 3000;
const WINDOWS = ["15m", "1h", "24h"] as const;
const LEVELS = ["error", "warn", "info", "debug"] as const;

const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function LogsView({ appId }: { appId: number }) {
  const t = useTranslations("appPage.logs");
  const th = useTranslations("health.components");
  const [level, setLevel] = useState("");
  const [component, setComponent] = useState("");
  const [search, setSearch] = useState("");
  const [since, setSince] = useState<(typeof WINDOWS)[number]>("1h");
  const [paused, setPaused] = useState(false);
  const query = useDebounced(search.trim(), 400);

  const { health } = useAppHealth(appId, 0);
  const components = (health?.components ?? []).map((c) => c.name);

  const logs = useCardData(
    `${appId}|${level}|${component}|${query}|${since}`,
    () =>
      getAppLogs(appId, { limit: 200, since, level, component, q: query }),
    { intervalMs: paused ? 0 : POLL_MS },
  );
  const lines = logs.data ? [...logs.data].reverse() : [];

  const regionRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = regionRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [logs.data]);

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            {t("level")}
            <select
              className={SELECT_CLASS}
              value={level}
              onChange={(e) => setLevel(e.target.value)}
            >
              <option value="">{t("allLevels")}</option>
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {t(`levels.${l}`)}
                </option>
              ))}
            </select>
          </label>
          {components.length > 1 && (
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {t("component")}
              <select
                className={SELECT_CLASS}
                value={component}
                onChange={(e) => setComponent(e.target.value)}
              >
                <option value="">{t("allComponents")}</option>
                {components.map((c) => (
                  <option key={c} value={c}>
                    {th.has(c) ? th(c) : c}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            {t("window")}
            <select
              className={SELECT_CLASS}
              value={since}
              onChange={(e) => setSince(e.target.value as typeof since)}
            >
              {WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {t(`windows.${w}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-muted-foreground">
            {t("search")}
            <Input
              type="search"
              maxLength={200}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="h-8"
            />
          </label>
          <Button
            variant="outline"
            onClick={() => setPaused((p) => !p)}
            aria-pressed={paused}
          >
            {paused ? (
              <Play aria-hidden />
            ) : (
              <Pause aria-hidden />
            )}
            {paused ? t("resume") : t("pause")}
          </Button>
        </div>

        <p role="status" className="text-xs text-muted-foreground">
          {paused ? t("paused") : t("live")}
          {logs.data && ` - ${t("lineCount", { count: lines.length })}`}
        </p>

        {logs.forbidden ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Lock className="h-4 w-4" aria-hidden />
            {t("restricted")}
          </p>
        ) : logs.loading && !logs.data ? (
          <Skeleton className="h-96 w-full" />
        ) : logs.error && !logs.data ? (
          <div role="alert" className="flex h-96 flex-col items-start gap-2 text-sm">
            <p className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-4 w-4" aria-hidden />
              {t("loadFailed")}
            </p>
            <Button variant="outline" size="sm" onClick={logs.retry}>
              {t("retry")}
            </Button>
          </div>
        ) : (
          <>
            {logs.stale && (
              <p role="status" className="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                {t("stale")}
                <Button variant="ghost" size="xs" onClick={logs.retry}>
                  {t("retry")}
                </Button>
              </p>
            )}
            <div
              ref={regionRef}
              role="log"
              aria-live="off"
              aria-label={t("region")}
              tabIndex={0}
              onScroll={(e) => {
                const el = e.currentTarget;
                pinned.current =
                  el.scrollHeight - el.scrollTop - el.clientHeight < 40;
              }}
              className="h-96 space-y-0.5 overflow-y-auto rounded-lg border bg-muted/30 p-3"
            >
              {lines.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("emptyFiltered")}</p>
              ) : (
                <LogList lines={lines} />
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
