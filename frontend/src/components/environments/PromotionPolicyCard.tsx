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
import { Switch } from "@/components/ui/switch";
import { updatePromotionPolicy } from "@/lib/api";
import type { OnInconclusive, PromotionPolicy, SuiteSpec } from "@/types";
import { ListChecks, Plus, Save, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "../release/format";

const INCONCLUSIVE: Record<OnInconclusive, string> = {
  ask: "Demander à un admin",
  fail: "Bloquer",
  pass: "Laisser passer (tracé)",
};

function newSuite(kind: SuiteSpec["kind"], index: number): SuiteSpec {
  return kind === "external"
    ? { name: `e2e-${index}`, kind, workflow: "cnp-verify.yml", timeoutMinutes: 30, prodSafe: false }
    : {
        name: `metriques-${index}`,
        kind,
        windowMinutes: 15,
        minRequests: 50,
        errorRateMax: 1,
        p95Ms: null,
        onInconclusive: "ask",
        prodSafe: true,
      };
}

function NumberField({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  disabled: boolean;
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Input
        id={id}
        type="number"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        disabled={disabled}
      />
    </div>
  );
}

export function PromotionPolicyCard({
  deploymentId,
  policy,
  canAdminister,
  onSaved,
}: {
  deploymentId: number;
  policy: PromotionPolicy;
  canAdminister: boolean;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<PromotionPolicy>(policy);
  const [saving, setSaving] = useState(false);
  const locked = !canAdminister || saving;

  const setSuite = (index: number, patch: Partial<SuiteSpec>) =>
    setDraft((d) => ({ ...d, suites: d.suites.map((s, i) => (i === index ? { ...s, ...patch } : s)) }));

  const save = async () => {
    setSaving(true);
    try {
      await updatePromotionPolicy(deploymentId, draft);
      toast.success("Politique de promotion enregistrée");
      onSaved();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ListChecks className="h-4 w-4 text-muted-foreground" />
          Étapes de promotion
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <p className="text-muted-foreground">
          Une promotion passe toujours par le smoke de la plateforme sur staging, puis par les
          suites choisies ici, dans cet ordre. Un résultat ne vaut que pour la version testée et
          reste valable 7 jours.
        </p>

        <ol className="space-y-3">
          <li className="rounded-md border px-3 py-2 text-muted-foreground">
            1. <span className="font-medium text-foreground">smoke</span> · plateforme, toujours
          </li>
          {draft.suites.map((suite, index) => (
            <li key={index} className="space-y-3 rounded-md border px-3 py-3">
              <div className="flex flex-wrap items-end gap-3">
                <span className="pb-2 text-muted-foreground">{index + 2}.</span>
                <div className="min-w-0 flex-1 space-y-1">
                  <Label htmlFor={`suite-name-${index}`} className="text-xs">
                    Nom
                  </Label>
                  <Input
                    id={`suite-name-${index}`}
                    value={suite.name}
                    onChange={(e) => setSuite(index, { name: e.target.value })}
                    disabled={locked}
                  />
                </div>
                <span className="pb-2 text-xs text-muted-foreground">
                  {suite.kind === "external" ? "Workflow GitHub" : "Métriques Envoy"}
                </span>
                {canAdminister && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Retirer ${suite.name}`}
                    onClick={() =>
                      setDraft((d) => ({ ...d, suites: d.suites.filter((_, i) => i !== index) }))
                    }
                    disabled={locked}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
              {suite.kind === "external" ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor={`suite-workflow-${index}`} className="text-xs">
                      Fichier du workflow
                    </Label>
                    <Input
                      id={`suite-workflow-${index}`}
                      className="font-mono"
                      value={suite.workflow ?? ""}
                      onChange={(e) => setSuite(index, { workflow: e.target.value })}
                      disabled={locked}
                    />
                  </div>
                  <NumberField
                    id={`suite-timeout-${index}`}
                    label="Délai max (min)"
                    value={suite.timeoutMinutes}
                    onChange={(v) => setSuite(index, { timeoutMinutes: v ?? 30 })}
                    disabled={locked}
                  />
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-4">
                  <NumberField
                    id={`suite-window-${index}`}
                    label="Fenêtre (min)"
                    value={suite.windowMinutes}
                    onChange={(v) => setSuite(index, { windowMinutes: v ?? 15 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-min-${index}`}
                    label="Requêtes min."
                    value={suite.minRequests}
                    onChange={(v) => setSuite(index, { minRequests: v ?? 50 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-errors-${index}`}
                    label="Erreurs max (%)"
                    value={suite.errorRateMax}
                    onChange={(v) => setSuite(index, { errorRateMax: v ?? 1 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-p95-${index}`}
                    label="p95 max (ms)"
                    value={suite.p95Ms}
                    onChange={(v) => setSuite(index, { p95Ms: v })}
                    disabled={locked}
                  />
                  <div className="space-y-1 sm:col-span-2">
                    <Label className="text-xs">Si pas assez de trafic</Label>
                    <Select
                      value={suite.onInconclusive ?? "ask"}
                      onValueChange={(v) => setSuite(index, { onInconclusive: v as OnInconclusive })}
                      disabled={locked}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue>{(v) => INCONCLUSIVE[v as OnInconclusive]}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(INCONCLUSIVE).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <p className="text-xs text-muted-foreground sm:col-span-2">
                    Staging n&apos;a pas de trafic réel : lancez une suite de charge, la recette ou
                    le trafic de démo juste avant.
                  </p>
                </div>
              )}
            </li>
          ))}
        </ol>

        {canAdminister && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setDraft((d) => ({ ...d, suites: [...d.suites, newSuite("external", d.suites.length + 1)] }))
              }
              disabled={locked || draft.suites.length >= 10}
            >
              <Plus className="mr-1 h-4 w-4" /> Workflow GitHub
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setDraft((d) => ({ ...d, suites: [...d.suites, newSuite("metrics", d.suites.length + 1)] }))
              }
              disabled={locked || draft.suites.length >= 10}
            >
              <Plus className="mr-1 h-4 w-4" /> Métriques
            </Button>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField
            id="policy-soak"
            label="Temps en staging éveillé (min, 0 = aucun)"
            value={draft.soakMinutes}
            onChange={(v) => setDraft((d) => ({ ...d, soakMinutes: v ?? 0 }))}
            disabled={locked}
          />
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="policy-approval">Approbation d&apos;un admin</Label>
            <Switch
              id="policy-approval"
              checked={draft.requireApproval}
              onCheckedChange={(requireApproval) => setDraft((d) => ({ ...d, requireApproval }))}
              disabled={locked}
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="policy-auto">Promotion automatique</Label>
            <Switch
              id="policy-auto"
              checked={draft.autoPromote}
              onCheckedChange={(autoPromote) => setDraft((d) => ({ ...d, autoPromote }))}
              disabled={locked}
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Sans le rôle admin, lancer une promotion revient à la demander : une approbation par un
          autre admin est ajoutée. La promotion automatique démarre dès qu&apos;une nouvelle version
          tourne en staging et s&apos;arrête à l&apos;approbation s&apos;il y en a une.
        </p>

        {canAdminister && (
          <Button onClick={save} disabled={saving}>
            <Save className="mr-2 h-4 w-4" />
            Enregistrer
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
