"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { NotConnected } from "@/components/clusters/NotConnected";
import {
  getSecurityExceptions,
  getSecurityFindings,
  getSecurityRole,
  getSecuritySummary,
  isNotConnected,
  getSecurityTrend,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  SecurityCategory,
  SecurityException,
  SecurityFinding,
  SecuritySummary,
  SecurityTrendPoint,
  SecurityView,
} from "@/types";
import { AlertCircle, AlertTriangle, CheckCircle2, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { ExceptionDialog } from "./ExceptionDialog";
import { ExceptionsList } from "./ExceptionsList";
import { FindingCard } from "./FindingCard";
import { useFindingText } from "./findingText";
import { AlertsCard } from "./AlertsCard";
import { BackupsCard } from "./BackupsCard";
import { CATEGORY_TAB, errorMessage, TABS, type SecurityTab } from "./labels";
import { ScansCard } from "./ScansCard";
import { ScoreRing } from "./ScoreRing";
import { AppSettingsCard, ProjectPolicyCard } from "./SettingsCard";
import { NewMajorChart, ScoreTrendChart } from "./TrendCharts";

const REFRESH_MS = 60_000;
const PRIORITY_LIMIT = 6;

interface Data {
  summary: SecuritySummary;
  findings: SecurityFinding[];
  trend: SecurityTrendPoint[];
  exceptions: SecurityException[];
  role: { role: "admin" | "member" | null; cnp_admin: boolean; username: string };
}

async function fetchSecurityData(
  project: string,
  app: string | null,
  view: SecurityView,
): Promise<Data> {
  const [summary, findings, trend, exceptions, role] = await Promise.all([
    getSecuritySummary(project, app, view),
    getSecurityFindings(project, app, view),
    getSecurityTrend(project, app),
    getSecurityExceptions(project, app),
    getSecurityRole(project),
  ]);
  return { summary, findings, trend, exceptions, role };
}

function useSecurityData(project: string, app: string | null, view: SecurityView) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(
    () =>
      fetchSecurityData(project, app, view).then(
        (next) => {
          setData(next);
          setError(null);
        },
        (e) => setError(e),
      ),
    [project, app, view],
  );

  useEffect(() => {
    const timer = setInterval(refresh, REFRESH_MS);
    const first = setTimeout(refresh, 0);
    return () => {
      clearInterval(timer);
      clearTimeout(first);
    };
  }, [refresh]);

  return { data, error, refresh };
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function CoreBanner({
  findings,
  onShow,
}: {
  findings: SecurityFinding[];
  onShow: (finding: SecurityFinding) => void;
}) {
  const t = useTranslations("security");
  const text = useFindingText();
  const core = findings.filter((f) => f.tier === "core" && !f.exception);
  if (core.length === 0) return null;
  const first = core[0];
  const title = text(first, "title");
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3">
      <AlertTriangle className="h-5 w-5 shrink-0 text-red-600" />
      <p className="flex-1 text-sm font-medium">
        {t("banner.count", { count: core.length })}
        <span className="font-normal text-muted-foreground">
          {" "}
          · {first.app
            ? t("banner.inApp", { title, app: first.app })
            : title}
        </span>
      </p>
      <button
        type="button"
        onClick={() => onShow(first)}
        className="text-sm font-medium text-red-700 hover:underline dark:text-red-300"
      >
        {t("banner.show")}
      </button>
    </div>
  );
}

function CategorySection({
  category,
  findings,
  render,
}: {
  category: SecurityCategory;
  findings: SecurityFinding[];
  render: (f: SecurityFinding) => React.ReactNode;
}) {
  const t = useTranslations("security");
  const items = findings.filter((f) => f.category === category);
  return (
    <section id={`security-${category}`} className="scroll-mt-24 space-y-3">
      <h3 className="text-base font-semibold">
        {t(`categories.${category}.question`)}
      </h3>
      {items.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          {t("dashboard.nothingToReport")}
        </p>
      ) : (
        <div className="space-y-2">{items.map(render)}</div>
      )}
    </section>
  );
}

