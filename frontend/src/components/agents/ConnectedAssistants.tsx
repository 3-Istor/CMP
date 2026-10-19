"use client";

import { Loader2, Unplug } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getAgentGrants, revokeAgentGrant } from "@/lib/api";
import type { AgentGrant } from "@/types";

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never";
}

export function ConnectedAssistants() {
  const [grants, setGrants] = useState<AgentGrant[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    getAgentGrants()
      .then(setGrants)
      .catch(() => setGrants([]));
  }, []);
  useEffect(load, [load]);

  const revoke = async (grant: AgentGrant) => {
    setBusy(grant.id);
    try {
      await revokeAgentGrant(grant.id);
      toast.success(`${grant.client_name} disconnected`);
      load();
    } catch {
      toast.error("Could not disconnect the assistant. Try again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connected AI assistants</CardTitle>
        <CardDescription>
          Assistants that act on CNP with your rights. Disconnecting one cuts
          its access immediately.{" "}
          <Link href="/connect-ai" className="text-primary hover:underline">
            Connect one
          </Link>
        </CardDescription>
      </CardHeader>
      <CardContent>
        {grants === null ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : grants.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No assistant connected.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {grants.map((grant) => (
              <li
                key={grant.id}
                className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{grant.client_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {grant.scopes.includes("cnp:write")
                      ? "Read and write"
                      : "Read only"}{" "}
                    · connected {when(grant.created_at)} · last used{" "}
                    {when(grant.last_used_at)}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy === grant.id}
                  onClick={() => revoke(grant)}
                >
                  {busy === grant.id ? (
                    <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Unplug className="mr-2 h-3.5 w-3.5" />
                  )}
                  Disconnect
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
