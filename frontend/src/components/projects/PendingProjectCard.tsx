"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { FolderKanban, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

interface Props {
  name: string;
  /** Epoch ms when creation started; used to resume progress after a reload. */
  createdAt: number;
}

// Asymptotic curve toward 90%: starts at 8% and eases up over the bootstrap.
// Derived purely from elapsed time so it resumes correctly across a reload
// instead of restarting at zero.
const START = 8;
const CEILING = 90;
const TAU_MS = 14000;

function progressFor(createdAt: number): number {
  const elapsed = Math.max(0, Date.now() - createdAt);
  const value = CEILING - (CEILING - START) * Math.exp(-elapsed / TAU_MS);
  return Math.min(CEILING, Math.round(value));
}

/**
 * Placeholder card shown in the projects grid while a freshly created project
 * is still being bootstrapped (Keycloak groups, Vault policy, ArgoCD).
 *
 * Mirrors the AppCard loading treatment: the icon appears immediately in the
 * list with an animated progress bar, so the user gets feedback during the
 * ~30s bootstrap instead of an empty screen.
 */
export function PendingProjectCard({ name, createdAt }: Props) {
  // Fake progress that climbs toward ~90% and stops; it completes once the
  // real project shows up in the list and this card is unmounted.
  const [progress, setProgress] = useState(() => progressFor(createdAt));

  useEffect(() => {
    const timer = setInterval(() => {
      setProgress(progressFor(createdAt));
    }, 1000);
    return () => clearInterval(timer);
  }, [createdAt]);

  return (
    <Card className="h-full border-primary/30">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="rounded-lg bg-primary/10 p-2.5">
            <FolderKanban className="h-5 w-5 text-primary" />
          </div>
          <Badge variant="outline" className="shrink-0 gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Creating
          </Badge>
        </div>
        <p className="text-base font-semibold mt-2 capitalize truncate">{name}</p>
        <p className="text-xs text-muted-foreground">Bootstrapping project…</p>
      </CardHeader>
      <CardContent className="pt-0 space-y-1.5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="animate-pulse">Keycloak · Vault · ArgoCD</span>
          <span className="tabular-nums font-mono">{progress}%</span>
        </div>
        <Progress value={progress} className="h-1.5" />
      </CardContent>
    </Card>
  );
}
