"use client";

import { CLOUD_META } from "@/components/finops/format";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import AnimatedList from "@/components/ui/AnimatedList";
import CountUp from "@/components/ui/CountUp";
import ShinyText from "@/components/ui/ShinyText";
import StarBorder from "@/components/ui/StarBorder";
import Stepper, { Step } from "@/components/ui/Stepper";
import {
  getProjectPlacement,
  type PlacementPod,
  type ProjectPlacement,
} from "@/lib/api";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { pollWhileVisible } from "@/lib/visiblePolling";
import { cn } from "@/lib/utils";
import { ExternalLink, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

type Cloud = keyof typeof CLOUD_META;

const POLL_MS = 5000;
// Faster while a move is active, so the steps and pods follow it.
const POLL_MOVING_MS = 2000;
// Stop-to-healthy window of a move: the apps are down during these steps.
const CUTOVER_STEPS = ["stop_source", "final_backup", "switch", "wait_target"];

const DOT: Record<string, string> = {
  Running: "bg-emerald-500",
  Succeeded: "bg-emerald-500",
  Failed: "bg-destructive",
  Terminating: "bg-muted-foreground",
};
const dotColor = (status: string) => DOT[status] ?? "bg-amber-500";
const isTransient = (status: string) =>
  !["Running", "Succeeded", "Failed"].includes(status);

const seconds = (from: string | null, to: string | null) =>
  from && to ? Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000) : 0;

