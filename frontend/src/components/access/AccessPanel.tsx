"use client";

import {
  Check,
  Clock,
  Loader2,
  LogOut,
  ShieldAlert,
  Undo2,
  X,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, decideAccess, getAccessOverview, requestAccess } from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import type { AccessOverview, AccessRequest, AccessStatus } from "@/types";

const POLL_MS = 15_000;
const DURATIONS = [15, 30, 60, 120, 240];
const REASON_MIN = 10;

const SELECT =
  "h-9 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const STATUS_VARIANT: Record<
  AccessStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  active: "default",
  denied: "destructive",
  cancelled: "secondary",
  lapsed: "secondary",
  expired: "secondary",
  revoked: "secondary",
  failed: "destructive",
};

type Action = "approve" | "deny" | "cancel" | "end";

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

function countdown(until: string, now: number): string {
  const left = Math.max(0, Math.floor((new Date(until).getTime() - now) / 1000));
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function AccessPanel() {
  const t = useTranslations("access");
  const [data, setData] = useState<AccessOverview | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    getAccessOverview()
      .then((overview) => {
        setData(overview);
        setForbidden(false);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
      });
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const act = async (row: AccessRequest, action: Action, reason?: string) => {
    setBusy(`${row.id}:${action}`);
    try {
      await decideAccess(row.id, action, reason);
      toast.success(t(`toasts.${action}`));
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
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

  const mine = [...data.pending, ...data.active].filter(
    (r) => r.requester === data.me,
  );
  const queue = data.pending.filter((r) => r.requester !== data.me);

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <RequestForm data={data} onDone={load} />
        <Card>
          <CardHeader>
            <CardTitle>{t("mine.title")}</CardTitle>
            <CardDescription>{t("mine.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            <RequestList
              rows={mine}
              empty={t("mine.empty")}
              busy={busy}
              actions={(row) =>
                row.status === "pending" ? ["cancel"] : ["end"]
              }
              onAct={act}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("queue.title")}</CardTitle>
          <CardDescription>{t("queue.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <RequestList
            rows={queue}
            empty={t("queue.empty")}
            busy={busy}
            actions={() => ["approve", "deny"]}
            onAct={act}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("active.title")}</CardTitle>
          <CardDescription>{t("active.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <RequestList
            rows={data.active}
            empty={t("active.empty")}
            busy={busy}
            actions={() => ["end"]}
            onAct={act}
          />
        </CardContent>
      </Card>

      <History rows={data.history} />
    </div>
  );
}

function useElevationLabel() {
  const t = useTranslations("access");
  return (key: string, field: "title" | "description" = "title") =>
    t.has(`elevations.${key}.${field}`)
      ? t(`elevations.${key}.${field}`)
      : key;
}

function durationLabel(
  t: ReturnType<typeof useTranslations>,
  minutes: number,
): string {
  return minutes % 60 === 0
    ? t("duration.hours", { count: minutes / 60 })
    : t("duration.minutes", { count: minutes });
}

function RequestForm({
  data,
  onDone,
}: {
  data: AccessOverview;
  onDone: () => void;
}) {
  const t = useTranslations("access");
  const label = useElevationLabel();
  const id = useId();
  const { elevations, default_minutes, min_minutes, max_minutes } =
    data.catalog;
  const [elevation, setElevation] = useState(elevations[0]?.key ?? "");
  const [minutes, setMinutes] = useState(default_minutes);
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const choices = DURATIONS.filter((d) => d >= min_minutes && d <= max_minutes);
  const tooShort = reason.trim().length < REASON_MIN;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    try {
      await requestAccess({ elevation, reason, duration_minutes: minutes });
      toast.success(t("toasts.request"));
      setReason("");
      onDone();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("form.title")}</CardTitle>
        <CardDescription>{t("form.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t("form.elevation")}</legend>
            {elevations.map((e) => (
              <label
                key={e.key}
                className="flex cursor-pointer gap-3 rounded-lg border p-3 text-sm has-[:checked]:border-primary has-[:checked]:bg-primary/5"
              >
                <input
                  type="radio"
                  name={`${id}-elevation`}
                  value={e.key}
                  checked={elevation === e.key}
                  onChange={() => setElevation(e.key)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="block font-medium">{label(e.key)}</span>
                  <span className="block text-xs text-muted-foreground">
                    {label(e.key, "description")}
                  </span>
                  <code className="mt-1 block text-[11px] text-muted-foreground">
                    {e.group}
                  </code>
                </span>
              </label>
            ))}
          </fieldset>

          <div className="space-y-1.5">
            <Label htmlFor={`${id}-duration`}>{t("form.duration")}</Label>
            <select
              id={`${id}-duration`}
              className={SELECT}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
            >
              {choices.map((d) => (
                <option key={d} value={d}>
                  {durationLabel(t, d)}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {t("form.durationHint", { max: durationLabel(t, max_minutes) })}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor={`${id}-reason`}>{t("form.reason")}</Label>
            <textarea
              id={`${id}-reason`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={500}
              required
              placeholder={t("form.reasonPlaceholder")}
              className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            />
            <p className="text-xs text-muted-foreground">
              {t("form.lagHint")}
            </p>
          </div>

          <Button type="submit" disabled={sending || tooShort || !elevation}>
            {sending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("form.submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function RequestList({
  rows,
  empty,
  busy,
  actions,
  onAct,
}: {
  rows: AccessRequest[];
  empty: string;
  busy: string | null;
  actions: (row: AccessRequest) => Action[];
  onAct: (row: AccessRequest, action: Action, reason?: string) => void;
}) {
  const t = useTranslations("access");
  const label = useElevationLabel();
  const format = useFormat();
  const now = useNow();
  const [denying, setDenying] = useState<number | null>(null);
  const [denyReason, setDenyReason] = useState("");

  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }

  const icons: Record<Action, React.ReactNode> = {
    approve: <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />,
    deny: <X className="mr-1.5 h-3.5 w-3.5" aria-hidden />,
    cancel: <Undo2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />,
    end: <LogOut className="mr-1.5 h-3.5 w-3.5" aria-hidden />,
  };

  return (
    <ul className="divide-y divide-border">
      {rows.map((row) => (
        <li key={row.id} className="space-y-2 py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1 basis-64">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                {label(row.elevation)}
                <Badge variant={STATUS_VARIANT[row.status]}>
                  {t(`status.${row.status}`)}
                </Badge>
                {row.status === "active" && row.expires_at && (
                  <span
                    className="inline-flex items-center gap-1 font-mono text-xs tabular-nums text-amber-700 dark:text-amber-400"
                    title={format.dateTime(row.expires_at)}
                  >
                    <Clock className="h-3.5 w-3.5" aria-hidden />
                    {t("remaining", { time: countdown(row.expires_at, now) })}
                  </span>
                )}
              </p>
              <p className="text-xs text-muted-foreground">
                {row.status === "active"
                  ? t("meta.active", {
                      user: row.requester,
                      approver: row.decided_by ?? "?",
                    })
                  : t("meta.pending", {
                      user: row.requester,
                      duration: durationLabel(t, row.duration_minutes),
                      when: format.relative(row.created_at),
                    })}
              </p>
              <p className="mt-1 break-words text-sm">{row.reason}</p>
            </div>
            <div className="flex gap-2">
              {actions(row).map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant={
                    action === "approve"
                      ? "default"
                      : action === "deny"
                        ? "destructive"
                        : "outline"
                  }
                  disabled={busy !== null}
                  onClick={() =>
                    action === "deny"
                      ? setDenying(denying === row.id ? null : row.id)
                      : onAct(row, action)
                  }
                >
                  {busy === `${row.id}:${action}` ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    icons[action]
                  )}
                  {t(`actions.${action}`)}
                </Button>
              ))}
            </div>
          </div>
          {denying === row.id && (
            <form
              className="flex flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                onAct(row, "deny", denyReason);
                setDenying(null);
                setDenyReason("");
              }}
            >
              <Input
                aria-label={t("denyReason")}
                placeholder={t("denyReason")}
                value={denyReason}
                onChange={(e) => setDenyReason(e.target.value)}
                className="h-8 max-w-sm flex-1"
              />
              <Button type="submit" size="sm" variant="destructive">
                {t("actions.confirmDeny")}
              </Button>
            </form>
          )}
        </li>
      ))}
    </ul>
  );
}

function History({ rows }: { rows: AccessRequest[] }) {
  const t = useTranslations("access");
  const label = useElevationLabel();
  const format = useFormat();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("history.title")}</CardTitle>
        <CardDescription>{t("history.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("history.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3 font-medium">{t("history.when")}</th>
                  <th className="py-2 pr-3 font-medium">{t("history.who")}</th>
                  <th className="py-2 pr-3 font-medium">
                    {t("history.elevation")}
                  </th>
                  <th className="py-2 pr-3 font-medium">
                    {t("history.decidedBy")}
                  </th>
                  <th className="py-2 pr-3 font-medium">
                    {t("history.status")}
                  </th>
                  <th className="py-2 font-medium">{t("history.reason")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr key={row.id} className="align-top">
                    <td className="whitespace-nowrap py-2 pr-3 tabular-nums">
                      {format.dateTime(row.created_at)}
                    </td>
                    <td className="py-2 pr-3">{row.requester}</td>
                    <td className="py-2 pr-3">{label(row.elevation)}</td>
                    <td className="py-2 pr-3">{row.decided_by ?? ""}</td>
                    <td className="py-2 pr-3">
                      <Badge variant={STATUS_VARIANT[row.status]}>
                        {t(`status.${row.status}`)}
                      </Badge>
                    </td>
                    <td className="max-w-md break-words py-2 text-muted-foreground">
                      {row.reason}
                      {row.decision_note && (
                        <span className="block text-xs">
                          {t("history.note", { note: row.decision_note })}
                        </span>
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
