"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { getAlertCatalog, updateCatalogAlert } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AlertCatalog, CatalogAlert } from "@/types";
import { AlertTriangle, BellRing, ExternalLink, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const STATE: Record<string, { label: string; className: string }> = {
  normal: {
    label: "OK",
    className:
      "border-green-600/40 bg-green-600/10 text-green-700 dark:text-green-400",
  },
  pending: {
    label: "En attente",
    className:
      "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  firing: {
    label: "Déclenchée",
    className: "border-red-600/40 bg-red-600/15 text-red-700 dark:text-red-400",
  },
  error: {
    label: "Erreur",
    className: "border-red-600/40 text-red-700 dark:text-red-400",
  },
  nodata: { label: "Pas de données", className: "text-muted-foreground" },
};

const NOTIFIES: Record<string, string> = {
  app: "le canal Discord de l'app",
  project: "le canal Discord du projet",
  platform: "le canal Discord de la plateforme (aucun canal projet configuré)",
};

function message(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  try {
    return JSON.parse(text).detail ?? text;
  } catch {
    return text;
  }
}

function AlertRow({
  alert,
  canEdit,
  onSave,
}: {
  alert: CatalogAlert;
  canEdit: boolean;
  onSave: (enabled: boolean, params: Record<string, number>) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, number>>(
    Object.fromEntries(alert.params.map((p) => [p.key, p.value])),
  );
  const [saving, setSaving] = useState(false);
  const dirty = alert.params.some((p) => values[p.key] !== p.value);

  const save = async (enabled: boolean) => {
    setSaving(true);
    try {
      await onSave(enabled, values);
    } finally {
      setSaving(false);
    }
  };

  const state = alert.state ? STATE[alert.state] : null;
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-start",
        alert.state === "firing" && "border-red-600/50 bg-red-600/5",
      )}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{alert.title}</span>
          <Badge variant="outline" className="text-xs">
            {alert.severity === "critical" ? "Critique" : "Avertissement"}
          </Badge>
          {state && (
            <Badge variant="outline" className={cn("text-xs", state.className)}>
              {state.label}
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">{alert.description}</p>
        {alert.params.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 pt-1">
            {alert.params.map((p) => (
              <label key={p.key} className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{p.label}</span>
                <Input
                  type="number"
                  min={p.minimum}
                  max={p.maximum}
                  value={values[p.key]}
                  disabled={!canEdit || saving}
                  onChange={(e) =>
                    setValues((v) => ({
                      ...v,
                      [p.key]: Number(e.target.value),
                    }))
                  }
                  className="h-8 w-24"
                />
                {p.unit && (
                  <span className="text-muted-foreground">{p.unit}</span>
                )}
              </label>
            ))}
            {dirty && alert.enabled && canEdit && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => save(true)}
                disabled={saving}
              >
                Appliquer
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        {saving && (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        )}
        <Switch
          checked={alert.enabled}
          disabled={!canEdit || saving}
          onCheckedChange={(v) => save(v)}
          aria-label={`Activer ${alert.title}`}
        />
      </div>
    </div>
  );
}

/** Ready-made alerts to switch on; anything else is written in Grafana. */
export function AlertsPanel({
  project,
  app,
}: {
  project: string;
  app?: string;
}) {
  const [catalog, setCatalog] = useState<AlertCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      getAlertCatalog(project, app).then(
        (c) => {
          setCatalog(c);
          setError(null);
        },
        (e) => setError(message(e)),
      ),
    [project, app],
  );

  useEffect(() => {
    load();
  }, [load]);

  if (error && !catalog) {
    return (
      <p className="flex items-center gap-2 text-sm text-destructive">
        <AlertTriangle className="h-4 w-4" /> {error}
      </p>
    );
  }
  if (!catalog) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }

  const enabledCount = catalog.items.filter((a) => a.enabled).length;
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <BellRing className="mt-0.5 h-5 w-5 text-muted-foreground" />
            <div className="text-sm">
              <p className="font-medium">
                {enabledCount} alerte{enabledCount > 1 ? "s" : ""} active
                {enabledCount > 1 ? "s" : ""} sur {catalog.items.length}{" "}
                {app ? "pour cette app" : "pour tout le projet"}
              </p>
              <p className="text-muted-foreground">
                Notifications vers{" "}
                {catalog.notifies
                  ? NOTIFIES[catalog.notifies]
                  : "aucun canal : configurez-en un dans l'onglet Sécurité"}
                . Évaluées par Grafana toutes les minutes.
              </p>
              {!catalog.can_edit && (
                <p className="text-muted-foreground">
                  Seuls les admins du projet les modifient.
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {catalog.grafana_rules_url && (
              <a
                href={catalog.grafana_rules_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm hover:bg-muted"
              >
                Voir dans Grafana <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
            {catalog.grafana_new_rule_url && (
              <a
                href={catalog.grafana_new_rule_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm hover:bg-muted"
              >
                Créer une alerte personnalisée{" "}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        </CardContent>
      </Card>
      <div className="space-y-2">
        {catalog.items.map((alert) => (
          <AlertRow
            key={`${alert.id}-${alert.enabled}-${alert.params.map((p) => p.value).join(",")}`}
            alert={alert}
            canEdit={catalog.can_edit}
            onSave={async (enabled, params) => {
              try {
                await updateCatalogAlert(
                  project,
                  app,
                  alert.id,
                  enabled,
                  params,
                );
                toast.success(
                  enabled
                    ? `« ${alert.title} » activée`
                    : `« ${alert.title} » désactivée`,
                );
                await load();
              } catch (e) {
                toast.error(message(e));
              }
            }}
          />
        ))}
      </div>
    </div>
  );
}
