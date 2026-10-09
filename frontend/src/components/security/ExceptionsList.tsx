"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { approveSecurityException, revokeSecurityException } from "@/lib/api";
import type { SecurityException } from "@/types";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  errorMessage,
  EXCEPTION_STATUS_LABELS,
  JUSTIFICATION_LABELS,
} from "./labels";

function date(iso: string): string {
  return new Date(iso.length === 10 ? `${iso}T00:00:00Z` : `${iso}Z`).toLocaleDateString(
    "fr-FR",
  );
}

export function ExceptionsList({
  exceptions,
  canAdmin,
  username,
  onChanged,
}: {
  exceptions: SecurityException[];
  canAdmin: boolean;
  username: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);

  async function run(id: number, action: () => Promise<unknown>, done: string) {
    setBusy(id);
    try {
      await action();
      toast.success(done);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (exceptions.length === 0) {
    return <p className="text-sm text-muted-foreground">Aucune alerte ignorée.</p>;
  }

  return (
    <ul className="space-y-2">
      {exceptions.map((e) => (
        <li key={e.id} className="rounded-lg border bg-card p-3 text-sm">
          <div className="flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <p className="font-medium">
                {e.rule}
                <span className="font-normal text-muted-foreground">
                  {" "}
                  · {e.app ?? "projet"}
                </span>
              </p>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline">{EXCEPTION_STATUS_LABELS[e.status]}</Badge>
                {e.justification && (
                  <Badge variant="outline">{JUSTIFICATION_LABELS[e.justification]}</Badge>
                )}
                {e.pending_approval && (
                  <Badge
                    variant="outline"
                    className="border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                  >
                    En attente de validation
                  </Badge>
                )}
              </div>
              <p>{e.statement}</p>
              <p className="text-xs text-muted-foreground">
                Par {e.author} le {date(e.created_at)}
                {e.approved_by && `, validée par ${e.approved_by}`} · expire le{" "}
                {date(e.expires_on)}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              {e.pending_approval && canAdmin && e.author !== username && (
                <Button
                  size="sm"
                  disabled={busy === e.id}
                  onClick={() =>
                    run(e.id, () => approveSecurityException(e.id), "Exception validée")
                  }
                >
                  {busy === e.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Valider
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={busy === e.id}
                onClick={() =>
                  run(e.id, () => revokeSecurityException(e.id), "Alerte réactivée")
                }
              >
                Réactiver
              </Button>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
