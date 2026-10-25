"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ReleaseEvent, ReleaseStep } from "@/types";
import {
  durationBetween,
  formatDateTime,
  isVisibleStep,
  imageTag,
  OUTCOME,
  STRATEGY,
  TONE,
} from "./format";

const SEGMENT: Record<ReleaseStep["status"], string> = {
  done: "bg-green-600",
  failed: "bg-red-600",
  active: "bg-amber-500",
  running: "bg-primary",
  skipped: "bg-muted-foreground/30",
  pending: "bg-muted",
};

function Progress({ event }: { event: ReleaseEvent }) {
  const steps = (event.steps ?? []).filter(isVisibleStep);
  const reached =
    event.reached_step != null
      ? steps.filter((s) => s.index <= event.reached_step!).length
      : null;
  const label =
    reached != null && steps.length > 0
      ? `Étape ${Math.max(reached, 1)} sur ${steps.length}`
      : "Progression inconnue";
  if (steps.length === 0) {
    return <span className="text-xs text-muted-foreground">-</span>;
  }
  return (
    <span className="flex gap-0.5" role="img" aria-label={label} title={label}>
      {steps.map((s) => (
        <i key={s.index} className={cn("h-2 w-2.5 rounded-sm", SEGMENT[s.status])} />
      ))}
    </span>
  );
}

function forcedReason(event: ReleaseEvent): string | null {
  const withReason = (event.actors ?? []).filter((a) => a.reason);
  return withReason.length ? withReason[withReason.length - 1].reason : null;
}

export function ReleaseHistoryTable({ events }: { events: ReleaseEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Aucune release enregistrée pour cette application.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Révision</th>
            <th scope="col" className="py-2 pr-4 font-medium">Version</th>
            <th scope="col" className="py-2 pr-4 font-medium">Issue</th>
            <th scope="col" className="py-2 pr-4 font-medium">Progression</th>
            <th scope="col" className="py-2 pr-4 font-medium">Démarrée</th>
            <th scope="col" className="py-2 font-medium">Durée</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const outcome = OUTCOME[e.outcome] ?? { label: e.outcome, tone: "muted" as const };
            const reason = e.forced ? forcedReason(e) : null;
            return (
              <tr key={e.id} className="border-b last:border-0 align-top">
                <td className="py-2 pr-4 font-mono">#{e.revision}</td>
                <td className="py-2 pr-4">
                  <span className="font-mono" title={e.image}>
                    {imageTag(e.image)}
                  </span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {STRATEGY[e.strategy] ?? e.strategy}
                  </span>
                </td>
                <td className="py-2 pr-4">
                  <div className="flex flex-wrap gap-1">
                    <Badge variant="outline" className={TONE[outcome.tone]}>
                      {outcome.label}
                    </Badge>
                    {e.forced && (
                      <Badge variant="outline" className={TONE.warn}>
                        Forcée
                      </Badge>
                    )}
                  </div>
                  {reason && (
                    <p className="mt-1 max-w-64 text-xs text-muted-foreground">
                      Motif : {reason}
                    </p>
                  )}
                </td>
                <td className="py-2 pr-4">
                  <Progress event={e} />
                </td>
                <td className="py-2 pr-4 font-mono text-xs tabular-nums">
                  {formatDateTime(e.started_at)}
                </td>
                <td className="py-2 font-mono text-xs tabular-nums">
                  {durationBetween(e.started_at, e.finished_at) || "-"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
