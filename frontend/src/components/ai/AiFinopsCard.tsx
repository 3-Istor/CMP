"use client";

import { RESOURCE_META } from "@/components/finops/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
import { getProjectAiUsage } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { projectHref } from "@/components/layout/projectSections";
import { useAiFormat } from "./shared";

interface Row {
  project: string;
  name: string;
  kind: "agent" | "app" | "other";
  tokens: number;
  cost: number;
  refused: number;
}

/** FinOps "IA" category: tokens and cost per agent or app, from the gateway metrics. */
export function AiFinopsCard({ projects, period }: { projects: string[]; period: string }) {
  const t = useTranslations("ai.finops");
  const f = useAiFormat();
  const range = period === "7d" ? "7d" : "30d";
  const usage = useCardData(`ai-finops-${projects.join(",")}-${range}`, async () => {
    const answers = await Promise.allSettled(projects.map((p) => getProjectAiUsage(p, range)));
    return answers.flatMap((answer, i): Row[] =>
      answer.status === "fulfilled"
        ? answer.value.items.map((item) => ({
            project: projects[i],
            kind: item.kind,
            name: item.name || item.azp,
            tokens: item.tokens_in + item.tokens_out,
            cost: item.cost_eur,
            refused: item.refused_total,
          }))
        : [],
    );
  }, { enabled: projects.length > 0 });

  const rows = [...(usage.data ?? [])].sort((a, b) => b.cost - a.cost || b.tokens - a.tokens);
  if (!usage.loading && rows.length === 0) return null;
  const max = Math.max(...rows.map((r) => r.tokens), 1);
  const totalCost = rows.reduce((s, r) => s + r.cost, 0);
  const totalTokens = rows.reduce((s, r) => s + r.tokens, 0);

  const columns: DataColumn<Row>[] = [
    {
      key: "name",
      header: t("who"),
      sortable: true,
      cell: (row) => (
        <>
          {row.kind === "agent" ? (
            <Link href={`${projectHref(row.project, "ai")}/agents/${encodeURIComponent(row.name)}`} className="font-mono text-xs font-semibold hover:underline">
              {row.name}
            </Link>
          ) : (
            <span className="font-mono text-xs font-semibold">{row.name}</span>
          )}
          <span className="ml-2 text-xs text-muted-foreground">{t(row.kind)}</span>
        </>
      ),
    },
    {
      key: "project",
      header: t("project"),
      sortable: true,
      className: "hidden text-muted-foreground sm:table-cell",
      cell: (row) => row.project,
    },
    {
      key: "tokens",
      header: t("tokens"),
      sortable: true,
      className: "w-2/5",
      cell: (row) => (
        <div className="flex items-center gap-2">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
            <div className="h-full rounded-full" style={{ width: `${(row.tokens / max) * 100}%`, backgroundColor: RESOURCE_META.ai.color }} />
          </div>
          <span className="w-16 shrink-0 text-right text-xs tabular-nums">{f.tokens(row.tokens)}</span>
        </div>
      ),
    },
    {
      key: "cost",
      header: t("cost"),
      align: "right",
      sortable: true,
      cell: (row) => f.eur(row.cost),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Sparkles className="size-4" style={{ color: RESOURCE_META.ai.color }} aria-hidden />
          {t("title")}
          <span className="ml-auto text-sm font-normal tabular-nums text-muted-foreground">
            {t("total", { tokens: f.tokens(totalTokens), cost: f.eur(totalCost), days: range === "7d" ? 7 : 30 })}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable
          caption={t("caption")}
          columns={columns}
          rows={rows}
          rowKey={(row) => `${row.project}/${row.kind}/${row.name}`}
          loading={usage.loading && !usage.data}
          storageKey="finops.aiUsage"
          initialSort={{ key: "cost", direction: "desc" }}
        />
      </CardContent>
    </Card>
  );
}
