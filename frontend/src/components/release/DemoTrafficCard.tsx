"use client";

import type { Translate } from "@/components/finops/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { startTraffic, stopTraffic, streamTraffic } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { TrafficEvent, TrafficFrame } from "@/types";
import { Activity, Loader2, Play, Square } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./format";

const RATES = [5, 10, 20];
const DURATIONS = [2, 5, 10];
const MAX_BUBBLES = 80;
const BUBBLE_MS = 2600;
// The split shown follows the weights as they move: a cumulative one would
// still say 2 % canary long after the canary took 50 %.
const WINDOW_MS = 10_000;

const COLOR: Record<TrafficEvent["version"], string> = {
  stable: "bg-primary/80 shadow-primary/40",
  canary: "bg-amber-500/90 shadow-amber-500/40",
  error: "bg-red-600/90 shadow-red-600/40",
};

interface Bubble {
  id: number;
  version: TrafficEvent["version"];
  left: number;
  size: number;
}

function formatRemaining(seconds: number, t: Translate): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes > 0
    ? t("traffic.remainingMinutes", { minutes, seconds: String(rest).padStart(2, "0") })
    : t("traffic.remainingSeconds", { seconds: rest });
}

/**
 * Demo visitors for an app nobody uses yet: the CMP logs a technical user in
 * through the app's SSO and sends fresh visitors, each bubble being one answer
 * coloured by the version that served it.
 */
