"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable, type DataColumn } from "@/components/data/DataTable";
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
import { can } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import type {
  SecurityCategory,
  SecurityException,
  SecurityFinding,
  SecuritySummary,
  SecurityTier,
  SecurityTrendPoint,
  SecurityView,
} from "@/types";
import { AlertCircle, AlertTriangle, CheckCircle2, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { ExceptionDialog } from "./ExceptionDialog";
import { ExceptionsList } from "./ExceptionsList";
import { FindingCard } from "./FindingCard";
import { useFindingText } from "./findingText";
import { AlertsCard } from "./AlertsCard";
import { BackupsCard } from "./BackupsCard";
import { CATEGORY_TAB, errorMessage, TABS, TIER_META, type SecurityTab } from "./labels";
import { ScansCard } from "./ScansCard";
import { ScoreRing } from "./ScoreRing";
import { AppSettingsCard, ProjectPolicyCard } from "./SettingsCard";
import { NewMajorChart, ScoreTrendChart } from "./TrendCharts";
import { pollWhileVisible } from "@/lib/visiblePolling";

const REFRESH_MS = 60_000;
const PRIORITY_LIMIT = 6;

interface Data {
  summary: SecuritySummary;
  findings: SecurityFinding[];
  trend: SecurityTrendPoint[];
  exceptions: SecurityException[];
  role: {
    role: "admin" | "member" | "guest" | null;
    cnp_admin: boolean;
    auditor?: boolean;
    username: string;
  };
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
    const stop = pollWhileVisible(refresh, REFRESH_MS);
    const first = setTimeout(refresh, 0);
    return () => {
      stop();
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

function TabBar({
  tab,
  onSelect,
  countOf,
  fullPageHref,
}: {
  tab: SecurityTab;
  onSelect: (tab: SecurityTab) => void;
  countOf?: (item: (typeof TABS)[number]) => number;
  fullPageHref?: string;
}) {
  const t = useTranslations("security");
  return (
    <div className="flex flex-wrap items-center gap-1 border-b pb-2">
      {TABS.map((item) => {
        const count = countOf?.(item) ?? 0;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onSelect(item.value)}
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
      {fullPageHref && (
        <Link href={fullPageHref} className="ml-auto text-xs text-primary hover:underline">
          {t("dashboard.openFullPage")}
        </Link>
      )}
    </div>
  );
}

function TitledSkeleton({
  title,
  description,
  height,
  className,
}: {
  title: string;
  description?: string;
  height: string;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </CardHeader>
      <CardContent>
        <Skeleton className={height} />
      </CardContent>
    </Card>
  );
}

function OverviewSkeleton({ showApps }: { showApps: boolean }) {
  const t = useTranslations("security");
  return (
    <div className="space-y-4" aria-busy>
      <div className="grid gap-4 lg:grid-cols-3">
        <TitledSkeleton className="lg:col-span-2" title={t("dashboard.trend")} height="h-40" />
        <TitledSkeleton
          title={t("dashboard.newMajor")}
          description={t("dashboard.newMajorDescription")}
          height="h-40"
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <TitledSkeleton title={t("dashboard.score")} height="h-36" />
        <TitledSkeleton title={t("dashboard.scans")} height="h-36" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <TitledSkeleton className="lg:col-span-2" title={t("dashboard.priority")} height="h-72" />
        <div className="space-y-4">
          <TitledSkeleton title={t("dashboard.guarantees")} height="h-28" />
        </div>
      </div>
      {showApps && (
        <Card>
          <CardHeader>
            <CardTitle>{t("dashboard.apps")}</CardTitle>
          </CardHeader>
          <CardContent>
            <AppsTable apps={[]} project="" loading />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AppsTable({
  apps,
  project,
  loading = false,
}: {
  apps: SecuritySummary["apps"];
  project: string;
  loading?: boolean;
}) {
  const t = useTranslations("security");
  const columns: DataColumn<SecuritySummary["apps"][number]>[] = [
    {
      key: "app",
      header: t("dashboard.app"),
      sortable: true,
      cell: (a) => (
        <Link
          href={`/security?project=${encodeURIComponent(project)}&app=${encodeURIComponent(a.app)}`}
          className="font-medium hover:underline"
        >
          {a.app}
        </Link>
      ),
    },
    {
      key: "score",
      header: t("dashboard.grade"),
      sortable: true,
      className: "tabular-nums",
      cell: (a) => (a.score === null ? t("notScanned.short") : `${a.score} · ${a.grade}`),
    },
    { key: "actions", header: t("dashboard.appActions"), sortable: true, cell: (a) => a.actions },
    {
      key: "core",
      header: t("dashboard.appMandatory"),
      sortable: true,
      cell: (a) => <span className={cn(a.core > 0 && "font-semibold text-red-600")}>{a.core}</span>,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={apps}
      rowKey={(a) => a.app}
      loading={loading}
      storageKey="security.apps"
      initialSort={{ key: "score", direction: "asc" }}
      empty={{ title: t("dashboard.noApp") }}
    />
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
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tabParam = params.get("tab");
  const tierParam = params.get("tier");
  const tab = TABS.find((item) => item.value === tabParam)?.value ?? "overview";
  const tier = tierParam !== null && tierParam in TIER_META ? (tierParam as SecurityTier) : null;
  // The tab and tier live in the URL so a link lands on the filtered view.
  function setView(nextTab: SecurityTab, nextTier: SecurityTier | null = null) {
    const next = new URLSearchParams(params.toString());
    if (nextTab === "overview") next.delete("tab");
    else next.set("tab", nextTab);
    if (nextTier) next.set("tier", nextTier);
    else next.delete("tier");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }
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
      <div className="space-y-6">
        <TabBar tab={tab} onSelect={setView} />
        {tab === "overview" ? (
          <OverviewSkeleton showApps={!app} />
        ) : (
          <Skeleton className="h-72 rounded-xl" />
        )}
      </div>
    );
  }

  const { summary, findings, trend, exceptions, role } = data;
  const canAdmin = role.cnp_admin || can(role.role, "security.manage");

  function showCategory(category: SecurityCategory) {
    const target = `security-${category}`;
    const next = CATEGORY_TAB[category];
    if (next === tab) {
      scrollToSection(target);
    } else {
      pendingScroll.current = target;
      setView(next);
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

      <TabBar
        tab={tab}
        onSelect={setView}
        countOf={(item) =>
          item.value === "ignored"
            ? exceptions.length
            : findings.filter((f) => item.categories.includes(f.category)).length
        }
        fullPageHref={
          fullPageLink
            ? `/security?project=${encodeURIComponent(project)}${app ? `&app=${encodeURIComponent(app)}` : ""}`
            : undefined
        }
      />

      {tab === "overview" && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>{t("dashboard.trend")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ScoreTrendChart points={trend} />
              </CardContent>
            </Card>
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
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t("dashboard.score")}</CardTitle>
              </CardHeader>
              <CardContent>
                {summary.score === null || summary.grade === null ? (
                  <div role="status" className="space-y-1">
                    <p className="text-lg font-semibold">{t("notScanned.title")}</p>
                    <p className="text-sm text-muted-foreground">
                      {t(`notScanned.reasons.${summary.not_scanned_reason ?? "no_scan_yet"}`)}
                    </p>
                  </div>
                ) : (
                  <ScoreRing
                    score={summary.score}
                    grade={summary.grade}
                    actions={summary.actions}
                    segments={summary.segments}
                    onSelect={showCategory}
                  />
                )}
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

          {!app && (
            <Card>
              <CardHeader>
                <CardTitle>{t("dashboard.apps")}</CardTitle>
              </CardHeader>
              <CardContent>
                <AppsTable apps={summary.apps} project={project} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {tab !== "overview" && tab !== "ignored" && tab !== "journal" && (
        <div className="space-y-8">
          {tier && (
            <button
              type="button"
              onClick={() => setView(tab)}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"
              aria-label={t("dashboard.clearTier", { tier: t(`tiers.${tier}`) })}
            >
              {t(`tiers.${tier}`)}
              <X className="h-3 w-3" />
            </button>
          )}
          {TABS.find((item) => item.value === tab)?.categories.map((category) => (
            <CategorySection
              key={category}
              category={category}
              findings={tier ? findings.filter((f) => f.tier === tier) : findings}
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
          canReview={role.cnp_admin || role.auditor === true}
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
