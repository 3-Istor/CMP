"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import type { CanaryBlockerCode, ReleaseSettings, ReleaseSettingsRead } from "@/types";
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

const BLOCKERS: Record<
  CanaryBlockerCode,
  { title: string; short: string; why: string; fix: string }
> = {
  db_without_migrations: {
    title: "Activer les migrations pour passer en Canary",
    short: "l'app a une base de données sans migrations.",
    why: "Pendant un canary, l'ancienne et la nouvelle version tournent ensemble sur la même base. La plateforme doit donc migrer le schéma une seule fois, avant le démarrage de la nouvelle version : c'est le Job de migration du standard CNP.",
    fix: "",
  },
  no_route: {
    title: "Exposer l'app pour passer en Canary",
    short: "l'app n'est pas exposée.",
    why: "Le canary répartit le trafic entre les deux versions via la route HTTP de l'app. Sans exposition, il n'y a pas de trafic à répartir.",
    fix: "Activez l'exposition dans l'onglet Application, section Exposition, puis revenez ici.",
  },
  no_service: {
    title: "Le canary a besoin du Service de l'app",
    short: "le Service de l'app est désactivé.",
    why: "Le canary route le trafic vers deux Services, stable et canary, construits à partir de celui de l'app.",
    fix: "Réactivez service.enabled dans la configuration de l'app.",
  },
};

function componentOf(file: string): string {
  return file.match(/values-(\w+)\.yaml$/)?.[1] ?? file;
}

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
  const [blockerOpen, setBlockerOpen] = useState(false);
  const [migrationsCommand, setMigrationsCommand] = useState("");
  const [pendingMigrations, setPendingMigrations] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getReleaseSettings(deploymentId);
      setRemote(data);
      setDraft(data.settings);
      setPendingMigrations(null);
      setMigrationsCommand((data.migrationsCommand ?? ["/app/migrate"]).join(" "));
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
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(remote.settings) || pendingMigrations !== null;
  const blocker = remote.canaryBlocker;

  const chooseStrategy = (strategy: ReleaseSettings["strategy"]) => {
    if (strategy === "canary" && blocker && !pendingMigrations) {
      setBlockerOpen(true);
      return;
    }
    if (strategy === "rolling") setPendingMigrations(null);
    set("strategy", strategy);
  };

  const enableMigrationsAndCanary = () => {
    const command = migrationsCommand.trim().split(/\s+/).filter(Boolean);
    if (command.length === 0) return;
    setPendingMigrations(command);
    set("strategy", "canary");
    setBlockerOpen(false);
  };
  const set = <K extends keyof ReleaseSettings>(key: K, value: ReleaseSettings[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  const save = async () => {
    setSaving(true);
    try {
      const saved = await updateReleaseSettings(
        deploymentId,
        draft,
        draft.strategy === "canary" ? pendingMigrations : null,
      );
      const kept = Object.entries(saved.strategies)
        .filter(([, s]) => s !== draft.strategy)
        .map(([file]) => componentOf(file));
      toast.success(
        Object.keys(saved.commits).length === 0
          ? "Réglages déjà à jour dans Git."
          : "Réglages commités. Argo CD les applique à la prochaine synchronisation ; la prochaine version suivra cette stratégie.",
        { duration: 7000 },
      );
      if (kept.length > 0) {
        toast.info(
          `Le ${kept.join(" et le ")} reste en rolling update : il n'est pas exposé directement, le frontend lui relaie /api. Le canary s'applique au frontend.`,
          { duration: 9000 },
        );
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
            onValueChange={(v) => chooseStrategy(v as ReleaseSettings["strategy"])}
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
          {draft.strategy === "canary" && remote.files.length > 1 && (
            <p className="text-xs text-muted-foreground">
              Application fullstack : le canary s&apos;applique au frontend, qui est
              exposé. Le backend reste en rolling update, car il n&apos;a pas de route
              propre : le frontend lui relaie <code className="font-mono">/api</code>.
            </p>
          )}
          {blocker && draft.strategy === "rolling" && (
            <p className="text-xs text-muted-foreground">
              Canary indisponible pour l&apos;instant : {BLOCKERS[blocker.code].short}
            </p>
          )}
          {pendingMigrations && draft.strategy === "canary" && (
            <p className="text-xs text-muted-foreground">
              Les migrations seront activées dans le même commit, avec{" "}
              <code className="font-mono">{pendingMigrations.join(" ")}</code>.
            </p>
          )}
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

      {blocker && (
        <Dialog open={blockerOpen} onOpenChange={setBlockerOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{BLOCKERS[blocker.code].title}</DialogTitle>
              <DialogDescription>{BLOCKERS[blocker.code].why}</DialogDescription>
            </DialogHeader>
            {blocker.code === "db_without_migrations" ? (
              <div className="space-y-3 text-sm">
                <div className="space-y-1.5">
                  <Label htmlFor="release-migrations-command">
                    Commande de migration de l&apos;image
                  </Label>
                  <Input
                    id="release-migrations-command"
                    value={migrationsCommand}
                    onChange={(e) => setMigrationsCommand(e.target.value)}
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    <code className="font-mono">/app/migrate</code> pour le template
                    FastAPI + React. Elle doit être idempotente : relancée sans rien à
                    faire, elle sort en 0.
                  </p>
                </div>
                <p className="text-xs text-muted-foreground">
                  L&apos;app n&apos;utilise pas sa base ? Désactivez-la plutôt dans
                  l&apos;onglet Application, section Base de données. Attention, cela
                  supprime la base.
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{BLOCKERS[blocker.code].fix}</p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setBlockerOpen(false)}>
                {blocker.code === "db_without_migrations" ? "Annuler" : "Fermer"}
              </Button>
              {blocker.code === "db_without_migrations" && (
                <Button
                  onClick={enableMigrationsAndCanary}
                  disabled={migrationsCommand.trim() === ""}
                >
                  Activer les migrations et passer en Canary
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}
