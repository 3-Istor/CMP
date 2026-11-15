"use client";

import {
  AlertTriangle,
  Ban,
  Gauge,
  KeyRound,
  Loader2,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useId, useMemo, useState } from "react";

import { TopologyCanvas } from "@/components/topology/TopologyCanvas";
import { TopologyLegend } from "@/components/topology/TopologyLegend";
import { SELECT_CLASS } from "@/components/topology/LinkDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  capacityGauge,
  effectiveService,
  finalName,
  planProblems,
  type PlanEditAction,
} from "@/lib/importPlan";
import {
  draftGraph,
  PLAN_PREFIX,
  planLinkKey,
  type EdgeAction,
  type GraphEdge,
  type GraphNode,
} from "@/lib/topology";
import { cn } from "@/lib/utils";
import type { ImportCapacity, ImportPlan, ImportPlanEdits, Topology } from "@/types";

function Panel({
  title,
  icon: Icon,
  tone = "default",
  children,
}: {
  title: string;
  icon: typeof Gauge;
  tone?: "default" | "warn" | "danger";
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "space-y-2 rounded-xl border p-4",
        tone === "warn" && "border-amber-600/40",
        tone === "danger" && "border-destructive/50",
      )}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="h-4 w-4" aria-hidden />
        {title}
      </h3>
      {children}
    </section>
  );
}

function CapacityPanel({
  plan,
  edits,
  capacity,
}: {
  plan: ImportPlan;
  edits: ImportPlanEdits;
  capacity: ImportCapacity;
}) {
  const t = useTranslations("imports.review");
  const gauge = capacityGauge(plan, edits, capacity);
  const pct = (mib: number) => `${Math.min(100, (mib / gauge.maxMiB) * 100)}%`;
  return (
    <Panel title={t("capacity")} icon={Gauge} tone={gauge.over ? "danger" : "default"}>
      <div aria-hidden className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-muted-foreground/50" style={{ width: pct(gauge.otherMiB) }} />
        <div
          className={cn("h-full", gauge.over ? "bg-destructive" : "bg-primary")}
          style={{ width: pct(gauge.requestedMiB) }}
        />
      </div>
      <p className={cn("text-sm", gauge.over && "font-medium text-destructive")}>
        {t("capacityText", {
          requested: gauge.requestedMiB,
          other: gauge.otherMiB,
          max: gauge.maxMiB,
        })}
      </p>
      {gauge.tooBig.length > 0 && (
        <p className="text-sm text-destructive">
          {t("namespaceTooSmall", { apps: gauge.tooBig.join(", "), max: capacity.namespace_max_mib })}
        </p>
      )}
    </Panel>
  );
}

