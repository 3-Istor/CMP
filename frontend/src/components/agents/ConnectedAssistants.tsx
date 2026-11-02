"use client";

import { Loader2, Unplug } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
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

export function ConnectedAssistants() {
  const t = useTranslations("agents");
  const locale = useLocale();
  const [grants, setGrants] = useState<AgentGrant[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const when = (iso: string | null): string =>
    iso ? new Date(iso).toLocaleString(locale) : t("never");

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
      toast.success(t("connected.revoked", { name: grant.client_name }));
      load();
    } catch {
      toast.error(t("connected.revokeFailed"));
    } finally {
      setBusy(null);
    }
  };

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
        ) : grants.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("connected.empty")}
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
                    {t("connected.meta", {
                      scope: grant.scopes.includes("cnp:write")
                        ? t("scopes.readWrite")
                        : t("scopes.readOnly"),
                      created: when(grant.created_at),
                      lastUsed: when(grant.last_used_at),
                    })}
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
                  {t("connected.disconnect")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
