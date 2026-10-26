"use client";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  closeFinopsMonth,
  downloadFinopsCsv,
  downloadFinopsFile,
  putFinopsCostCenters,
  type FinopsFileKind,
} from "@/lib/api";
import { useFinopsFinance, useFinopsMeta } from "@/lib/hooks";
import type { FinopsCostCenter } from "@/types";
import {
  AlertCircle,
  Download,
  FileText,
  Loader2,
  Lock,
  Save,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

function previousMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function errorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : "Erreur inconnue";
  try {
    const detail = JSON.parse(raw)?.detail;
    if (typeof detail === "string") return detail;
  } catch {
    // Pas du JSON: le message brut suffit.
  }
  return raw;
}

export default function FinopsFinancePage() {
  const role = useFinopsMeta()?.role;
  const { closes, centers, error, refresh } = useFinopsFinance();
  const [period, setPeriod] = useState(previousMonth);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [needsForce, setNeedsForce] = useState<string | null>(null);
  const [draft, setDraft] = useState<FinopsCostCenter[]>([]);

  useEffect(() => setDraft(centers), [centers]);

  const closedPeriods = new Set(closes.map((c) => c.billing_period));
  const alreadyClosed = closedPeriods.has(period);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const download = (kind: FinopsFileKind, project?: string) =>
    run(`${kind}:${project ?? ""}`, () =>
      downloadFinopsFile(kind, period, project),
    );

  const downloadCsv = () =>
    run("csv", () => downloadFinopsCsv({ period }));

  const close = (force: boolean) =>
    run("close", async () => {
      try {
        await closeFinopsMonth(period, force);
      } catch (err) {
        if (!force && (err as { status?: number }).status === 409) {
          setNeedsForce(errorText(err));
          return;
        }
        throw err;
      }
      toast.success(`Mois ${period} clôturé.`);
      setConfirming(false);
      setNeedsForce(null);
      await refresh();
    });

  const saveCenters = () =>
    run("centers", async () => {
      await putFinopsCostCenters(draft);
      toast.success("Centres de coûts enregistrés.");
      await refresh();
    });

  const edit = (
    project: string,
    field: "cost_center" | "budget_owner",
    value: string,
  ) =>
    setDraft((rows) =>
      rows.map((r) => (r.project_name === project ? { ...r, [field]: value } : r)),
    );

  if (role && role !== "admin" && role !== "finance") {
    return (
      <div className="flex items-center gap-2 rounded-lg border px-4 py-3 text-sm text-muted-foreground">
        <Lock className="h-4 w-4" />
        Cet onglet est réservé à la finance et aux admins CNP.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Exports du mois</CardTitle>
          <p className="text-xs text-muted-foreground">
            Électricité on-prem en TTC, factures cloud en HT: la colonne Base de
            l&apos;export le précise.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="month"
              aria-label="Mois"
              value={period}
              max={previousMonth()}
              onChange={(e) => {
                setPeriod(e.target.value);
                setConfirming(false);
                setNeedsForce(null);
              }}
              className="w-44"
            />
            <Button
              variant="outline"
              disabled={!period || busy !== null}
              onClick={() => download("xlsx")}
            >
              <Download className="h-4 w-4" /> Export XLSX
            </Button>
            <Button
              variant="outline"
              disabled={!period || busy !== null}
              onClick={() => download("parquet")}
            >
              <Download className="h-4 w-4" /> Export Parquet
            </Button>
            <Button
              variant="outline"
              disabled={!period || busy !== null}
              onClick={downloadCsv}
            >
              <Download className="h-4 w-4" /> Export CSV
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Relevés par projet</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {centers.length === 0 && (
            <span className="text-sm text-muted-foreground">Aucun projet.</span>
          )}
          {centers.map((c) => (
            <Button
              key={c.project_name}
              variant="outline"
              size="sm"
              disabled={!period || busy !== null}
              onClick={() => download("statement", c.project_name)}
            >
              <FileText className="h-4 w-4" /> {c.project_name}
            </Button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Clôture de {period || "..."}</CardTitle>
          <p className="text-xs text-muted-foreground">
            Un mois clôturé n&apos;est plus modifié: une révision tardive devient
            une ligne d&apos;ajustement sur le premier mois ouvert.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {alreadyClosed ? (
            <p className="text-sm text-muted-foreground">
              Ce mois est déjà clôturé.
            </p>
          ) : !confirming ? (
            <Button
              variant="outline"
              disabled={!period || busy !== null}
              onClick={() => setConfirming(true)}
            >
              <Lock className="h-4 w-4" /> Clôturer ce mois
            </Button>
          ) : (
            <div className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">
              <p>
                Clôturer {period} ? Cette action ne peut pas être annulée depuis
                le CMP.
              </p>
              {needsForce && (
                <p className="text-destructive">
                  {needsForce} Vous pouvez forcer la clôture.
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  disabled={busy !== null}
                  onClick={() => close(needsForce !== null)}
                >
                  {busy === "close" && <Loader2 className="h-4 w-4 animate-spin" />}
                  {needsForce ? "Forcer la clôture" : "Confirmer la clôture"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setConfirming(false);
                    setNeedsForce(null);
                  }}
                >
                  Annuler
                </Button>
              </div>
            </div>
          )}

          <div>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Mois clôturés
            </h3>
            {closes.length === 0 ? (
              <p className="text-sm text-muted-foreground">Aucun pour l&apos;instant.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {closes.map((c) => (
                  <li key={c.billing_period} className="tabular-nums">
                    <span className="font-medium">{c.billing_period}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      par {c.closed_by}
                      {c.closed_at
                        ? `, le ${new Date(c.closed_at).toLocaleDateString("fr-FR")}`
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Centres de coûts</CardTitle>
          <p className="text-xs text-muted-foreground">
            Un centre de coûts vide retire le rattachement. Repris dans l&apos;onglet
            Projets de l&apos;export XLSX.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-3 py-2 font-medium">Projet</th>
                  <th className="px-3 py-2 font-medium">Centre de coûts</th>
                  <th className="px-3 py-2 font-medium">Responsable budget</th>
                </tr>
              </thead>
              <tbody>
                {draft.map((r) => (
                  <tr key={r.project_name} className="border-b border-border/50">
                    <td className="px-3 py-2 font-medium">{r.project_name}</td>
                    <td className="px-3 py-2">
                      <Input
                        aria-label={`Centre de coûts de ${r.project_name}`}
                        value={r.cost_center ?? ""}
                        maxLength={64}
                        onChange={(e) =>
                          edit(r.project_name, "cost_center", e.target.value)
                        }
                      />
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        aria-label={`Responsable budget de ${r.project_name}`}
                        value={r.budget_owner ?? ""}
                        onChange={(e) =>
                          edit(r.project_name, "budget_owner", e.target.value)
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Button disabled={busy !== null || draft.length === 0} onClick={saveCenters}>
            {busy === "centers" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Enregistrer
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