function AppEditor({
  plan,
  edits,
  planName,
  dispatch,
  removedLinks,
}: {
  plan: ImportPlan;
  edits: ImportPlanEdits;
  planName: string;
  dispatch: (action: PlanEditAction) => void;
  removedLinks: Set<string>;
}) {
  const t = useTranslations("imports.review.editor");
  const ids = {
    name: useId(),
    port: useId(),
    probeKind: useId(),
    probePath: useId(),
    probePort: useId(),
    requests: useId(),
    limits: useId(),
    public: useId(),
  };
  const service = effectiveService(plan, edits, planName);
  const backing = plan.backing.find((b) => b.name === planName);
  const excluded = edits.exclude.includes(planName);
  const links = plan.links.filter((l) => l.from === planName || l.to === planName);
  const [nameDraft, setNameDraft] = useState(finalName(edits, planName));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="truncate font-semibold">{finalName(edits, planName)}</h3>
        <Button
          size="sm"
          variant={excluded ? "default" : "outline"}
          onClick={() => dispatch({ type: "toggleExclude", app: planName })}
        >
          {excluded ? t("include") : t("exclude")}
        </Button>
      </div>
      {planName !== finalName(edits, planName) && (
        <p className="text-xs text-muted-foreground">{t("renamedFrom", { name: planName })}</p>
      )}
      <div className="space-y-1.5">
        <Label htmlFor={ids.name}>{t("name")}</Label>
        <Input
          id={ids.name}
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => dispatch({ type: "rename", app: planName, name: nameDraft })}
          disabled={excluded}
          className="font-mono"
          spellCheck={false}
        />
      </div>

      {backing && (
        <p className="text-sm text-muted-foreground">
          {backing.supported ? t("catalogue", { kind: backing.kind }) : t("notCatalogue", { kind: backing.kind })}
        </p>
      )}

      {service && (
        <>
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor={ids.public}>{t("public")}</Label>
            <Switch
              id={ids.public}
              checked={service.public}
              disabled={excluded}
              onCheckedChange={(value: boolean) => dispatch({ type: "setPublic", app: planName, value })}
            />
          </div>
          {service.ports.length > 0 && (
            <div className="space-y-1.5">
              <Label htmlFor={ids.port}>{t("port")}</Label>
              <Input
                id={ids.port}
                type="number"
                min={1}
                max={65535}
                value={service.ports[0].port}
                disabled={excluded}
                onChange={(e) => {
                  const port = Number(e.target.value);
                  if (port >= 1 && port <= 65535) dispatch({ type: "setPort", app: planName, port });
                }}
              />
            </div>
          )}
          <fieldset className="space-y-2" disabled={excluded}>
            <legend className="text-sm font-medium">{t("probe")}</legend>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor={ids.probeKind} className="text-xs">
                  {t("probeKind")}
                </Label>
                <select
                  id={ids.probeKind}
                  className={SELECT_CLASS}
                  value={service.probe?.kind ?? "tcp"}
                  onChange={(e) =>
                    dispatch({
                      type: "setProbe",
                      app: planName,
                      probe: {
                        kind: e.target.value as "tcp" | "http",
                        path: service.probe?.path ?? (e.target.value === "http" ? "/" : null),
                        port: service.probe?.port ?? service.ports[0]?.port ?? 80,
                      },
                    })
                  }
                >
                  <option value="tcp">TCP</option>
                  <option value="http">HTTP</option>
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor={ids.probePort} className="text-xs">
                  {t("probePort")}
                </Label>
                <select
                  id={ids.probePort}
                  className={SELECT_CLASS}
                  value={service.probe?.port ?? service.ports[0]?.port}
                  onChange={(e) =>
                    dispatch({
                      type: "setProbe",
                      app: planName,
                      probe: {
                        kind: service.probe?.kind ?? "tcp",
                        path: service.probe?.path ?? null,
                        port: Number(e.target.value),
                      },
                    })
                  }
                >
                  {service.ports.map((p) => (
                    <option key={p.port} value={p.port}>
                      {p.port}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {service.probe?.kind === "http" && (
              <div className="space-y-1">
                <Label htmlFor={ids.probePath} className="text-xs">
                  {t("probePath")}
                </Label>
                <Input
                  id={ids.probePath}
                  value={service.probe.path ?? ""}
                  placeholder="/health"
                  className="font-mono"
                  onChange={(e) =>
                    dispatch({
                      type: "setProbe",
                      app: planName,
                      probe: { ...service.probe!, path: e.target.value },
                    })
                  }
                />
              </div>
            )}
          </fieldset>
          <fieldset className="grid grid-cols-2 gap-2" disabled={excluded}>
            <legend className="col-span-2 text-sm font-medium">{t("memory")}</legend>
            <div className="space-y-1">
              <Label htmlFor={ids.requests} className="text-xs">
                {t("requests")}
              </Label>
              <Input
                id={ids.requests}
                type="number"
                min={16}
                max={8192}
                value={service.requests}
                onChange={(e) =>
                  dispatch({
                    type: "setMemory",
                    app: planName,
                    requests: Number(e.target.value),
                    limits: service.limits,
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={ids.limits} className="text-xs">
                {t("limits")}
              </Label>
              <Input
                id={ids.limits}
                type="number"
                min={16}
                max={8192}
                value={service.limits}
                onChange={(e) =>
                  dispatch({
                    type: "setMemory",
                    app: planName,
                    requests: service.requests,
                    limits: Number(e.target.value),
                  })
                }
              />
            </div>
          </fieldset>
        </>
      )}

      {links.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">{t("links")}</p>
          <ul className="space-y-1">
            {links.map((link) => {
              const removed = removedLinks.has(planLinkKey(link));
              return (
                <li key={planLinkKey(link)} className="flex items-center justify-between gap-2 text-sm">
                  <span className={cn("min-w-0 truncate font-mono text-xs", removed && "line-through")}>
                    {finalName(edits, link.from)} {"->"} {link.alias}:{edits.port[finalName(edits, link.to)] ?? link.port}
                  </span>
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => dispatch({ type: "toggleLink", from: link.from, to: link.to })}
                    aria-label={t(removed ? "restoreLinkOf" : "removeLinkOf", {
                      from: link.from,
                      to: link.to,
                    })}
                  >
                    {removed ? t("restoreLink") : t("removeLink")}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export function PlanReview({
  projectName,
  plan,
  edits,
  dispatch,
  capacity,
  live,
  applying,
  onApply,
  onAbandon,
}: {
  projectName: string;
  plan: ImportPlan;
  edits: ImportPlanEdits;
  dispatch: (action: PlanEditAction) => void;
  capacity: ImportCapacity;
  live: Topology | null;
  applying: boolean;
  onApply: () => void;
  onAbandon: () => void;
}) {
  const t = useTranslations("imports.review");
  const tProblem = useTranslations("imports.problem");
  const [selected, setSelected] = useState<string | null>(null);

  const graph = useMemo(() => draftGraph(plan, edits, live), [plan, edits, live]);
  const dimmed = useMemo(
    () => new Set(edits.exclude.map((name) => PLAN_PREFIX + name)),
    [edits.exclude],
  );
  const removedLinks = useMemo(() => new Set(edits.drop_links.map(planLinkKey)), [edits.drop_links]);
  const taken = useMemo(
    () => (live?.nodes ?? []).filter((n) => !n.imported).map((n) => n.app),
    [live],
  );
  const problems = planProblems(plan, edits, taken);
  const gauge = capacityGauge(plan, edits, capacity);
  const blocked = problems.length > 0 || gauge.over || gauge.tooBig.length > 0;

  const onSelectNode = useCallback((node: GraphNode | null) => {
    if (node?.draft) setSelected(node.id.slice(PLAN_PREFIX.length));
  }, []);
  const onEdgeAction = useCallback(
    (edge: GraphEdge, action: EdgeAction) => {
      if ((action === "remove" || action === "restore") && edge.planLink) {
        dispatch({ type: "toggleLink", ...edge.planLink });
      }
    },
    [dispatch],
  );

  const apps = [
    ...plan.services.map((s) => ({ name: s.name, kind: "service", confidence: s.confidence })),
    ...plan.backing.map((b) => ({
      name: b.name,
      kind: b.kind,
      confidence: b.supported ? null : { overall: "low" as const, reasons: [] },
    })),
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-2">
          <TopologyLegend draft />
          <TopologyCanvas
            graph={graph}
            ariaLabel={t("canvasLabel", { project: projectName })}
            canEdit
            dimmedNodes={dimmed}
            removedPlanLinks={removedLinks}
            onSelectNode={onSelectNode}
            onEdgeAction={onEdgeAction}
            className="h-[560px] w-full overflow-hidden rounded-xl border bg-muted/30"
          />
          <p className="text-xs text-muted-foreground">{t("canvasHelp")}</p>
        </div>
        <aside aria-label={t("editorLabel")} className="space-y-4 rounded-xl border p-4">
          {selected ? (
            <AppEditor
              key={selected}
              plan={plan}
              edits={edits}
              planName={selected}
              dispatch={dispatch}
              removedLinks={removedLinks}
            />
          ) : (
            <p className="text-sm text-muted-foreground">{t("selectHint")}</p>
          )}
          <div className="space-y-1.5 border-t pt-3">
            <p className="text-sm font-medium">{t("apps")}</p>
            <ul className="space-y-1">
              {apps.map((app) => (
                <li key={app.name}>
                  <button
                    type="button"
                    onClick={() => setSelected(app.name)}
                    aria-current={selected === app.name ? "true" : undefined}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-accent",
                      selected === app.name && "bg-primary/10",
                      edits.exclude.includes(app.name) && "text-muted-foreground line-through",
                    )}
                  >
                    <span className="truncate font-mono">{finalName(edits, app.name)}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {app.confidence
                        ? t(`confidenceShort.${app.confidence.overall}`)
                        : app.kind}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <CapacityPanel plan={plan} edits={edits} capacity={capacity} />
        <Panel title={t("secrets")} icon={KeyRound} tone={plan.secrets_found.length ? "warn" : "default"}>
          {plan.secrets_found.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noSecrets")}</p>
          ) : (
            <>
              <p className="flex items-start gap-1.5 text-sm text-muted-foreground">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                {t("secretsBody")}
              </p>
              <ul className="space-y-1 text-xs">
                {plan.secrets_found.map((secret) => (
                  <li key={`${secret.file}:${secret.line}`} className="font-mono">
                    {secret.file}:{secret.line} <span className="text-muted-foreground">{secret.redacted}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>
        <Panel title={t("unsupported")} icon={Ban} tone={plan.unsupported.length ? "warn" : "default"}>
          {plan.unsupported.length === 0 && plan.backing.every((b) => b.supported) ? (
            <p className="text-sm text-muted-foreground">{t("allSupported")}</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {plan.backing
                .filter((b) => !b.supported)
                .map((b) => (
                  <li key={b.name}>
                    <span className="font-medium">{b.name}</span>{" "}
                    <span className="text-muted-foreground">{t("backingNotInCatalogue", { kind: b.kind })}</span>
                  </li>
                ))}
              {plan.unsupported.map((item) => (
                <li key={`${item.name}-${item.file}-${item.line}`}>
                  <span className="font-medium">{item.name}</span>{" "}
                  <span className="text-muted-foreground">{item.reason}</span>
                  {item.file && (
                    <span className="block font-mono text-xs text-muted-foreground">
                      {item.file}
                      {item.line ? `:${item.line}` : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title={t("warnings")} icon={AlertTriangle}>
          {plan.warnings.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noWarnings")}</p>
          ) : (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {plan.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {problems.length > 0 && (
        <div role="alert" className="rounded-xl border border-destructive bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">{t("blocked")}</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {problems.map((p) => (
              <li key={`${p.code}-${"app" in p ? p.app : ""}`}>
                {tProblem(p.code, { app: "app" in p ? p.app : "" })}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <Button variant="ghost" onClick={onAbandon} disabled={applying}>
          <RotateCcw aria-hidden />
          {t("abandon")}
        </Button>
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-xs text-muted-foreground">{t("stepUpNote")}</p>
          <Button onClick={onApply} disabled={blocked || applying}>
            {applying && <Loader2 className="animate-spin" aria-hidden />}
            {t("apply")}
          </Button>
        </div>
      </div>
    </div>
  );
}