export function PlacementPanel() {
  const isAdmin = usePlatformAdmin();
  const { projectName, move } = useProjectContext();
  const t = useTranslations("placement");
  const tSteps = useTranslations("projectSettings.location.steps");
  const [data, setData] = useState<ProjectPlacement | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<
    (PlacementPod & { node: string; cloud: Cloud }) | null
  >(null);
  const moving = move?.status === "running";

  useEffect(() => {
    if (!isAdmin) return;
    const load = () =>
      getProjectPlacement(projectName)
        .then((d) => {
          setData(d);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    void load();
    return pollWhileVisible(load, moving ? POLL_MOVING_MS : POLL_MS);
  }, [isAdmin, projectName, moving]);

  if (!isAdmin) return null;

  const source = move?.source as Cloud | undefined;
  const target = move?.target as Cloud | undefined;
  const dbCloud = data?.clusters.find((c) =>
    c.nodes.some((n) => n.pods.some((p) => p.kind === "db")),
  )?.cloud;
  const here = target ?? dbCloud;
  const readyOnHere = (
    data?.clusters.find((c) => c.cloud === here)?.nodes ?? []
  )
    .flatMap((n) => n.pods)
    .filter((p) => p.status === "Running").length;
  const cutover = Math.round(
    (move?.steps ?? [])
      .filter((s) => CUTOVER_STEPS.includes(s.name))
      .reduce((sum, s) => sum + seconds(s.started_at, s.finished_at), 0),
  );
  const stepIndex = move
    ? (move.steps.findIndex((s) => s.status !== "done") + 1 ||
        move.steps.length + 1)
    : 1;
  const events = (move?.steps ?? [])
    .filter((s) => s.status !== "pending")
    .map((s) => ({
      id: s.name,
      title: tSteps(s.name),
      description: s.detail ?? s.status,
      meta: s.started_at
        ? new Date(s.started_at).toLocaleTimeString()
        : undefined,
    }))
    .reverse();

  return (
    <Card aria-labelledby="placement-title">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <h2 id="placement-title" className="font-heading text-base font-medium">
            {t("title")}
          </h2>
          <Badge variant="secondary">{t("admin")}</Badge>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 text-xs",
              failed ? "text-destructive" : "text-emerald-700",
            )}
          >
            <span
              className={cn(
                "size-2 rounded-full motion-safe:animate-pulse",
                failed ? "bg-destructive" : "bg-emerald-500",
              )}
            />
            {failed ? t("loadFailed") : t("live")}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {data?.clusters.map((c) => (
            <a key={c.cloud} href={c.console_url} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
              <Swatch cloud={c.cloud} />
              {t(`console.${c.cloud}`)}
              <ExternalLink aria-hidden />
            </a>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label={t("runsOn")}
            hint={
              move
                ? t("step", {
                    n: Math.min(stepIndex, move.steps.length),
                    total: move.steps.length,
                    name: tSteps(move.steps[Math.min(stepIndex, move.steps.length) - 1].name),
                  })
                : undefined
            }
          >
            {moving && source && target ? (
              <ShinyText
                text={`${CLOUD_META[source].label} → ${CLOUD_META[target].label}`}
              />
            ) : (
              <span>{here ? CLOUD_META[here].label : "–"}</span>
            )}
          </Stat>
          <Stat label={t("cutover")} hint={t("cutoverHint")}>
            {cutover > 0 ? <CountUp trigger="load" to={cutover} suffix=" s" /> : "–"}
          </Stat>
          <Stat label={t("ready")} hint={t("readyHint")}>
            <CountUp trigger="load" to={readyOnHere} />
          </Stat>
          <Stat label={t("database")} hint={t("generation")}>
            {data?.database?.generation != null ? (
              <CountUp trigger="load" to={data.database.generation} />
            ) : (
              "–"
            )}
          </Stat>
        </div>

        {move && (
          <Stepper
            step={stepIndex}
            theme="light"
            frame={false}
            showFooter={false}
            disableStepIndicators
            transition="none"
            width="100%"
          >
            {move.steps.map((s) => (
              <Step key={s.name} title={tSteps(s.name)}>
                {null}
              </Step>
            ))}
          </Stepper>
        )}

        <div className="grid gap-3.5 lg:grid-cols-3">
          {data?.clusters.map((c) => {
            const isTarget = moving && c.cloud === target;
            const isSource = moving && c.cloud === source;
            const pods = c.nodes.flatMap((n) => n.pods);
            const lane = (
              <div className="flex h-full w-full min-w-0 flex-col gap-3 rounded-xl bg-card p-4">
                <div className="flex items-start justify-between gap-2">
                  <span className="flex items-center gap-2 font-semibold">
                    <Swatch cloud={c.cloud} />
                    {CLOUD_META[c.cloud].label}
                  </span>
                  <span className="flex flex-col items-end gap-1">
                    <Badge variant={c.state === "up" ? "secondary" : "destructive"}>
                      {t.has(`state.${c.state}`) ? t(`state.${c.state}`) : c.state}
                    </Badge>
                    {(isSource || isTarget) && (
                      <span className="text-xs font-medium text-muted-foreground">
                        {isSource ? t("source") : t("target")}
                      </span>
                    )}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {pods.length}
                  </span>{" "}
                  {t("pods", { count: pods.length, project: projectName })}
                </p>
                {c.nodes.length === 0 && (
                  <p className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">
                    {isTarget ? t("arriving") : t("empty")}
                  </p>
                )}
                {c.nodes.map((n) => (
                  <div key={n.name} className="overflow-hidden rounded-lg border">
                    <div className="flex justify-between gap-2 bg-muted/50 px-2.5 py-1.5 text-[11px] text-muted-foreground">
                      <span className="truncate font-mono">{n.name}</span>
                      <span className="whitespace-nowrap">{n.instance_type}</span>
                    </div>
                    {n.pods.map((p) => (
                      <button
                        key={`${p.namespace}/${p.name}`}
                        type="button"
                        onClick={() => setSelected({ ...p, node: n.name, cloud: c.cloud })}
                        className="flex w-full items-center gap-2.5 border-t px-2.5 py-2 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
                      >
                        <span className="grid h-6 w-9 shrink-0 place-items-center rounded-md bg-muted text-[10px] font-semibold uppercase">
                          {p.kind}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-xs">{p.name}</span>
                          <span className="block text-[11px] text-muted-foreground">
                            {p.namespace}
                          </span>
                        </span>
                        <span className="inline-flex items-center gap-1.5 text-xs">
                          <span
                            className={cn(
                              "size-2 rounded-full",
                              dotColor(p.status),
                              isTransient(p.status) && "motion-safe:animate-pulse",
                            )}
                          />
                          {t.has(`podStatus.${p.status}`) ? t(`podStatus.${p.status}`) : p.status}
                        </span>
                      </button>
                    ))}
                  </div>
                ))}
                {c.cloud === "onprem" && data.backups.length > 0 && (
                  <div className="space-y-1 rounded-lg border bg-muted/30 p-3 text-[11px]">
                    <p className="text-sm font-semibold">{t("backups")}</p>
                    {data.backups.map((b) => (
                      <p key={b.claim} className="break-all font-mono text-muted-foreground">
                        {b.bucket && <span className="block">{t("bucket", { name: b.bucket })}</span>}
                        <span className="block">{t("claim", { name: b.claim })}</span>
                      </p>
                    ))}
                  </div>
                )}
              </div>
            );
            return isTarget ? (
              <StarBorder
                as="div"
                key={c.cloud}
                theme="light"
                color={CLOUD_META[c.cloud].color}
                radius={14}
                className="flex! h-full w-full items-stretch! justify-start! p-0! text-left font-normal"
              >
                {lane}
              </StarBorder>
            ) : (
              <div key={c.cloud} className="rounded-xl border">
                {lane}
              </div>
            );
          })}
        </div>

        {selected && (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border p-3.5 text-sm">
            <Field label={t("pod")} value={selected.name} mono />
            <Field label={t("namespace")} value={selected.namespace} />
            <Field label={t("node")} value={selected.node} mono />
            <Field label={t("status")} value={selected.status} />
            <a
              href={data?.clusters.find((c) => c.cloud === selected.cloud)?.console_url}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              {t("openIn", { console: t(`console.${selected.cloud}`) })}
              <ExternalLink aria-hidden />
            </a>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setSelected(null)}
              aria-label={t("close")}
            >
              <X aria-hidden />
            </Button>
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-5">
          <section aria-label={t("events")} className="lg:col-span-3">
            <h3 className="mb-2 text-sm font-semibold">{t("events")}</h3>
            {events.length ? (
              <AnimatedList
                items={events}
                theme="light"
                frame={false}
                animation="slide"
                maxHeight={280}
                width="100%"
                showGradients={false}
              />
            ) : (
              <p className="text-sm text-muted-foreground">{t("noEvents")}</p>
            )}
          </section>
          <section aria-label={t("data")} className="space-y-3 lg:col-span-2">
            <h3 className="text-sm font-semibold">{t("data")}</h3>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("dbName")} value={data?.database?.name ?? "–"} mono />
              <Field label={t("onCluster")} value={dbCloud ? CLOUD_META[dbCloud].label : "–"} />
              <Field
                label={t("wal")}
                value={
                  data?.database?.last_archived_wal
                    ? new Date(data.database.last_archived_wal).toLocaleTimeString()
                    : "–"
                }
              />
            </div>
          </section>
        </div>
      </CardContent>
    </Card>
  );
}

function Swatch({ cloud }: { cloud: Cloud }) {
  return (
    <span
      aria-hidden
      className="inline-block size-2.5 shrink-0 rounded-[3px]"
      style={{ background: CLOUD_META[cloud].color }}
    />
  );
}

function Stat({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border p-3.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xl font-semibold tabular-nums">{children}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn("break-all text-sm", mono && "font-mono text-xs")}>{value}</span>
    </div>
  );
}
