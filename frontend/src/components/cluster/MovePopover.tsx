"use client";

import { errorMessage } from "@/components/activity/labels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CLOUD_META } from "@/components/finops/format";
import { type LastWal, type ProjectMove, getLastWal, getMoveDataLoss, startMove } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Popover } from "@base-ui/react/popover";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { ClusterGlyph, type GlyphCloud } from "./ClusterGlyph";

type MoveTarget = "onprem" | "public";
type Cloud = "onprem" | "aws" | "gcp";
type Target = { target: MoveTarget; cloud: GlyphCloud; label: string; state: string };

const label = (c: Cloud) => CLOUD_META[c].label;

/**
 * Two-click move. Click 1 opens the targets, click 2 starts the move. A step is added only when something
 * can be lost (source not up, or data that starts empty): the loss is spelled out and the project name typed.
 * `states` is null until cluster health is known; the trigger stays disabled meanwhile.
 */
export function MovePopover({
  project,
  cloud,
  states,
  memory,
  onStarted,
}: {
  project: string;
  cloud: Cloud;
  states: Record<string, string> | null;
  memory?: Record<string, number | null>;
  onStarted?: (move: ProjectMove) => void;
}) {
  const t = useTranslations("cluster");
  const tl = useTranslations("projectSettings.location");
  const tc = useTranslations("confirm");
  const tState = useTranslations("placement.state");
  const nameId = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Target | null>(null);
  const [lost, setLost] = useState<string[]>([]);
  const [wal, setWal] = useState<LastWal[]>([]);
  const [dataLoss, setDataLoss] = useState(false);
  const [typed, setTyped] = useState("");

  const stateOf = (c: Cloud) => states?.[c] ?? "up";
  const sourceState = stateOf(cloud);
  const sourceDown = sourceState !== "up";
  const other: Cloud = cloud === "aws" ? "gcp" : "aws";
  const targets: Target[] =
    cloud === "onprem"
      ? [
          {
            target: "public",
            cloud: "public",
            label: t("publicCloud"),
            // The platform picks the public cloud: the best state of the two.
            state: ["up", "degraded"].find((s) => (["aws", "gcp"] as Cloud[]).some((c) => stateOf(c) === s)) ?? stateOf("aws"),
          },
        ]
      : [
          { target: "onprem", cloud: "onprem", label: label("onprem"), state: "up" },
          { target: "public", cloud: other, label: label(other), state: stateOf(other) },
        ];

  const reset = () => {
    setPending(null);
    setLost([]);
    setWal([]);
    setDataLoss(false);
    setTyped("");
    setError(null);
    setBusy(false);
  };

  const run = async (target: Target, confirmLoss: boolean) => {
    setBusy(true);
    setError(null);
    try {
      onStarted?.(await startMove(project, confirmLoss, target.target));
      setOpen(false);
      reset();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  const pick = async (target: Target) => {
    if (target.state !== "up" || busy) return;
    setBusy(true);
    setError(null);
    try {
      const [loss, last] = await Promise.all([
        getMoveDataLoss(project).catch(() => [] as string[]),
        sourceDown ? getLastWal(project).catch(() => [] as LastWal[]) : Promise.resolve([] as LastWal[]),
      ]);
      if (!sourceDown && loss.length === 0) {
        await run(target, false);
        return;
      }
      setLost(loss);
      setWal(last);
      setPending(target);
      setBusy(false);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  const ready = typed === project && (lost.length === 0 || dataLoss);

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <Popover.Trigger render={<Button variant="outline" size="sm" disabled={!states} />}>
        {t("move")}
        <ArrowRight aria-hidden />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup
            aria-label={t("moveTitle", { project })}
            className="grid w-80 max-w-[calc(100vw-2rem)] gap-3 rounded-xl border bg-popover p-3.5 text-popover-foreground shadow-xl outline-none"
          >
            <Popover.Title className="flex items-center gap-2 font-heading text-sm font-medium">
              <ClusterGlyph cloud={cloud} state={sourceState} size="sm" className="mx-1" />
              {pending ? (
                <>
                  <ArrowRight aria-hidden className="size-3.5 text-muted-foreground" />
                  <ClusterGlyph cloud={pending.cloud} state={pending.state} size="sm" className="mx-1" />
                  {t("moveTo", { project, cloud: pending.label })}
                </>
              ) : (
                t("moveTitle", { project })
              )}
            </Popover.Title>

            {!pending &&
              targets.map((o) => {
                const off = o.state !== "up";
                const mem = o.target === "public" && o.cloud !== "public" ? memory?.[o.cloud] : o.cloud === "onprem" ? memory?.onprem : undefined;
                return (
                  <button
                    key={o.target}
                    type="button"
                    aria-disabled={off || busy}
                    onClick={() => void pick(o)}
                    className={cn(
                      "grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border p-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      off || busy ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-primary/50 hover:bg-muted",
                    )}
                  >
                    <ClusterGlyph cloud={o.cloud} state={o.state} size="sm" className="mx-1" />
                    <span className="grid leading-tight">
                      <span className="text-sm font-medium">{o.label}</span>
                      <span className="text-xs text-muted-foreground">
                        {off
                          ? tl("notUp", { cloud: o.label })
                          : mem != null
                            ? t("room", { pct: Math.round(mem) })
                            : tState.has(o.state)
                              ? tState(o.state)
                              : o.state}
                      </span>
                    </span>
                    {busy && !off ? <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" /> : off ? null : <ArrowRight aria-hidden className="size-4 text-muted-foreground" />}
                  </button>
                );
              })}

            {pending && (
              <div className="grid gap-3">
                <div className="grid gap-1.5 rounded-md border-l-[3px] border-destructive bg-destructive/10 px-2.5 py-2 text-xs">
                  {sourceDown && (
                    <p className="font-medium">{t("sourceIs", { cloud: label(cloud), state: tState.has(sourceState) ? tState(sourceState) : sourceState })}</p>
                  )}
                  {sourceDown &&
                    wal.map((w) => (
                      <p key={w.database}>
                        {w.last_wal_at
                          ? tl("lastWal", { time: new Date(w.last_wal_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }), database: w.database })
                          : tl(w.reason ? "lastWalUnknown" : "lastWalNone", { database: w.database })}
                      </p>
                    ))}
                  {lost.length > 0 && (
                    <label className="flex items-center gap-2">
                      <input type="checkbox" checked={dataLoss} onChange={(e) => setDataLoss(e.target.checked)} />
                      {tl("dataLoss", { names: lost.join(", ") })}
                    </label>
                  )}
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor={nameId}>{tc("typeToConfirm", { name: project })}</Label>
                  <Input
                    id={nameId}
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    placeholder={project}
                    autoComplete="off"
                    spellCheck={false}
                    autoFocus
                    onKeyDown={(e) => e.key === "Enter" && ready && !busy && void run(pending, dataLoss || sourceDown)}
                  />
                </div>
                <div className="flex justify-between gap-2">
                  <Button variant="ghost" size="sm" disabled={busy} onClick={reset}>
                    <ArrowLeft aria-hidden />
                    {t("back")}
                  </Button>
                  <Button variant="destructive" size="sm" disabled={!ready || busy} onClick={() => void run(pending, dataLoss || sourceDown)}>
                    {busy ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : null}
                    {tl("confirmLabel")}
                  </Button>
                </div>
              </div>
            )}

            {error && (
              <p role="alert" className="text-xs text-destructive">
                {tc("failed", { message: error })}
              </p>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
