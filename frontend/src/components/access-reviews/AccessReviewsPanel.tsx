"use client";

import {
  CheckCircle2,
  Download,
  Loader2,
  Play,
  ShieldAlert,
  Square,
  UserCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/components/activity/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  closeAccessReview,
  downloadAccessReview,
  enableInactiveAccount,
  getAccessReview,
  getAccessReviews,
  markReviewTaskDone,
  startAccessReview,
} from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import type {
  AccessReviewDetail,
  AccessReviewItem,
  AccessReviewOverview,
  InactiveAccount,
} from "@/types/accessReview";

import { LastLogin, ReviewTable } from "./ReviewItems";

const POLL_MS = 30_000;

export function AccessReviewsPanel() {
  const t = useTranslations("accessReviews");
  const [data, setData] = useState<AccessReviewOverview | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<AccessReviewDetail | null>(null);

  const load = useCallback(() => {
    getAccessReviews()
      .then((overview) => {
        setData(overview);
        setForbidden(false);
        setSelected((current) => current ?? overview.campaigns[0]?.id ?? null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
      });
  }, []);

  const loadDetail = useCallback(() => {
    if (selected === null) return;
    getAccessReview(selected)
      .then(setDetail)
      .catch((err) => toast.error(errorMessage(err)));
  }, [selected]);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(loadDetail, [loadDetail]);

  const refresh = () => {
    load();
    loadDetail();
  };

  if (forbidden) {
    return (
      <Card>
        <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
          <ShieldAlert className="h-4 w-4" aria-hidden />
          {t("forbidden")}
        </CardContent>
      </Card>
    );
  }

  if (data === null) {
    return (
      <Loader2
        className="h-5 w-5 animate-spin text-muted-foreground"
        aria-label={t("loading")}
      />
    );
  }

  return (
    <div className="space-y-6">
      {!data.can_manage && (
        <p className="rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
          {t("readOnly")}
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Campaigns
          data={data}
          selected={selected}
          onSelect={setSelected}
          onStarted={(id) => {
            setSelected(id);
            load();
          }}
        />
        {detail && detail.campaign.id === selected ? (
          <CampaignDetail
            detail={detail}
            me={data.me}
            canManage={data.can_manage}
            onChange={refresh}
          />
        ) : (
          <Card>
            <CardContent className="text-sm text-muted-foreground">
              {t("campaigns.none")}
            </CardContent>
          </Card>
        )}
      </div>
      <InactiveAccounts data={data} onChange={load} />
    </div>
  );
}

function Campaigns({
  data,
  selected,
  onSelect,
  onStarted,
}: {
  data: AccessReviewOverview;
  selected: number | null;
  onSelect: (id: number) => void;
  onStarted: (id: number) => void;
}) {
  const t = useTranslations("accessReviews");
  const format = useFormat();
  const [starting, setStarting] = useState(false);
  const hasOpen = data.campaigns.some((c) => c.status === "open");
  const s = data.settings;

  const start = async () => {
    setStarting(true);
    try {
      const campaign = await startAccessReview();
      toast.success(t("toasts.started"));
      onStarted(campaign.id);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setStarting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("campaigns.title")}</CardTitle>
        <CardDescription>
          {s.auto_start
            ? t("campaigns.auto", { days: s.interval_days })
            : t("campaigns.manual")}{" "}
          {t("campaigns.deadline", { days: s.deadline_days })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {data.can_manage && (
          <Button size="sm" onClick={start} disabled={starting || hasOpen}>
            {starting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {t("actions.start")}
          </Button>
        )}
        {data.campaigns.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("campaigns.empty")}
          </p>
        ) : (
          <ul className="space-y-2">
            {data.campaigns.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onSelect(c.id)}
                  aria-current={selected === c.id ? "true" : undefined}
                  className="w-full rounded-lg border p-3 text-left text-sm hover:bg-muted/50 aria-[current=true]:border-primary aria-[current=true]:bg-primary/5"
                >
                  <span className="flex flex-wrap items-center gap-2 font-medium">
                    {t("campaigns.name", { id: c.id })}
                    <Badge variant={c.status === "open" ? "default" : "secondary"}>
                      {t(`status.${c.status}`)}
                    </Badge>
                    {c.escalated_at && c.status === "open" && (
                      <Badge variant="destructive">{t("status.overdue")}</Badge>
                    )}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {t("campaigns.meta", {
                      decided: c.decided,
                      total: c.total,
                      due: format.date(c.due_at),
                    })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function CampaignDetail({
  detail,
  me,
  canManage,
  onChange,
}: {
  detail: AccessReviewDetail;
  me: string;
  canManage: boolean;
  onChange: () => void;
}) {
  const t = useTranslations("accessReviews");
  const format = useFormat();
  const { campaign, items } = detail;
  const [closing, setClosing] = useState(false);
  const platform = items.filter((i) => i.scope === "platform");
  const projects = items.filter((i) => i.scope === "project");
  const tasks = items.filter((i) => i.outcome === "terraform_task");

  const close = async () => {
    setClosing(true);
    try {
      await closeAccessReview(campaign.id);
      toast.success(t("toasts.closed"));
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setClosing(false);
    }
  };

  const download = (fmt: "csv" | "json") =>
    downloadAccessReview(campaign.id, fmt).catch((err) =>
      toast.error(errorMessage(err)),
    );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("campaigns.name", { id: campaign.id })}</CardTitle>
          <CardDescription>
            {t("detail.meta", {
              by: campaign.started_by,
              started: format.date(campaign.started_at),
              due: format.date(campaign.due_at),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => download("csv")}>
            <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("actions.exportCsv")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => download("json")}>
            <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("actions.exportJson")}
          </Button>
          {canManage && campaign.status === "open" && (
            <Button
              size="sm"
              variant="outline"
              onClick={close}
              disabled={closing}
            >
              <Square className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {t("actions.close")}
            </Button>
          )}
        </CardContent>
      </Card>

      {tasks.length > 0 && (
        <TerraformTasks items={tasks} canManage={canManage} onChange={onChange} />
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t("detail.platformTitle")}</CardTitle>
          <CardDescription>{t("detail.platformDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {platform.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("detail.empty")}</p>
          ) : (
            <ReviewTable
              items={campaign.status === "open" ? platform : platform.map(frozen)}
              me={me}
              onDone={onChange}
              showSubject
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("detail.projectsTitle")}</CardTitle>
          <CardDescription>{t("detail.projectsDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {projects.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("detail.empty")}</p>
          ) : (
            <ReviewTable
              items={campaign.status === "open" ? projects : projects.map(frozen)}
              me={me}
              onDone={onChange}
              showSubject
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** A closed campaign takes no decision: nobody is a reviewer any more. */
function frozen(item: AccessReviewItem): AccessReviewItem {
  return { ...item, reviewers: [] };
}

function TerraformTasks({
  items,
  canManage,
  onChange,
}: {
  items: AccessReviewItem[];
  canManage: boolean;
  onChange: () => void;
}) {
  const t = useTranslations("accessReviews");

  const done = async (item: AccessReviewItem) => {
    try {
      await markReviewTaskDone(item.id);
      toast.success(t("toasts.taskDone"));
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("tasks.title")}</CardTitle>
        <CardDescription>{t("tasks.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border">
          {items.map((item) => (
            <li key={item.id} className="space-y-1 py-3 first:pt-0 last:pb-0">
              <p className="text-sm font-medium">
                {t("tasks.remove", { user: item.username, group: item.subject })}
              </p>
              <code className="block break-words text-xs text-muted-foreground">
                {item.outcome_detail}
              </code>
              {item.task_done_at ? (
                <p className="flex items-center gap-1 text-xs text-muted-foreground">
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                  {t("tasks.done", { user: item.task_done_by ?? "" })}
                </p>
              ) : (
                canManage && (
                  <Button size="sm" variant="outline" onClick={() => done(item)}>
                    {t("actions.taskDone")}
                  </Button>
                )
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function InactiveAccounts({
  data,
  onChange,
}: {
  data: AccessReviewOverview;
  onChange: () => void;
}) {
  const t = useTranslations("accessReviews");
  const format = useFormat();
  const captionId = useId();
  const s = data.settings;

  const keep = async (account: InactiveAccount) => {
    try {
      await enableInactiveAccount(account.user_id);
      toast.success(t("toasts.kept", { user: account.username }));
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle id={captionId}>{t("inactive.title")}</CardTitle>
        <CardDescription>
          {t("inactive.description", { days: s.inactive_after_days })}{" "}
          {s.inactive_disable_enabled
            ? t("inactive.disableOn", { days: s.inactive_grace_days })
            : t("inactive.disableOff")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.accounts.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("inactive.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-labelledby={captionId}>
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("table.member")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("table.lastLogin")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("inactive.state")}
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    <span className="sr-only">{t("table.actions")}</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.accounts.map((a) => (
                  <tr key={a.user_id}>
                    <td className="py-2 pr-3 font-medium">{a.username}</td>
                    <td className="py-2 pr-3">
                      {a.last_login_at ? (
                        <LastLogin value={a.last_login_at} />
                      ) : (
                        <span className="text-muted-foreground">
                          {t("inactive.notSince", {
                            date: format.date(a.tracked_since),
                          })}
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {a.disabled_at ? (
                        <Badge variant="destructive">
                          {t("inactive.disabled", {
                            date: format.date(a.disabled_at),
                          })}
                        </Badge>
                      ) : (
                        <Badge variant="outline">
                          {a.disable_due_at
                            ? t("inactive.disableDue", {
                                date: format.date(a.disable_due_at),
                              })
                            : t("inactive.reported")}
                        </Badge>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {data.can_manage && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => keep(a)}
                          aria-label={t("actions.keepAccount", {
                            user: a.username,
                          })}
                        >
                          <UserCheck className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                          {a.disabled_at
                            ? t("actions.enable")
                            : t("actions.keepActive")}
                        </Button>
                      )}
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
