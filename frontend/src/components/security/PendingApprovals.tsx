"use client";

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

export function PendingApprovals({ username }: { username: string }) {
  const t = useTranslations("security");
  const text = useFindingText();
  const [items, setItems] = useState<SecurityException[]>([]);
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

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("approvals.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("approvals.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="pb-2 pr-3 font-medium">{t("approvals.project")}</th>
                  <th className="pb-2 pr-3 font-medium">{t("approvals.finding")}</th>
                  <th className="hidden pb-2 pr-3 font-medium sm:table-cell">{t("approvals.by")}</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.id} className="border-t">
                    <td className="py-2 pr-3">
                      <Link
                        href={`/security?project=${encodeURIComponent(e.project ?? "")}&tab=ignored`}
                        className="font-medium hover:underline"
                      >
                        {e.project}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      {text(e, "title") || e.rule}
                      <span className="text-muted-foreground">
                        {" "}
                        · {t(`exceptionStatus.${e.status}`)}
                      </span>
                    </td>
                    <td className="hidden py-2 pr-3 sm:table-cell">{e.author}</td>
                    <td className="py-2">
                      <div className="flex flex-wrap justify-end gap-2">
                        {e.author !== username && (
                          <Button
                            size="sm"
                            disabled={busy === e.id}
                            onClick={() =>
                              run(e.id, () => approveSecurityException(e.id), t("exceptions.approved"))
                            }
                          >
                            {busy === e.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            {t("exceptions.approve")}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy === e.id}
                          onClick={() =>
                            run(e.id, () => revokeSecurityException(e.id), t("approvals.rejected"))
                          }
                        >
                          {t("approvals.reject")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
