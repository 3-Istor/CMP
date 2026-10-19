"use client";

import { Eye, Loader2, PenLine } from "lucide-react";
import { useSession } from "next-auth/react";
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
      .catch(() =>
        setError(
          "This connection request expired. Start the connection again from your assistant.",
        ),
      );
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
      setError("Something went wrong. Start the connection again from your assistant.");
      setBusy(false);
    }
  };

  const name = session?.user?.name ?? session?.user?.email ?? "you";
  const problem =
    error ??
    (requestId
      ? null
      : "This link is incomplete. Start the connection again from your assistant.");

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
            {request.client_name} wants to use CNP as {name}
          </h1>
          <p className="mt-2 text-center text-sm text-muted-foreground">
            It will act with your rights, in the projects you can access. You
            can disconnect it at any time from your account.
          </p>

          <ul className="mt-6 space-y-3 text-sm">
            <li className="flex items-start gap-3 rounded-lg border border-border p-3">
              <Eye className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <p className="font-medium">See your projects and apps</p>
                <p className="text-muted-foreground">
                  Status, logs, releases, costs.
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3 rounded-lg border border-border p-3">
              <PenLine className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div className="flex-1">
                <p className="font-medium">Create and change apps</p>
                <p className="text-muted-foreground">
                  Create apps, push code, release. Sensitive actions still ask
                  you, depending on the project.
                </p>
              </div>
              <Switch
                checked={allowWrite}
                onCheckedChange={setAllowWrite}
                aria-label="Allow creating and changing apps"
              />
            </li>
          </ul>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            You will be sent back to {request.redirect_host}.
          </p>

          <div className="mt-6 grid grid-cols-2 gap-3">
            <Button variant="outline" disabled={busy} onClick={() => decide(false)}>
              Decline
            </Button>
            <Button disabled={busy} onClick={() => decide(true)}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Allow
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
