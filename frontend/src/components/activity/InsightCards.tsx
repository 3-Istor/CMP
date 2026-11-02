"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ActivitySummary } from "@/types";
import { useLocale, useTranslations } from "next-intl";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  fontSize: 12,
  background: "var(--popover)",
  border: "1px solid var(--border)",
};

/** 2024-01-01 is a Monday, so index 0 is Monday as in the API heatmap. */
function weekdayLabel(locale: string, index: number): string {
  return new Date(Date.UTC(2024, 0, 1 + index)).toLocaleDateString(locale, {
    weekday: "short",
    timeZone: "UTC",
  });
}

function TopList({ title, rows }: { title: string; rows: [string, number][] }) {
  const t = useTranslations("activity.insights");
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-medium text-muted-foreground">{title}</p>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("none")}</p>
      ) : (
        <ul className="space-y-0.5 text-sm">
          {rows.map(([name, count]) => (
            <li key={name} className="flex justify-between gap-2">
              <span className="truncate" title={name}>
                {name}
              </span>
              <span className="tabular-nums text-muted-foreground">
                {count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Realm logins per day, and who fails most: a password attack shows here. */
export function LoginsCard({
  logins,
}: {
  logins: NonNullable<ActivitySummary["logins"]>;
}) {
  const t = useTranslations("activity.insights.logins");
  const locale = useLocale();
  const data = logins.days.map((d) => ({
    day: new Date(`${d.day}T00:00:00Z`).toLocaleDateString(locale, {
      day: "numeric",
      month: "short",
    }),
    success: d.success,
    failure: d.failure,
  }));
  const any = logins.days.some((d) => d.success || d.failure);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {any ? (
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                margin={{ top: 4, right: 4, left: -24, bottom: 0 }}
              >
                <CartesianGrid vertical={false} strokeOpacity={0.15} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                <Bar
                  dataKey="success"
                  name={t("success")}
                  stackId="l"
                  fill="#16a34a"
                />
                <Bar
                  dataKey="failure"
                  name={t("failure")}
                  stackId="l"
                  fill="#dc2626"
                  radius={[3, 3, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {t("empty")}
          </p>
        )}
        <div className="grid grid-cols-2 gap-4">
          <TopList title={t("failingAccounts")} rows={logins.top_failed_users} />
          <TopList title={t("failingIps")} rows={logins.top_failed_ips} />
        </div>
      </CardContent>
    </Card>
  );
}

/** Actions by people per weekday and hour: what happens at 3 am stands out. */
export function HeatmapCard({ heatmap }: { heatmap: number[][] }) {
  const t = useTranslations("activity.insights.heatmap");
  const locale = useLocale();
  const max = Math.max(1, ...heatmap.flat());
  const total = heatmap.flat().reduce((a, b) => a + b, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {t("empty")}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <div
              className="grid min-w-[30rem] gap-0.5 text-[10px] text-muted-foreground"
              style={{
                gridTemplateColumns: "2.25rem repeat(24, minmax(0, 1fr))",
              }}
            >
              <span />
              {Array.from({ length: 24 }, (_, h) => (
                <span key={h} className="text-center">
                  {h % 3 === 0 ? t("hour", { hour: h }) : ""}
                </span>
              ))}
              {heatmap.map((row, d) => (
                <div key={d} className="contents">
                  <span className="pr-1 text-right leading-5">
                    {weekdayLabel(locale, d)}
                  </span>
                  {row.map((count, h) => (
                    <span
                      key={h}
                      title={t("cell", {
                        day: weekdayLabel(locale, d),
                        hour: h,
                        count,
                      })}
                      className="h-5 rounded-sm"
                      style={{
                        background:
                          count === 0
                            ? "var(--muted)"
                            : `rgba(37, 99, 235, ${0.2 + 0.8 * (count / max)})`,
                      }}
                    />
                  ))}
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("note")}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
