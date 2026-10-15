"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { getReleaseSettings, updateReleaseSettings } from "@/lib/api";
import type { ReleaseSettings, ReleaseSettingsRead } from "@/types";
import { Loader2, Save, SlidersHorizontal } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "./format";

const STRATEGIES: Record<ReleaseSettings["strategy"], { label: string; hint: string }> = {
  rolling: {
    label: "Rolling update",
    hint: "Chaque nouvelle version remplace les pods directement, sans étape.",
  },
  canary: {
    label: "Canary",
    hint: "La nouvelle version reçoit d'abord une part du trafic, analysée avant d'aller plus loin.",
  },
};

const PRESETS: Record<ReleaseSettings["preset"], { label: string; steps: string }> = {
  safe: {
    label: "Safe",
    steps: "10 % → analyse 2 min → 25 % → validation manuelle → 50 % → analyse 2 min → 100 %",
  },
  fast: { label: "Fast", steps: "25 % → analyse 1 min → 100 %" },
  demo: { label: "Démo", steps: "10 % → analyse 30 s → 50 % → pause 10 s → 100 %" },
  manual: {
    label: "Manuel",
    steps: "10 % → validation manuelle → 50 % → validation manuelle → 100 %",
  },
};

const STICKINESS: Record<ReleaseSettings["stickiness"], string> = {
  cookie: "Cookie : chaque utilisateur reste sur la version qui l'a servi",
  none: "Aucune : chaque requête est tirée au sort",
};

export function ReleaseSettingsCard({
  deploymentId,
  inProgress,
}: {
  deploymentId: number;
  inProgress: boolean;
}) {
  const [remote, setRemote] = useState<ReleaseSettingsRead | null>(null);
  const [draft, setDraft] = useState<ReleaseSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getReleaseSettings(deploymentId);
      setRemote(data);
      setDraft(data.settings);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [deploymentId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Impossible de lire les réglages de la release : {error}
        </CardContent>
      </Card>
    );
  }
  if (!remote || !draft) return <Skeleton className="h-48 w-full" />;

  const locked = !remote.canEdit || inProgress || saving;
  const dirty = JSON.stringify(draft) !== JSON.stringify(remote.settings);
  const set = <K extends keyof ReleaseSettings>(key: K, value: ReleaseSettings[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  const save = async () => {
    setSaving(true);
    try {
      const saved = await updateReleaseSettings(deploymentId, draft);
      const kept = Object.entries(saved.strategies)
        .filter(([, s]) => s !== draft.strategy)
        .map(([file]) => file);
      toast.success(
        Object.keys(saved.commits).length === 0
          ? "Réglages déjà à jour dans Git."
          : "Réglages commités. Argo CD les applique à la prochaine synchronisation ; la prochaine version suivra cette stratégie.",
        { duration: 7000 },
      );
      if (kept.length > 0) {
        toast.info(`Reste en rolling (pas de route HTTP propre) : ${kept.join(", ")}`, {
          duration: 9000,
        });
      }
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
          Réglages de la release
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Enregistrés dans Git, section <code className="font-mono">release</code> de{" "}
          {remote.files.map((f, i) => (
            <span key={f}>
              {i > 0 && ", "}
              <code className="font-mono">{f}</code>
            </span>
          ))}
          . Modifier le fichier à la main revient au même.
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {!remote.declared && (
          <p className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
            Aucune section <code className="font-mono">release</code> dans Git : ce sont les
            valeurs par défaut de la plateforme.
          </p>
        )}

        <div className="space-y-1.5">
          <Label>Stratégie</Label>
          <Select
            value={draft.strategy}
            onValueChange={(v) => set("strategy", v as ReleaseSettings["strategy"])}
            disabled={locked}
          >
            <SelectTrigger className="w-full">
              <SelectValue>
                {(v) => STRATEGIES[v as ReleaseSettings["strategy"]].label}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(STRATEGIES).map(([value, s]) => (
                <SelectItem key={value} value={value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">{STRATEGIES[draft.strategy].hint}</p>
        </div>

        {draft.strategy === "canary" && (
          <>
            <div className="space-y-1.5">
              <Label>Preset</Label>
              <Select
                value={draft.preset}
                onValueChange={(v) => set("preset", v as ReleaseSettings["preset"])}
                disabled={locked}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>
                    {(v) => PRESETS[v as ReleaseSettings["preset"]].label}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(PRESETS).map(([value, p]) => (
                    <SelectItem key={value} value={value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Smoke test → {PRESETS[draft.preset].steps}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>Affinité de session</Label>
              <Select
                value={draft.stickiness}
                onValueChange={(v) => set("stickiness", v as ReleaseSettings["stickiness"])}
                disabled={locked}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>
                    {(v) => STICKINESS[v as ReleaseSettings["stickiness"]]}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(STICKINESS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="release-smoke">Chemin du smoke test</Label>
                <Input
                  id="release-smoke"
                  value={draft.smokePath}
                  onChange={(e) => set("smokePath", e.target.value)}
                  disabled={locked}
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="release-error-rate">Taux d&apos;erreur max (%)</Label>
                <Input
                  id="release-error-rate"
                  type="number"
                  min={0.1}
                  max={100}
                  step={0.1}
                  value={draft.errorRateMax}
                  onChange={(e) => set("errorRateMax", Number(e.target.value))}
                  disabled={locked}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="release-min-requests">Requêtes minimum</Label>
                <Input
                  id="release-min-requests"
                  type="number"
                  min={1}
                  step={1}
                  value={draft.minRequests}
                  onChange={(e) => set("minRequests", Number(e.target.value))}
                  disabled={locked}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Sous le minimum de requêtes dans la fenêtre d&apos;analyse, le résultat est
              « non concluant » et la release se met en pause.
            </p>

            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="release-allow-force">Autoriser « Tout promouvoir »</Label>
                <p className="text-xs text-muted-foreground">
                  Passer directement à 100 % avec une raison, tracée dans l&apos;historique.
                </p>
              </div>
              <Switch
                id="release-allow-force"
                checked={draft.allowForce}
                onCheckedChange={(checked) => set("allowForce", checked)}
                disabled={locked}
              />
            </div>
          </>
        )}

        {inProgress && (
          <p className="text-xs text-muted-foreground">
            Une release est en cours : promouvez-la ou annulez-la avant de changer ses
            réglages.
          </p>
        )}
        {!remote.canEdit ? (
          <p className="border-t border-border pt-2 text-xs text-muted-foreground">
            Seuls les admins du projet peuvent changer ces réglages.
          </p>
        ) : (
          dirty &&
          !inProgress && (
            <div className="border-t border-border pt-2">
              <Button onClick={save} disabled={saving} className="w-full">
                {saving ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                Enregistrer dans Git
              </Button>
            </div>
          )
        )}
      </CardContent>
    </Card>
  );
}
