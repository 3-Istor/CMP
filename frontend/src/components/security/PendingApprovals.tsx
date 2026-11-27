"use client";

import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  approveSecurityException,
  getPendingSecurityExceptions,
  revokeSecurityException,
} from "@/lib/api";
import type { SecurityException } from "@/types";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { useFindingText } from "./findingText";
import { errorMessage } from "./labels";

/** Shown to platform admins and auditors; an auditor acts on criticals only. */
export function PendingApprovals({ username, cnpAdmin }: { username: string; cnpAdmin: boolean }) {
  const t = useTranslations("security");
  const text = useFindingText();
  const [items, setItems] = useState<SecurityException[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    getPendingSecurityExceptions()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);
  useEffect(load, [load]);

  async function run(id: number, action: () => Promise<unknown>, done: string) {
    setBusy(id);
    try {
      await action();
      toast.success(done);
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const columns: DataColumn<SecurityException>[] = [
    {
      key: "project",
      header: t("approvals.project"),
      sortable: true,
      cell: (e) => (
        <Link
          href={`/security?project=${encodeURIComponent(e.project ?? "")}&tab=ignored`}
          className="font-medium hover:underline"
        >
          {e.project}
        </Link>
      ),
    },
    {
      key: "finding",
      header: t("approvals.finding"),
      cell: (e) => (
        <>
          {text(e, "title") || e.rule}
          <span className="text-muted-foreground"> · {t(`exceptionStatus.${e.status}`)}</span>
        </>
      ),
    },
    {
      key: "author",
      header: t("approvals.by"),
      sortable: true,
      className: "hidden sm:table-cell",
      cell: (e) => e.author,
    },
    {
      key: "actions",
      header: <span className="sr-only">{t("approvals.actions")}</span>,
      cell: (e) => (cnpAdmin || e.critical) && (
        <div className="flex flex-wrap justify-end gap-2">
          {e.author !== username && (
            <Button
              size="sm"
              disabled={busy === e.id}
              onClick={() => run(e.id, () => approveSecurityException(e.id), t("exceptions.approved"))}
            >
              {busy === e.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("exceptions.approve")}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={busy === e.id}
            onClick={() => run(e.id, () => revokeSecurityException(e.id), t("approvals.rejected"))}
          >
            {t("approvals.reject")}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("approvals.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={items ?? []}
          rowKey={(e) => e.id}
          loading={items === null}
          storageKey="security.approvals"
          empty={{ title: t("approvals.empty") }}
        />
      </CardContent>
    </Card>
  );
}

/** Holds the card's place until the role is known, so the page below does not jump. */
export function PendingApprovalsPlaceholder() {
  const t = useTranslations("security");
  return (
    <Card aria-busy>
      <CardHeader>
        <CardTitle>{t("approvals.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-9 animate-pulse rounded bg-muted" />
      </CardContent>
    </Card>
  );
}
