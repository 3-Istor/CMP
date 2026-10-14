"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getPlatformStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PlatformStatus } from "@/types";
import { useEffect, useState } from "react";
import { SOURCE_META, timeLabel } from "./labels";

const STALE_MS = 10 * 60_000;
const ARCHIVE_STALE_MS = 2 * 3_600_000;

function Health({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-2 text-sm">
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          ok ? "bg-green-600" : "bg-red-600",
        )}
      />
      {children}
    </span>
  );
}

/** What the platform admin checks first: firing alerts, archive, collector. */
export function PlatformCard() {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [now, setNow] = useState(0);

  useEffect(() => {
    getPlatformStatus().then(
      (s) => {
        setStatus(s);
        setNow(Date.now());
      },
      () => setStatus(null),
    );
  }, []);

  if (!status) return null;
  const archive = status.archive_last_success;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Alertes actives{" "}
            <span className="text-muted-foreground">
              ({status.alerts.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {status.alerts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Aucune alerte en cours.
            </p>
          ) : (
            status.alerts.slice(0, 8).map((a) => (
              <div
                key={`${a.name}-${a.since}-${a.summary}`}
                className="flex gap-2 text-sm"
              >
                <span
                  className={cn(
                    "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                    a.severity === "critical" ? "bg-red-600" : "bg-amber-500",
                  )}
                />
                <span className="min-w-0">
                  <span className="font-medium">{a.name}</span>
                  {a.summary && (
                    <span className="text-muted-foreground">
                      {" "}
                      · {a.summary}
                    </span>
                  )}
                  <span className="block text-xs text-muted-foreground">
                    depuis {timeLabel(a.since)}
                  </span>
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Chaîne d&apos;audit</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Health
            ok={
              !!archive && now - new Date(archive).getTime() < ARCHIVE_STALE_MS
            }
          >
            Archive immuable : dernière heure archivée{" "}
            {archive ? timeLabel(archive) : "jamais"}
          </Health>
          {Object.entries(status.collected_until).map(([source, until]) => (
            <Health
              key={source}
              ok={now - new Date(until).getTime() < STALE_MS}
            >
              Collecte{" "}
              {SOURCE_META[source as keyof typeof SOURCE_META]?.label ?? source}{" "}
              : à jour jusqu&apos;à {timeLabel(until)}
            </Health>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
