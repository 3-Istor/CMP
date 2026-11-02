"use client";
import { Eye, Loader2, PenLine } from "lucide-react";
import { useSession } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  approveAgentAuthRequest,
  denyAgentAuthRequest,
  getAgentAuthRequest,
} from "@/lib/api";
import type { AgentAuthRequest } from "@/types";

import { Mascot } from "./Mascot";

const WRITE = "cnp:write";

export function ConsentPanel({ requestId }: { requestId: string }) {
  const t = useTranslations("agents.consent");
  const { data: session } = useSession();
  const [request, setRequest] = useState<AgentAuthRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allowWrite, setAllowWrite] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!requestId) return;
    getAgentAuthRequest(requestId)
      .then((r) => {
        setRequest(r);
        setAllowWrite(r.scopes.includes(WRITE));
      })
      .catch(() => setError(t("expired")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      const { redirect_url } = approve
        ? await approveAgentAuthRequest(
            requestId,
            allowWrite ? ["cnp:read", WRITE] : ["cnp:read"],
          )
        : await denyAgentAuthRequest(requestId);
      window.location.assign(redirect_url);
    } catch {
      setError(t("failed"));
      setBusy(false);
    }
  };

  const name = session?.user?.name ?? session?.user?.email ?? t("you");
  const problem =
    error ?? (requestId ? null : t("incomplete"));

  return (
    <div className="w-full max-w-md rounded-2xl border border-border bg-card p-7 shadow-xl shadow-primary/5">
      <Mascot mood={problem ? "idle" : "happy"} className="mx-auto h-16 w-20" />
      {problem ? (
        <p className="mt-5 text-center text-sm text-muted-foreground">{problem}</p>
      ) : !request ? (
        <div className="mt-6 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          <h1 className="mt-5 text-center font-heading text-xl font-semibold text-balance">
            {t("title", { client: request.client_name, name })}
          </h1>
          <p className="mt-2 text-center text-sm text-muted-foreground">
            {t("body")}
          </p>
          <ul className="mt-6 space-y-3 text-sm">
            <li className="flex items-start gap-3 rounded-lg border border-border p-3">
              <Eye className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <p className="font-medium">{t("seeTitle")}</p>
                <p className="text-muted-foreground">{t("seeText")}</p>
              </div>
            </li>
            <li className="flex items-start gap-3 rounded-lg border border-border p-3">
              <PenLine className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div className="flex-1">
                <p className="font-medium">{t("writeTitle")}</p>
                <p className="text-muted-foreground">{t("writeText")}</p>
              </div>
              <Switch
                checked={allowWrite}
                onCheckedChange={setAllowWrite}
                aria-label={t("writeSwitch")}
              />
            </li>
          </ul>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            {t("redirect", { host: request.redirect_host })}
          </p>
          <div className="mt-6 grid grid-cols-2 gap-3">
            <Button variant="outline" disabled={busy} onClick={() => decide(false)}>
              {t("decline")}
            </Button>
            <Button disabled={busy} onClick={() => decide(true)}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("allow")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
