"use client";

import { ArrowRight, Bot, Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  applyImport,
  dismissRecommendation,
  getAgentConfirmation,
  getImportRun,
  getImports,
  getTopology,
  rebuildImport,
  startImport,
} from "@/lib/api";
import { emptyEdits, planEditsReducer, type PlanEditAction } from "@/lib/importPlan";
import { importRefusal } from "@/lib/importRefusal";
import { cn } from "@/lib/utils";
import type {
  AgentConfirmation,
  ImportPlan,
  ImportPlanEdits,
  ImportRun,
  ImportRunDetail,
  ImportSourceMode,
  ImportSourceView,
  Topology,
} from "@/types";
import { AnalysisStep } from "./AnalysisStep";
import { ImportProgress } from "./ImportProgress";
import { PlanReview } from "./PlanReview";
import { RepoStep } from "./RepoStep";
import { pollWhileVisible } from "@/lib/visiblePolling";

const POLL_MS = 3000;
const STEPS = ["repo", "analysis", "review", "progress"] as const;
type Step = (typeof STEPS)[number];
const EDITS_KEY = "cnp-import-edits:";

// Kept across the passkey step-up: Keycloak sends the user back to this
// page, where the reviewed edits must still be there.
function loadEdits(runId: string): ImportPlanEdits | null {
  try {
    const raw = sessionStorage.getItem(EDITS_KEY + runId);
    return raw ? { ...emptyEdits(), ...(JSON.parse(raw) as ImportPlanEdits) } : null;
  } catch {
    return null;
  }
}

function saveEdits(runId: string, edits: ImportPlanEdits) {
  try {
    sessionStorage.setItem(EDITS_KEY + runId, JSON.stringify(edits));
  } catch {
    // storage blocked: the edits are lost on a step-up
  }
}

function stepOf(run: ImportRunDetail | null): Step {
  if (!run) return "repo";
  if (run.kind === "build") return "progress";
  return run.status === "succeeded" ? "review" : "analysis";
}

// The analysis a build applied and the build that applied an analysis are
// not linked by the API: the closest run of the same source stands in.
function analysisBefore(runs: ImportRun[], build: ImportRun): ImportRun | null {
  return (
    runs
      .filter((r) => r.kind === "analyze" && r.status === "succeeded" && r.created_at <= build.created_at)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  );
}

function buildAfter(runs: ImportRun[], analysis: ImportRun): ImportRun | null {
  const build = runs
    .filter((r) => r.kind === "build" && r.created_at >= analysis.created_at)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
  return build && analysisBefore(runs, build)?.id === analysis.id ? build : null;
}

function isPlan(plan: ImportRunDetail["plan"]): plan is ImportPlan {
  return Boolean(plan && "services" in plan);
}

export function ImportWizard() {
  const params = useSearchParams();
  const runId = params.get("run");
  // A new run is a new wizard: nothing of the previous one carries over.
  return (
    <WizardForRun
      key={runId ?? "new"}
      runId={runId}
      draftId={params.get("draft")}
      requestedStep={params.get("step")}
      initialRepo={params.get("repo") ?? undefined}
    />
  );
}

