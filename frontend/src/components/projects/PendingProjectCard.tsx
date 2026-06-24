"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { FolderKanban, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

interface Props {
  name: string;
}

/**
 * Placeholder card shown in the projects grid while a freshly created project
 * is still being bootstrapped (Keycloak groups, Vault policy, ArgoCD).
 *
 * Mirrors the AppCard loading treatment: the icon appears immediately in the
 * list with an animated progress bar, so the user gets feedback during the
 * ~30s bootstrap instead of an empty screen.
 */
export function PendingProjectCard({ name }: Props) {
  // Fake progress that climbs toward ~90% and stops; it completes once the
  // real project shows up in the list and this card is unmounted.
  const [progress, setProgress] = useState(8);

  useEffect(() => {
    const timer = setInterval(() => {
      setProgress((p) => (p >= 90 ? 90 : p + Math.max(1, Math.round((90 - p) / 12))));
    }, 1200);
    return () => clearInterval(timer);
  }, []);

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