/**
 * The security page of a project, or of one app when ``app`` is set. Used
 * as the /security page and inside the project and app pages.
 */
export function SecurityDashboard({
  project,
  app = null,
  view = "developer",
  fullPageLink = false,
  showSettings = true,
}: {
  project: string;
  app?: string | null;
  view?: SecurityView;
  fullPageLink?: boolean;
  showSettings?: boolean;
}) {
  const { data, error, refresh } = useSecurityData(project, app, view);
  const [tab, setTab] = useState<SecurityTab>("overview");
  const pendingScroll = useRef<string | null>(null);
  const [ignoring, setIgnoring] = useState<SecurityFinding | null>(null);
  const t = useTranslations("security");

  useEffect(() => {
    if (!pendingScroll.current) return;
    scrollToSection(pendingScroll.current);
    pendingScroll.current = null;
  }, [tab]);

  if (error != null && !data) {
    const nc = isNotConnected(error);
    if (nc) return <NotConnected cloud={nc.cloud} />;
    return (
      <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        <AlertCircle className="h-4 w-4" />
        {errorMessage(error)}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-12 rounded-lg" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-56 rounded-xl" />
        </div>
        <Skeleton className="h-72 rounded-xl" />
      </div>
    );
  }

  const { summary, findings, trend, exceptions, role } = data;
  const canAdmin = role.cnp_admin || role.role === "admin";

  function showCategory(category: SecurityCategory) {
    const target = `security-${category}`;
    const next = CATEGORY_TAB[category];
    if (next === tab) {
      scrollToSection(target);
    } else {
      pendingScroll.current = target;
      setTab(next);
    }
  }

  const card = (f: SecurityFinding) => (
    <FindingCard
      key={f.fingerprint}
      finding={f}
      project={project}
      apps={summary.apps}
      canAdmin={canAdmin}
      onIgnore={view === "developer" ? setIgnoring : undefined}
    />
  );

  return (
    <div className="space-y-6">
      <CoreBanner findings={findings} onShow={(f) => showCategory(f.category)} />

      <div className="flex flex-wrap items-center gap-1 border-b pb-2">
        {TABS.map((item) => {
          const count =
            item.value === "ignored"
              ? exceptions.length
              : findings.filter((f) => item.categories.includes(f.category)).length;
          return (
            <button
              key={item.value}
              type="button"
              onClick={() => setTab(item.value)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                tab === item.value
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              {t(`tabs.${item.value}`)}
              {item.value !== "overview" && count > 0 && (
                <Badge variant="outline" className="h-5 px-1.5 text-[11px]">
                  {count}
                </Badge>
              )}
            </button>
          );
        })}
        {fullPageLink && (
          <Link
            href={`/security?project=${encodeURIComponent(project)}${app ? `&app=${encodeURIComponent(app)}` : ""}`}
            className="ml-auto text-xs text-primary hover:underline"
          >
            {t("dashboard.openFullPage")}
          </Link>
        )}
      </div>

      {tab === "overview" && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t("dashboard.score")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ScoreRing
                  score={summary.score}
                  grade={summary.grade}
                  actions={summary.actions}
                  segments={summary.segments}
                  onSelect={showCategory}
                />
                {summary.excepted > 0 && (
                  <p className="mt-3 text-xs text-muted-foreground">
                    {t("dashboard.excepted", { count: summary.excepted })}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>{t("dashboard.scans")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ScansCard project={project} scans={summary.scans} onRequested={refresh} />
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>{t("dashboard.priority")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {findings.length === 0 ? (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <ShieldCheck className="h-4 w-4 text-green-600" />
                    {t("dashboard.noAction")}
                  </p>
                ) : (
                  <>
                    {findings.slice(0, PRIORITY_LIMIT).map(card)}
                    {findings.length > PRIORITY_LIMIT && (
                      <p className="pt-1 text-xs text-muted-foreground">
                        {t("dashboard.moreActions", {
                          count: findings.length - PRIORITY_LIMIT,
                        })}
                      </p>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>{t("dashboard.newMajor")}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t("dashboard.newMajorDescription")}
                  </p>
                </CardHeader>
                <CardContent>
                  <NewMajorChart points={trend} />
                </CardContent>
              </Card>
              {showSettings && app && view === "developer" && (
                <AppSettingsCard project={project} app={app} canAdmin={canAdmin} />
              )}
              {showSettings && !app && role.cnp_admin && <ProjectPolicyCard project={project} />}
              {showSettings && view === "developer" && (
                <AlertsCard project={project} app={app} canAdmin={canAdmin} />
              )}
              {view === "developer" && (
                <Card className="bg-muted/40">
                  <CardHeader>
                    <CardTitle>{t("dashboard.guarantees")}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1.5 text-sm">
                    {summary.guarantees.map((g) => (
                      <p key={g.label} className="flex items-center gap-2">
                        <span
                          className={cn(
                            "h-2 w-2 rounded-full",
                            g.ok === null
                              ? "bg-muted-foreground/40"
                              : g.ok
                                ? "bg-green-600"
                                : "bg-red-600",
                          )}
                        />
                        {g.key && t.has(`guarantees.${g.key}`)
                          ? t(`guarantees.${g.key}`)
                          : g.label}
                        {g.ok === null && (
                          <span className="text-xs text-muted-foreground">
                            {t("dashboard.notApplicable")}
                          </span>
                        )}
                      </p>
                    ))}
                    <p className="pt-1 text-xs text-muted-foreground">
                      {t("dashboard.guaranteesNote")}
                    </p>
                  </CardContent>
                </Card>
              )}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>{t("dashboard.trend")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ScoreTrendChart points={trend} />
              </CardContent>
            </Card>
            {!app && (
              <Card className="lg:col-span-2">
                <CardHeader>
                  <CardTitle>{t("dashboard.apps")}</CardTitle>
                </CardHeader>
                <CardContent>
                  {summary.apps.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("dashboard.noApp")}</p>
                  ) : (
                    <table className="w-full text-sm">
                      <thead className="text-left text-xs text-muted-foreground">
                        <tr>
                          <th className="pb-2 font-medium">{t("dashboard.app")}</th>
                          <th className="pb-2 font-medium">{t("dashboard.grade")}</th>
                          <th className="pb-2 font-medium">{t("dashboard.appActions")}</th>
                          <th className="pb-2 font-medium">{t("dashboard.appMandatory")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.apps.map((a) => (
                          <tr key={a.app} className="border-t">
                            <td className="py-2">
                              <Link
                                href={`/security?project=${encodeURIComponent(project)}&app=${encodeURIComponent(a.app)}`}
                                className="font-medium hover:underline"
                              >
                                {a.app}
                              </Link>
                            </td>
                            <td className="py-2 tabular-nums">
                              {a.score} · {a.grade}
                            </td>
                            <td className="py-2 tabular-nums">{a.actions}</td>
                            <td
                              className={cn(
                                "py-2 tabular-nums",
                                a.core > 0 && "font-semibold text-red-600",
                              )}
                            >
                              {a.core}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {tab !== "overview" && tab !== "ignored" && tab !== "journal" && (
        <div className="space-y-8">
          {TABS.find((item) => item.value === tab)?.categories.map((category) => (
            <CategorySection
              key={category}
              category={category}
              findings={findings}
              render={card}
            />
          ))}
          {tab === "data" && app && (
            <BackupsCard project={project} app={app} canAdmin={canAdmin} />
          )}
        </div>
      )}

      {tab === "journal" && (
        <p className="text-sm text-muted-foreground">
          {t("dashboard.journalSoon")}
        </p>
      )}

      {tab === "ignored" && (
        <ExceptionsList
          exceptions={exceptions}
          canAdmin={canAdmin}
          username={role.username}
          onChanged={refresh}
        />
      )}

      {ignoring && (
        <ExceptionDialog
          key={ignoring.fingerprint}
          finding={ignoring}
          project={project}
          onClose={() => setIgnoring(null)}
          onDone={() => {
            setIgnoring(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}
