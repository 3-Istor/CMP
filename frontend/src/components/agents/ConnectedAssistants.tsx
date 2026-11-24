"use client";

import { ChevronRight, Loader2, Unplug } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { cn } from "@/lib/utils";
import type { AgentGrant } from "@/types";

interface ClientGroup {
  name: string;
  grants: AgentGrant[];
  lastUsed: string | null;
}

/** One row per client: reconnecting the same client opens a new session each time. */
function groupByClient(grants: AgentGrant[]): ClientGroup[] {
  const groups = new Map<string, AgentGrant[]>();
  for (const grant of grants) {
    groups.set(grant.client_name, [...(groups.get(grant.client_name) ?? []), grant]);
  }
  return [...groups.entries()]
    .map(([name, list]) => {
      const sorted = [...list].sort((a, b) =>
        (b.last_used_at ?? b.created_at).localeCompare(a.last_used_at ?? a.created_at),
      );
      const used = list
        .map((g) => g.last_used_at)
        .filter((at): at is string => at !== null)
        .sort();
      return { name, grants: sorted, lastUsed: used.at(-1) ?? null };
    })
    .sort((a, b) => (b.lastUsed ?? "").localeCompare(a.lastUsed ?? ""));
}

export function ConnectedAssistants() {
  const t = useTranslations("agents");
  const locale = useLocale();
  const [grants, setGrants] = useState<AgentGrant[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const when = (iso: string | null): string =>
    iso ? new Date(iso).toLocaleString(locale) : t("never");
  const scopeOf = (grant: AgentGrant) =>
    grant.scopes.includes("cnp:write") ? t("scopes.readWrite") : t("scopes.readOnly");

  const load = useCallback(() => {
    getAgentGrants()
      .then(setGrants)
      .catch(() => setGrants([]));
  }, []);
  useEffect(load, [load]);

  const clients = useMemo(() => groupByClient(grants ?? []), [grants]);

  const disconnect = async (client: ClientGroup) => {
    setBusy(client.name);
    const results = await Promise.allSettled(
      client.grants.map((grant) => revokeAgentGrant(grant.id)),
    );
    if (results.every((r) => r.status === "fulfilled")) {
      toast.success(t("connected.revoked", { name: client.name }));
    } else {
      toast.error(t("connected.revokeFailed"));
    }
    setBusy(null);
    load();
  };

  const toggle = (name: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("connected.title")}</CardTitle>
        <CardDescription>
          {t("connected.description")}{" "}
          <Link href="/connect-ai" className="text-primary hover:underline">
            {t("connected.connectOne")}
          </Link>
        </CardDescription>
      </CardHeader>
      <CardContent>
        {grants === null ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : clients.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("connected.empty")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {clients.map((client) => {
              const expanded = open.has(client.name);
              return (
                <li key={client.name} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={() => toggle(client.name)}
                      aria-expanded={expanded}
                      className="flex min-w-0 flex-1 items-start gap-2 text-left"
                    >
                      <ChevronRight
                        className={cn(
                          "mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                          expanded && "rotate-90",
                        )}
                      />
                      <span className="min-w-0">
                        <span className="block font-medium">{client.name}</span>
                        <span className="block text-xs text-muted-foreground">
                          {t("connected.clientMeta", {
                            count: client.grants.length,
                            lastUsed: when(client.lastUsed),
                          })}
                        </span>
                      </span>
                    </button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy === client.name}
                      onClick={() => disconnect(client)}
                    >
                      {busy === client.name ? (
                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Unplug className="mr-2 h-3.5 w-3.5" />
                      )}
                      {t("connected.disconnect")}
                    </Button>
                  </div>
                  {expanded && (
                    <ul className="mt-2 space-y-1 pl-6 text-xs text-muted-foreground">
                      {client.grants.map((grant) => (
                        <li key={grant.id}>
                          {t("connected.meta", {
                            scope: scopeOf(grant),
                            created: when(grant.created_at),
                            lastUsed: when(grant.last_used_at),
                          })}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
