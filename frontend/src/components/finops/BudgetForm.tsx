"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { putBudget } from "@/lib/api";
import type { Budget } from "@/types";
import { Loader2, Save } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

/**
 * Formulaire d'enveloppe budgétaire mensuelle + seuils d'alerte.
 * Réservé au Project Owner (le backend renvoie 403 sinon).
 */
export function BudgetForm({
  projectName,
  budget,
  canEdit,
  onSaved,
}: {
  projectName: string;
  budget: Budget | null;
  canEdit: boolean;
  onSaved?: (b: Budget) => void;
}) {
  const [amount, setAmount] = useState(
    budget?.monthly_amount_eur ? String(budget.monthly_amount_eur) : "",
  );
  const [warn, setWarn] = useState(String(budget?.threshold_warn ?? 70));
  const [critical, setCritical] = useState(
    String(budget?.threshold_critical ?? 90),
  );
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    const amountNum = parseFloat(amount);
    const warnNum = parseInt(warn, 10);
    const criticalNum = parseInt(critical, 10);
    if (isNaN(amountNum) || amountNum < 0) {
      toast.error("Montant invalide.");
      return;
    }
    if (warnNum >= criticalNum) {
      toast.error("Le seuil d'attention doit être inférieur au seuil critique.");
      return;
    }
    setSaving(true);
    try {
      const saved = await putBudget(projectName, {
        monthly_amount_eur: amountNum,
        threshold_warn: warnNum,
        threshold_critical: criticalNum,
      });
      toast.success("Budget enregistré.");
      onSaved?.(saved);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Échec de l'enregistrement";
      toast.error(
        msg.includes("403") || msg.toLowerCase().includes("owner")
          ? "Seul le Project Owner peut modifier le budget."
          : msg,
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="budget-amount">Enveloppe mensuelle (EUR)</Label>
        <Input
          id="budget-amount"
          type="number"
          min={0}
          step="1"
          value={amount}
          disabled={!canEdit}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="ex : 500"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="budget-warn">Seuil d&apos;attention (%)</Label>
          <Input
            id="budget-warn"
            type="number"
            min={1}
            max={100}
            value={warn}
            disabled={!canEdit}
            onChange={(e) => setWarn(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="budget-critical">Seuil critique (%)</Label>
          <Input
            id="budget-critical"
            type="number"
            min={1}
            max={100}
            value={critical}
            disabled={!canEdit}
            onChange={(e) => setCritical(e.target.value)}
          />
        </div>
      </div>

      {canEdit ? (
        <Button onClick={handleSave} disabled={saving}>
          {saving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          Enregistrer le budget
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">
          Seul le <span className="font-medium">Project Owner</span> peut définir
          le budget de ce projet.
        </p>
      )}
    </div>
  );
}
