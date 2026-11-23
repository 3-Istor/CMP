"use client";

import { CLOUD_META } from "@/components/finops/format";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { ProjectProvider } from "@/components/projects/ProjectContext";
import { MoveButtons } from "@/components/projects/ProjectLocation";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getEmergency, type Emergency } from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { cn } from "@/lib/utils";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { CircleCheck, Shield, Siren, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

const cloudLabel = (cloud: string) =>
  CLOUD_META[cloud as keyof typeof CLOUD_META]?.label ?? cloud;

// Left stripe and pill colour per cluster state.
const STATE: Record<string, { stripe: string; pill: string }> = {
  down: { stripe: "border-l-destructive", pill: "bg-destructive/10 text-destructive" },
  degraded: {
    stripe: "border-l-amber-500",
    pill: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  },
  starting: {
    stripe: "border-l-sky-500",
    pill: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
  },
  unknown: { stripe: "border-l-muted-foreground", pill: "bg-muted text-muted-foreground" },
};
const stateStyle = (state: string) => STATE[state] ?? STATE.unknown;

function StatePill({ state }: { state: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 rounded-4xl px-2 text-xs font-medium",
        stateStyle(state).pill,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {state}
    </span>
  );
}

const MOVE_STATUS: Record<string, string> = {
  failed: "bg-destructive/10 text-destructive",
  cancelling: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  running: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
};

function Tile({
  href,
  label,
  value,
  tone,
}: {
  href: string;
  label: string;
  value: number;
  tone?: "bad" | "warn";
}) {
  return (
    <a
      href={href}
      className={cn(
        "flex min-w-36 flex-1 flex-col gap-0.5 rounded-xl border bg-card px-4 py-2.5 hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-ring",
        value > 0 && tone === "bad" && "border-destructive/60 text-destructive",
        value > 0 && tone === "warn" && "border-amber-500/60",
      )}
    >
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xl font-semibold tabular-nums">{value}</span>
    </a>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
      <CircleCheck aria-hidden className="size-4 text-emerald-600" />
      {children}
    </p>
  );
}

export default function EmergencyPage() {
  const t = useTranslations("emergency");
  const tSteps = useTranslations("projectSettings.location.steps");
  const isAdmin = usePlatformAdmin();
  const [data, setData] = useState<Emergency | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    const load = () =>
      getEmergency()
        .then((d) => {
          setData(d);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    void load();
    return pollWhileVisible(load, POLL_MS);
  }, [isAdmin]);

  const clusters = data?.clusters ?? [];
  const bad = clusters.filter((c) => c.state !== "up");
  const healthy = clusters.filter((c) => c.state === "up");
  const projects = data?.projects ?? [];
  const moves = data?.moves ?? [];
  const count = (status: string[]) => moves.filter((m) => status.includes(m.status)).length;

  return (
    <div className="min-h-screen">
      <PageHeader title={t("title")} icon={Siren} />
      <PageBody>
        {!isAdmin || !data ? null : (
          <div className="space-y-6">
            {failed && (
              <p role="alert" className="text-sm text-destructive">
                {t("loadFailed")}
              </p>
            )}

            <div className="flex flex-wrap gap-3">
              <Tile href="#clusters" label={t("clustersDown")} value={bad.length} tone="bad" />
              <Tile href="#act-now" label={t("projectsAffected")} value={projects.length} tone="bad" />
              <Tile
                href="#act-now"
                label={t("favoritesAffected")}
                value={projects.filter((p) => p.favorite).length}
                tone="bad"
              />
              <Tile href="#moves" label={t("movesActive")} value={count(["running", "cancelling"])} tone="warn" />
              <Tile href="#moves" label={t("movesFailed")} value={count(["failed"])} tone="bad" />
            </div>

            <section id="clusters" aria-labelledby="clusters-title" className="scroll-mt-6 space-y-2">
              <h2 id="clusters-title" className="text-sm font-semibold">
                {t("clustersTitle")}
              </h2>
              {bad.length === 0 ? (
                <Empty>{t("allUp")}</Empty>
              ) : (
                <div className="grid gap-3 sm:grid-cols-3">
                  {bad.map((c) => (
                    <Card key={c.cloud} className={cn("border-l-4", stateStyle(c.state).stripe)}>
                      <CardContent className="flex items-center justify-between gap-2">
                        <span className="font-semibold">{cloudLabel(c.cloud)}</span>
                        <StatePill state={c.state} />
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
              {healthy.length > 0 && bad.length > 0 && (
                <details className="rounded-xl border bg-card text-sm">
                  <summary className="cursor-pointer px-4 py-2 text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
                    {t("healthy")} · {healthy.length}
                  </summary>
                  <ul className="flex flex-wrap gap-3 px-4 pb-3">
                    {healthy.map((c) => (
                      <li key={c.cloud} className="flex items-center gap-2">
                        {cloudLabel(c.cloud)}
                        <StatePill state={c.state} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>

            <section id="act-now" aria-labelledby="act-now-title" className="scroll-mt-6 space-y-2">
              <h2 id="act-now-title" className="text-sm font-semibold">
                {t("actNow")}
              </h2>
              {projects.length === 0 ? (
                <Empty>{t("actNowEmpty")}</Empty>
              ) : (
                <ul className="flex flex-col gap-2">
                  {projects.map((p) => (
                    <li key={p.name}>
                      <Card className={cn("border-l-4", stateStyle(p.cloud_state).stripe)}>
                        <CardContent className="flex flex-wrap items-center justify-between gap-3">
                          <div className="flex min-w-0 flex-col gap-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <Link
                                href={`/projects/${encodeURIComponent(p.name)}`}
                                className="font-semibold hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                              >
                                {p.name}
                              </Link>
                              {p.favorite && (
                                <Badge>
                                  <Star aria-hidden className="fill-current" />
                                  {t("priority")}
                                </Badge>
                              )}
                              {p.protected && (
                                <Badge variant="outline">
                                  <Shield aria-hidden />
                                  {t("protected")}
                                </Badge>
                              )}
                              <Badge variant="outline">{cloudLabel(p.cloud)}</Badge>
                              <StatePill state={p.cloud_state} />
                            </span>
                            <span className="truncate text-xs text-muted-foreground">
                              {p.apps.length > 0 ? p.apps.join(", ") : t("apps", { count: 0 })}
                            </span>
                          </div>
                          <ProjectProvider projectName={p.name}>
                            <MoveButtons />
                          </ProjectProvider>
                        </CardContent>
                      </Card>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section id="moves" aria-labelledby="moves-title" className="scroll-mt-6 space-y-2">
              <h2 id="moves-title" className="text-sm font-semibold">
                {t("movesTitle")}
              </h2>
              {moves.length === 0 ? (
                <Empty>{t("movesEmpty")}</Empty>
              ) : (
                <div className="overflow-x-auto rounded-xl border bg-card">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs text-muted-foreground">
                      <tr>
                        {["project", "route", "step", "status", "started", "trigger"].map((k) => (
                          <th key={k} scope="col" className="px-4 py-2 font-medium">
                            {t(k)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {moves.map((m) => (
                        <tr key={m.id} className={cn("border-t", m.auto && "bg-amber-500/10")}>
                          <td className="px-4 py-2 font-semibold">{m.project}</td>
                          <td className="px-4 py-2 whitespace-nowrap">
                            {cloudLabel(m.source)} → {cloudLabel(m.target)}
                          </td>
                          <td className="px-4 py-2">
                            {m.step && tSteps.has(m.step) ? tSteps(m.step) : (m.step ?? "")}
                          </td>
                          <td className="px-4 py-2">
                            <span
                              className={cn(
                                "inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium",
                                MOVE_STATUS[m.status] ?? "bg-muted text-muted-foreground",
                              )}
                            >
                              {m.status}
                            </span>
                          </td>
                          <td className="px-4 py-2 whitespace-nowrap tabular-nums">
                            {new Date(m.started_at).toLocaleString()}
                          </td>
                          <td className="px-4 py-2">
                            <Badge variant={m.auto ? "default" : "outline"}>
                              {m.auto ? t("auto") : t("manual")}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}
      </PageBody>
    </div>
  );
}
