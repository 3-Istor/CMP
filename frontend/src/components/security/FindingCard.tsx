"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { requestSecurityBackup } from "@/lib/api";
import type { SecurityAppSummary, SecurityFinding } from "@/types";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  ShieldOff,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage, TIER_META } from "./labels";

const BACKUP_NOW_RULES = new Set(["backup-stale", "backup-missing"]);
const SETTINGS_RULES: Record<string, string> = {
  "backup-disabled": "Activer",
  "exposure-public": "Restreindre",
  "exposure-custom": "Régler",
};

export function FindingCard({
  finding,
  project,
  apps,
  canAdmin,
  onIgnore,
}: {
  finding: SecurityFinding;
  project: string;
  apps: SecurityAppSummary[];
  canAdmin: boolean;
  onIgnore?: (finding: SecurityFinding) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const tier = TIER_META[finding.tier];
  const deploymentId = apps.find((a) => a.app === finding.app)?.deployment_id;
  const settingsLabel = SETTINGS_RULES[finding.rule];
  const pending = finding.exception?.pending_approval;

  async function backupNow() {
    if (!finding.app) return;
    setBusy(true);
    try {
      const { backups } = await requestSecurityBackup(project, finding.app);
      toast.success(`Sauvegarde lancée : ${backups.join(", ")}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-start gap-3 p-3">
        <Badge variant="outline" className={`shrink-0 ${tier.badge}`}>
          {tier.label}
        </Badge>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium leading-tight">{finding.title}</p>
          <p className="text-xs text-muted-foreground">
            {[finding.app ?? "projet", finding.location]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {finding.fix && <p className="text-sm">{finding.fix}</p>}
          {pending && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              Ignorée par {finding.exception?.author}, en attente de validation
              par un admin du projet.
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {finding.link && (
            <a
              href={finding.link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              {finding.sources.includes("ci") ? "Voir le run" : "Détails"}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {BACKUP_NOW_RULES.has(finding.rule) && canAdmin && (
            <Button size="sm" variant="outline" onClick={backupNow} disabled={busy}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Sauvegarder maintenant
            </Button>
          )}
          {settingsLabel && deploymentId && (
            <Link
              href={`/projects/${encodeURIComponent(project)}/apps/${deploymentId}#security-data`}
              className="inline-flex h-7 items-center rounded-md border px-2.5 text-[0.8rem] font-medium hover:bg-accent"
            >
              {settingsLabel}
            </Link>
          )}
          {onIgnore && !finding.exception && (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => onIgnore(finding)}
            >
              <ShieldOff className="h-3.5 w-3.5" />
              Ignorer
            </Button>
          )}
        </div>
      </div>
      {(finding.detail || finding.raw) && (
        <div className="border-t px-3 py-1.5">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            aria-expanded={open}
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Détails
          </button>
          {open && (
            <div className="space-y-2 pb-2 pt-1 text-xs">
              {finding.detail && <p>{finding.detail}</p>}
              {finding.raw && (
                <pre className="whitespace-pre-wrap rounded bg-muted p-2 font-mono text-[11px] text-muted-foreground">
                  {finding.raw}
                </pre>
              )}
              <p className="text-muted-foreground">
                Vu par {finding.sources.join(", ")} depuis le{" "}
                {new Date(`${finding.first_seen}Z`).toLocaleDateString("fr-FR")}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
