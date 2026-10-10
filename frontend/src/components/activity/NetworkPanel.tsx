"use client";

import { Badge } from "@/components/ui/badge";
import { getDeniedFlows } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DeniedFlow } from "@/types";
import { ChevronDown, ShieldBan } from "lucide-react";
import { useEffect, useState } from "react";
import { timeLabel } from "./labels";

/** Flows a network policy dropped: "why can't my backend reach X?". */
export function NetworkPanel({ project }: { project: string }) {
  const [flows, setFlows] = useState<DeniedFlow[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    getDeniedFlows(project, 1).then(setFlows, () => setFlows([]));
  }, [project]);

  const total = flows?.reduce((a, f) => a + f.count, 0) ?? 0;

  return (
    <div className="rounded-xl border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <ShieldBan className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">Flux réseau refusés · 24 h</span>
        <Badge
          variant="outline"
          className={cn(total > 0 && "border-amber-500/40 bg-amber-500/15")}
        >
          {flows === null ? "…" : total}
        </Badge>
        <span className="ml-2 text-muted-foreground">
          Bloqués par une network policy, côté de ce projet.
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="overflow-x-auto border-t">
          {flows && flows.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-1.5 font-medium">Source</th>
                  <th className="px-3 py-1.5 font-medium">Destination</th>
                  <th className="px-3 py-1.5 font-medium">Port</th>
                  <th className="px-3 py-1.5 font-medium">Sens</th>
                  <th className="px-3 py-1.5 text-right font-medium">Fois</th>
                  <th className="px-3 py-1.5 font-medium">Dernier</th>
                </tr>
              </thead>
              <tbody className="font-mono text-xs">
                {flows.map((f) => (
                  <tr
                    key={`${f.source}|${f.destination}|${f.port}|${f.direction}`}
                    className="border-t"
                  >
                    <td className="px-3 py-1.5">{f.source}</td>
                    <td className="px-3 py-1.5">{f.destination}</td>
                    <td className="px-3 py-1.5">
                      {f.port ?? "—"} {f.protocol}
                    </td>
                    <td className="px-3 py-1.5">
                      {f.direction === "ingress" ? "entrant" : "sortant"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {f.count}
                    </td>
                    <td className="px-3 py-1.5 font-sans">
                      {timeLabel(f.last)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              Aucun flux refusé sur les dernières 24 heures.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
