"use client";

import { Loader2, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { decideAgentConfirmation, getAgentConfirmation } from "@/lib/api";
import type { AgentConfirmation } from "@/types";

import { Mascot } from "./Mascot";

const DONE: Record<string, string> = {
  approved: "Approved. Go back to your assistant and tell it to continue.",
  denied: "Declined. The assistant will not do it.",
  expired: "This request expired. Ask your assistant again if still needed.",
  used: "Already done.",
};

export function ConfirmationPanel({
  confirmationId,
}: {
  confirmationId: string;
}) {
  const [item, setItem] = useState<AgentConfirmation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getAgentConfirmation(confirmationId)
      .then(setItem)
      .catch(() =>
        setError("This request does not exist or is not in one of your projects."),
      );
  }, [confirmationId]);

  const decide = async (decision: "approve" | "deny") => {
    setBusy(true);
    try {
      setItem(await decideAgentConfirmation(confirmationId, decision));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record your decision.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full max-w-md rounded-2xl border border-border bg-card p-7 shadow-xl shadow-primary/5">
      <Mascot
        mood={item?.status === "approved" ? "happy" : "idle"}
        className="mx-auto h-16 w-20"
      />
      {error ? (
        <p className="mt-5 text-center text-sm text-muted-foreground">{error}</p>
      ) : !item ? (
        <div className="mt-6 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          <div className="mt-5 flex items-center justify-center gap-2 text-xs font-medium text-amber-600 dark:text-amber-400">
            <ShieldAlert className="h-4 w-4" />
            Critical project · needs a person
          </div>
          <h1 className="mt-2 text-center font-heading text-xl font-semibold text-balance">
            {item.summary}
          </h1>
          <dl className="mt-5 grid grid-cols-[7rem_1fr] gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">Project</dt>
            <dd>
              <Link
                href={`/projects/${item.project}`}
                className="text-primary hover:underline"
              >
                {item.project}
              </Link>
            </dd>
            <dt className="text-muted-foreground">Asked by</dt>
            <dd>{item.username}, through an assistant</dd>
            <dt className="text-muted-foreground">Action</dt>
            <dd className="font-mono text-xs leading-5">{item.tool}</dd>
            <dt className="text-muted-foreground">Expires</dt>
            <dd>{new Date(item.expires_at).toLocaleTimeString()}</dd>
          </dl>

          {item.status === "pending" ? (
            <div className="mt-6 grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => decide("deny")}
              >
                Decline
              </Button>
              <Button disabled={busy} onClick={() => decide("approve")}>
                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Approve
              </Button>
            </div>
          ) : (
            <p className="mt-6 rounded-lg bg-muted px-4 py-3 text-center text-sm">
              {DONE[item.status] ?? item.status}
              {item.decided_by && item.status !== "expired"
                ? ` (${item.decided_by})`
                : ""}
            </p>
          )}
        </>
      )}
    </div>
  );
}