function WizardForRun({
  runId,
  draftId,
  requestedStep,
  initialRepo,
}: {
  runId: string | null;
  draftId: string | null;
  requestedStep: string | null;
  // Set when a project was just created from a repository.
  initialRepo?: string;
}) {
  const t = useTranslations("imports");
  const router = useRouter();
  const pathname = usePathname();
  const { projectName, can } = useProjectContext();

  const [run, setRun] = useState<ImportRunDetail | null>(null);
  const [source, setSource] = useState<ImportSourceView | null>(null);
  const [live, setLive] = useState<Topology | null>(null);
  const [loading, setLoading] = useState(Boolean(runId));
  const [applying, setApplying] = useState(false);
  const [draft, setDraft] = useState<AgentConfirmation | null>(null);
  // Edits kept across a step-up win over the assistant's draft.
  const keptEdits = useRef(Boolean(runId && loadEdits(runId)));
  const plan = run && isPlan(run.plan) ? run.plan : null;

  const [edits, rawDispatch] = useReducer(
    (state: ImportPlanEdits, action: PlanEditAction) =>
      plan ? planEditsReducer(state, action, plan) : state,
    runId,
    (id) => (id ? loadEdits(id) : null) ?? emptyEdits(),
  );
  const dispatch = useCallback(
    (action: PlanEditAction) => rawDispatch(action),
    [],
  );

  const go = useCallback(
    (next: string | null) => {
      router.replace(next ? `${pathname}?run=${encodeURIComponent(next)}` : pathname);
    },
    [pathname, router],
  );

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      const detail = await getImportRun(projectName, runId);
      setRun(detail);
      if (!source || source.id !== detail.source_id) {
        const sources = await getImports(projectName);
        setSource(sources.find((s) => s.id === detail.source_id) ?? null);
      }
    } catch (err) {
      toast.error(t("loadFailed", { message: importRefusal(err).message }));
    } finally {
      setLoading(false);
    }
  }, [projectName, runId, source, t]);

  useEffect(() => {
    if (!runId) return;
    void load();
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = run && !["succeeded", "failed", "cancelled"].includes(run.status);
  useEffect(() => {
    if (!running) return;
    return pollWhileVisible(() => void load(), POLL_MS);
  }, [running, load]);

  const step = stepOf(run);
  // A finished step can be opened again without running it again.
  const [viewed, setViewed] = useState<Step | null>(
    STEPS.includes(requestedStep as Step) ? (requestedStep as Step) : null,
  );
  const shown = viewed && STEPS.indexOf(viewed) < STEPS.indexOf(step) && run?.kind !== "build" ? viewed : step;
  useEffect(() => {
    if (step !== "review") return;
    getTopology(projectName)
      .then(setLive)
      .catch(() => setLive(null));
  }, [step, projectName]);

  useEffect(() => {
    if (runId && step === "review") saveEdits(runId, edits);
  }, [runId, step, edits]);

  useEffect(() => {
    if (!draftId || step !== "review") return;
    getAgentConfirmation(draftId)
      .then((found) => {
        // A draft proposed for another project or analysis is never shown here.
        if (
          found.status !== "pending" ||
          found.tool !== "import_repo" ||
          found.project !== projectName ||
          found.draft_run_id !== runId
        )
          return;
        setDraft(found);
        if (!keptEdits.current && found.draft) {
          rawDispatch({ type: "reset", edits: { ...emptyEdits(), ...found.draft } });
        }
      })
      .catch(() => setDraft(null));
  }, [draftId, step, projectName, runId]);

  // A refusal caused by another run offers to open it: the run in
  // progress, or the analysis that replaced the one under review.
  const refused = (key: "startFailed" | "applyFailed", err: unknown) => {
    const { message, latestRun } = importRefusal(err);
    toast.error(t(key, { message }), {
      action: latestRun
        ? { label: t("openLatestRun"), onClick: () => go(latestRun.run_id) }
        : undefined,
    });
  };

  const start = async (repo: string, branch: string | null, mode: ImportSourceMode) => {
    try {
      const next = await startImport(projectName, repo, branch, mode);
      go(next.id);
    } catch (err) {
      refused("startFailed", err);
    }
  };

  const apply = async () => {
    if (!run) return;
    setApplying(true);
    try {
      const build = await applyImport(projectName, run.source_id, run.id, edits, draft?.id);
      toast.success(t("applied"));
      go(build.id);
    } catch (err) {
      refused("applyFailed", err);
    } finally {
      setApplying(false);
    }
  };

  const rebuild = async () => {
    if (!run) return;
    try {
      go((await rebuildImport(projectName, run.source_id)).id);
    } catch (err) {
      refused("startFailed", err);
    }
  };

  const reanalyze = () => {
    if (source) void start(source.origin_repo_full_name ?? source.repo_full_name, source.branch, source.source_mode);
    else go(null);
  };

  const current = STEPS.indexOf(step);
  const shownIndex = STEPS.indexOf(shown);
  const done = (index: number) =>
    index < current || (index === current && run?.kind === "build" && run.status === "succeeded");
  const runs = source?.runs;
  const otherRun = useMemo(() => {
    if (!run || !runs) return null;
    return run.kind === "build" ? analysisBefore(runs, run) : buildAfter(runs, run);
  }, [run, runs]);

  const openStep = (target: Step) => {
    if (STEPS.indexOf(target) <= current) setViewed(target === step ? null : target);
    else if (otherRun) go(otherRun.id);
  };

  // From a build, the earlier steps belong to the analysis it applied.
  const openFromBuild = (target: Step) => {
    if (otherRun && target !== "progress") {
      router.replace(`${pathname}?run=${encodeURIComponent(otherRun.id)}&step=${target}`);
    }
  };

  const reachable = (index: number) => {
    if (index === shownIndex) return false;
    if (run?.kind === "build") return index === current || Boolean(otherRun);
    return index <= current || (index === current + 1 && STEPS[index] === "progress" && Boolean(otherRun));
  };

  const stepper = (
    <ol aria-label={t("stepsLabel")} className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
      {STEPS.map((s, index) => {
        const label = (
          <>
            <span
              className={cn(
                "grid h-6 w-6 place-items-center rounded-full border text-xs",
                done(index) && "border-primary bg-primary text-primary-foreground",
                index === shownIndex && !done(index) && "border-primary text-primary",
                index === shownIndex && done(index) && "ring-2 ring-primary ring-offset-2 ring-offset-background",
              )}
              aria-hidden
            >
              {done(index) ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            {t(`step.${s}`)}
          </>
        );
        return (
          <li
            key={s}
            aria-current={index === shownIndex ? "step" : undefined}
            className={cn(index === shownIndex ? "font-medium text-foreground" : "text-muted-foreground")}
          >
            {reachable(index) ? (
              <button
                type="button"
                className="flex items-center gap-2 rounded-md hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => (run?.kind === "build" ? openFromBuild(s) : openStep(s))}
                title={t("openStep", { step: t(`step.${s}`) })}
              >
                {label}
              </button>
            ) : (
              <span className="flex items-center gap-2">{label}</span>
            )}
          </li>
        );
      })}
    </ol>
  );

  const nextStep = shownIndex < current ? STEPS[shownIndex + 1] : null;
  const continueButton = nextStep && (
    <Button size="sm" onClick={() => openStep(nextStep)}>
      {t("continueTo", { step: t(`step.${nextStep}`) })}
      <ArrowRight aria-hidden />
    </Button>
  );

  return (
    <div className="space-y-6">
      {stepper}
      {source && (
        <p className="text-sm text-muted-foreground">
          {t("sourceLine", { repo: source.repo_full_name, branch: source.branch })}
        </p>
      )}
      {loading ? (
        <Skeleton className="h-64 w-full rounded-xl" />
      ) : shown === "repo" && run ? (
        <div className="space-y-4 rounded-xl border p-4 text-sm">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2">
            <dt className="text-muted-foreground">{t("summary.repo")}</dt>
            <dd className="font-mono">{source?.origin_repo_full_name ?? source?.repo_full_name ?? "…"}</dd>
            <dt className="text-muted-foreground">{t("summary.branch")}</dt>
            <dd className="font-mono">{source?.branch ?? "…"}</dd>
            <dt className="text-muted-foreground">{t("summary.commit")}</dt>
            <dd className="font-mono">{run.sha.slice(0, 12)}</dd>
            <dt className="text-muted-foreground">{t("summary.mode")}</dt>
            <dd>{source ? t(`summary.modes.${source.source_mode}`) : "…"}</dd>
          </dl>
          <div className="flex flex-wrap gap-2">
            {continueButton}
            <Button size="sm" variant="outline" onClick={() => go(null)}>
              {t("summary.otherRepo")}
            </Button>
          </div>
        </div>
      ) : shown === "repo" ? (
        <RepoStep projectName={projectName} initialRepo={initialRepo} onStart={start} />
      ) : shown === "analysis" && run ? (
        <div className="space-y-4">
          <AnalysisStep run={run} repo={source?.repo_full_name ?? null} onRetry={reanalyze} onRestart={() => go(null)} />
          {continueButton}
        </div>
      ) : shown === "review" && run && plan ? (
        <>
        {draft && (
          <p role="note" className="flex items-start gap-2 rounded-xl border bg-muted/40 px-3 py-2 text-sm">
            <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            {t("draftNote", { user: draft.username })}
          </p>
        )}
        <PlanReview
          projectName={projectName}
          plan={plan}
          edits={edits}
          dispatch={dispatch}
          capacity={run.capacity}
          live={live}
          applying={applying}
          onApply={() => void apply()}
          runId={run.id}
          estimate={run.estimate ?? null}
          dismissedRecommendations={run.dismissed_recommendations ?? []}
          onDismissRecommendation={async (id) => {
            await dismissRecommendation(projectName, id);
            await load();
          }}
          onAbandon={() => {
            try {
              sessionStorage.removeItem(EDITS_KEY + run.id);
            } catch {
              // nothing kept
            }
            router.push(`/projects/${encodeURIComponent(projectName)}`);
          }}
        />
        </>
      ) : run ? (
        <ImportProgress
          projectName={projectName}
          run={run}
          source={source}
          canManage={can("import.manage")}
          onRebuild={() => void rebuild()}
          onReanalyze={reanalyze}
        />
      ) : null}
    </div>
  );
}
