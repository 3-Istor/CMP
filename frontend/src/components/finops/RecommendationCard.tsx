"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  applyRecommendation,
  ignoreRecommendation,
  notifyRecommendation,
} from "@/lib/api";
import type { Recommendation } from "@/types";
import { ArrowRight, Bell, Check, Loader2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { formatEUR } from "./format";

const EFFORT_LABEL: Record<string, string> = {
  low: "Effort faible",
  medium: "Effort moyen",
  high: "Effort élevé",
};

const TYPE_LABEL: Record<string, string> = {
  replicas: "Replicas",
  rightsizing: "Rightsizing",
  inactivity: "Inactivité",
  storage: "Stockage",
};

function ConfigBox({
  title,
  config,
  tone,
}: {
  title: string;
  config: Record<string, string | number>;
  tone: "current" | "recommended";
}) {
  return (
    <div
      className="flex-1 rounded-lg border p-3"
      style={{
        borderColor: tone === "recommended" ? "#10B981" : undefined,
        backgroundColor:
          tone === "recommended" ? "rgba(16,185,129,0.06)" : undefined,
      }}
    >
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <dl className="space-y-1 text-sm">
        {Object.entries(config).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-medium tabular-nums">{String(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function RecommendationCard({
  rec,
  onChanged,
}: {
  rec: Recommendation;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const done = rec.status !== "pending";

  const run = async (
    action: "apply" | "ignore" | "notify",
    fn: () => Promise<{ message: string }>,
  ) => {
    setBusy(action);
    try {
      const res = await fn();
      toast.success(res.message);
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Action échouée");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className={done ? "opacity-60" : undefined}>
      <CardContent className="space-y-4 pt-4">
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-medium">{rec.title}</h3>
              <Badge variant="secondary">{TYPE_LABEL[rec.rec_type]}</Badge>
              {done && (
                <Badge variant="outline" className="capitalize">
                  {rec.status}
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{rec.app_name}</p>
          </div>
          <div className="text-right">
            <p
              className="text-lg font-semibold tabular-nums"
              style={{ color: "#10B981" }}
            >
              {formatEUR(rec.monthly_saving_eur)}
              <span className="text-xs font-normal text-muted-foreground">
                {" "}
                / mois
              </span>
            </p>
            <p className="text-xs text-muted-foreground">
              Confiance {rec.confidence}%
            </p>
          </div>
        </div>

        {/* Comparaison Actuelle vs Recommandée */}
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          <ConfigBox title="Configuration actuelle" config={rec.current} tone="current" />
          <ArrowRight className="mx-auto hidden h-5 w-5 shrink-0 text-muted-foreground sm:block" />
          <ConfigBox title="Recommandée" config={rec.recommended} tone="recommended" />
        </div>

        {/* Justification + effort */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            📊 {rec.justification}
          </p>
          <Badge variant="outline">{EFFORT_LABEL[rec.effort]}</Badge>
        </div>

        {/* Actions */}
        {!done && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy !== null || !rec.can_apply}
              title={
                rec.can_apply
                  ? undefined
                  : "Cette recommandation ne peut pas être appliquée automatiquement."
              }
              onClick={() => run("apply", () => applyRecommendation(rec.id))}
            >
              {busy === "apply" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="mr-1.5 h-3.5 w-3.5" />
              )}
              Appliquer
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy !== null}
              onClick={() => run("notify", () => notifyRecommendation(rec.id))}
            >
              {busy === "notify" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Bell className="mr-1.5 h-3.5 w-3.5" />
              )}
              Notifier
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy !== null}
              onClick={() => run("ignore", () => ignoreRecommendation(rec.id))}
            >
              {busy === "ignore" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <X className="mr-1.5 h-3.5 w-3.5" />
              )}
              Ignorer
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
