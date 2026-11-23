"use client";

import { CLOUD_META } from "@/components/finops/format";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { ProjectProvider } from "@/components/projects/ProjectContext";
import { MoveButtons } from "@/components/projects/ProjectLocation";
import { Badge } from "@/components/ui/badge";
import { getEmergency, type Emergency } from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { cn } from "@/lib/utils";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { Siren, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

const POLL_MS = 5000;

const cloudLabel = (cloud: string) =>
  CLOUD_META[cloud as keyof typeof CLOUD_META]?.label ?? cloud;

const STATE_STYLE: Record<string, string> = {
  down: "border-destructive bg-destructive/10 text-destructive",
  degraded: "border-amber-500 bg-amber-500/10 text-amber-700 dark:text-amber-400",
};

const STATUS_STYLE: Record<string, string> = {
  failed: "text-destructive",
  cancelling: "text-amber-700 dark:text-amber-400",
};

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
            <section aria-label={t("clusters")} className="grid gap-3 sm:grid-cols-3">
              {data.clusters.map((c) => (
                <div
                  key={c.cloud}
                  className={cn(
                    "flex items-center justify-between rounded-xl border bg-card px-4 py-3",
                    STATE_STYLE[c.state],
                  )}
                >
                  <span className="font-semibold">{cloudLabel(c.cloud)}</span>
                  <span className="text-sm font-semibold uppercase">{c.state}</span>
                </div>
              ))}
            </section>

            <section aria-label={t("projects")} className="space-y-2">
              <h2 className="text-sm font-semibold">{t("projects")}</h2>
              {data.projects.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("none")}</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {data.projects.map((p) => (
                    <li
                      key={p.name}
                      className={cn(
                        "flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3",
                        p.cloud_state === "down" && "border-destructive/60",
                        p.protected && "border-l-4 border-l-primary",
                      )}
                    >
                      <div className="flex min-w-0 flex-col gap-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <Link
                            href={`/projects/${encodeURIComponent(p.name)}`}
                            className="font-semibold hover:underline"
                          >
                            {p.name}
                          </Link>
                          {p.favorite && (
                            <Badge variant="secondary">
                              <Star aria-hidden /> {t("favorite")}
                            </Badge>
                          )}
                          {p.protected && <Badge>{t("protected")}</Badge>}
                          <Badge variant={p.cloud_state === "down" ? "destructive" : "outline"}>
                            {cloudLabel(p.cloud)} · {p.cloud_state}
                          </Badge>
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          {p.apps.length > 0 ? p.apps.join(", ") : t("apps", { count: 0 })}
                        </span>
                      </div>
                      <ProjectProvider projectName={p.name}>
                        <MoveButtons />
                      </ProjectProvider>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-label={t("moves")} className="space-y-2">
              <h2 className="text-sm font-semibold">{t("moves")}</h2>
              {data.moves.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("none")}</p>
              ) : (
                <div className="overflow-x-auto rounded-xl border bg-card">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs text-muted-foreground">
                      <tr>
                        {["project", "route", "step", "status", "started", "trigger"].map((k) => (
                          <th key={k} className="px-4 py-2 font-medium">
                            {t(k)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.moves.map((m) => (
                        <tr key={m.id} className={cn("border-t", m.auto && "bg-amber-500/10")}>
                          <td className="px-4 py-2 font-semibold">{m.project}</td>
                          <td className="px-4 py-2">
                            {cloudLabel(m.source)} → {cloudLabel(m.target)}
                          </td>
                          <td className="px-4 py-2">
                            {m.step && tSteps.has(m.step) ? tSteps(m.step) : (m.step ?? "")}
                          </td>
                          <td className={cn("px-4 py-2 font-medium", STATUS_STYLE[m.status])}>
                            {m.status}
                          </td>
                          <td className="px-4 py-2 tabular-nums">
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