export function DemoTrafficCard({
  deploymentId,
  canOperate,
  weights,
}: {
  deploymentId: number;
  canOperate: boolean;
  weights: { stable: number; canary: number } | null;
}) {
  const t = useTranslations("release");
  const [frame, setFrame] = useState<TrafficFrame>({ running: false });
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [rps, setRps] = useState(10);
  const [minutes, setMinutes] = useState(5);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const [recentAnswers, setRecentAnswers] = useState<
    { at: number; version: TrafficEvent["version"] }[]
  >([]);

  useEffect(() => {
    const timer = setInterval(() => {
      const cutoff = Date.now() - WINDOW_MS;
      setRecentAnswers((current) => current.filter((r) => r.at >= cutoff));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const addBubbles = useCallback((events: TrafficEvent[]) => {
    if (events.length === 0) return;
    const now = Date.now();
    setRecentAnswers((current) => [
      ...current,
      ...events.map((e) => ({ at: now, version: e.version })),
    ]);
    const fresh = events.slice(-30).map((e) => ({
      id: e.seq,
      version: e.version,
      left: Math.random() * 94 + 3,
      size: 10 + Math.random() * 14,
    }));
    setBubbles((current) => [...current, ...fresh].slice(-MAX_BUBBLES));
    const ids = new Set(fresh.map((b) => b.id));
    setTimeout(() => setBubbles((current) => current.filter((b) => !ids.has(b.id))), BUBBLE_MS);
  }, []);

  const follow = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    let running = false;
    const read = () =>
      streamTraffic(
        deploymentId,
        (next) => {
          running = next.running;
          setFrame(next);
          addBubbles(next.events ?? []);
        },
        controller.signal,
      )
        .catch(() => undefined)
        .then(() => {
          // A proxy may cut a long stream: pick the run up again while it lasts.
          if (!controller.signal.aborted && running) setTimeout(read, 1000);
        });
    read();
  }, [deploymentId, addBubbles]);

  // A run started from another tab, or before a reload, is picked up here.
  useEffect(() => {
    follow();
    return () => abortRef.current?.abort();
  }, [follow]);

  const start = async () => {
    setBusy(true);
    try {
      await startTraffic(deploymentId, rps, minutes);
    } catch (e) {
      // Already running (another tab, a reload): show it rather than an error.
      if (!errorMessage(e).includes("already running")) toast.error(errorMessage(e));
    } finally {
      follow();
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await stopTraffic(deploymentId);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const counts = frame.counts ?? { stable: 0, canary: 0, error: 0 };
  const recent = { stable: 0, canary: 0, error: 0 };
  for (const r of recentAnswers) recent[r.version] += 1;
  const recentTotal = recent.stable + recent.canary + recent.error;
  const share = (n: number) => (recentTotal > 0 ? Math.round((n / recentTotal) * 100) : 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Activity className="h-4 w-4 text-muted-foreground" />
          {t("traffic.title")}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {t("traffic.intro")}
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          className="relative h-40 overflow-hidden rounded-lg border bg-muted/40"
          role="img"
          aria-label={t("traffic.ariaCounts", {
            stable: counts.stable,
            canary: counts.canary,
            error: counts.error,
          })}
        >
          <style>{`@keyframes cnp-rise {
            0% { transform: translateY(0) scale(.6); opacity: 0; }
            12% { opacity: 1; }
            100% { transform: translateY(-150px) scale(1); opacity: 0; }
          }`}</style>
          {bubbles.map((b) => (
            <span
              key={b.id}
              className={cn("absolute bottom-0 rounded-full shadow-lg", COLOR[b.version])}
              style={{
                left: `${b.left}%`,
                width: b.size,
                height: b.size,
                animation: `cnp-rise ${BUBBLE_MS}ms ease-out forwards`,
              }}
            />
          ))}
          {!frame.running && bubbles.length === 0 && (
            <p className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
              {t("traffic.idle")}
            </p>
          )}
        </div>

        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          {(
            [
              ["stable", "bg-primary"],
              ["canary", "bg-amber-500"],
              ["error", "bg-red-600"],
            ] as const
          ).map(([key, swatch]) => (
            <div key={key} className="rounded-md border px-2 py-1.5">
              <div className="flex items-center justify-center gap-1.5 text-muted-foreground">
                <i className={cn("size-2.5 rounded-sm", swatch)} aria-hidden />
                {t(`traffic.labels.${key}`)}
              </div>
              <div className="font-mono text-sm tabular-nums">{share(recent[key])} %</div>
              <div className="font-mono text-[11px] tabular-nums text-muted-foreground">
                {t("traffic.total", { count: counts[key] })}
              </div>
            </div>
          ))}
        </div>
        {weights && recentTotal > 0 && (
          <p className="text-xs text-muted-foreground">
            {t("traffic.split", {
              stable: share(recent.stable),
              canary: share(recent.canary),
              weightStable: weights.stable,
              weightCanary: weights.canary,
            })}
          </p>
        )}

        {canOperate &&
          (frame.running ? (
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {t("traffic.running", {
                  rps: frame.rps ?? 0,
                  remaining: formatRemaining(frame.remainingSeconds ?? 0, t),
                })}
                {frame.startedBy && ` · ${t("traffic.startedBy", { user: frame.startedBy })}`}
              </span>
              <Button variant="outline" size="sm" onClick={stop} disabled={busy}>
                <Square className="mr-2 h-3.5 w-3.5" />
                {t("traffic.stop")}
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Select value={String(rps)} onValueChange={(v) => setRps(Number(v))}>
                <SelectTrigger size="sm">
                  <SelectValue>{(v) => t("traffic.rate", { count: Number(v) })}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {RATES.map((r) => (
                    <SelectItem key={r} value={String(r)}>
                      {t("traffic.rate", { count: r })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={String(minutes)} onValueChange={(v) => setMinutes(Number(v))}>
                <SelectTrigger size="sm">
                  <SelectValue>{(v) => t("traffic.minutes", { count: Number(v) })}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {DURATIONS.map((d) => (
                    <SelectItem key={d} value={String(d)}>
                      {t("traffic.minutes", { count: d })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button size="sm" onClick={start} disabled={busy}>
                {busy ? (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="mr-2 h-3.5 w-3.5" />
                )}
                {t("traffic.start")}
              </Button>
            </div>
          ))}
      </CardContent>
    </Card>
  );
}
