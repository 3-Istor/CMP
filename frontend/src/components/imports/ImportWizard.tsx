"use client";

import { Bot, Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { Skeleton } from "@/components/ui/skeleton";
import {
  applyImport,
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
  ImportRunDetail,
  ImportSourceView,
  Topology,
} from "@/types";
import { AnalysisStep } from "./AnalysisStep";
import { ImportProgress } from "./ImportProgress";
import { PlanReview } from "./PlanReview";
import { RepoStep } from "./RepoStep";

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

function isPlan(plan: ImportRunDetail["plan"]): plan is ImportPlan {
  return Boolean(plan && "services" in plan);
}

export function ImportWizard() {
  const params = useSearchParams();
  const runId = params.get("run");
  // A new run is a new wizard: nothing of the previous one carries over.
  return <WizardForRun key={runId ?? "new"} runId={runId} draftId={params.get("draft")} />;
}

function WizardForRun({ runId, draftId }: { runId: string | null; draftId: string | null }) {
  const t = useTranslations("imports");
  const router = useRouter();
  const pathname = usePathname();
  const { projectName } = useProjectContext();

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
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  const step = stepOf(run);
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

  const start = async (repo: string, branch: string | null) => {
    try {
      const next = await startImport(projectName, repo, branch);
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
    if (source) void start(source.repo_full_name, source.branch);
    else go(null);
  };

  const current = STEPS.indexOf(step);
  const stepper = useMemo(
    () => (
      <ol aria-label={t("stepsLabel")} className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {STEPS.map((s, index) => (
          <li
            key={s}
            aria-current={index === current ? "step" : undefined}
            className={cn(
              "flex items-center gap-2",
              index === current ? "font-medium text-foreground" : "text-muted-foreground",
            )}
          >
            <span
              className={cn(
                "grid h-6 w-6 place-items-center rounded-full border text-xs",
                index < current && "border-primary bg-primary text-primary-foreground",
                index === current && "border-primary text-primary",
              )}
              aria-hidden
            >
              {index < current ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            {t(`step.${s}`)}
          </li>
        ))}
      </ol>
    ),
    [current, t],
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
      ) : step === "repo" ? (
        <RepoStep projectName={projectName} onStart={start} />
      ) : step === "analysis" && run ? (
        <AnalysisStep run={run} repo={source?.repo_full_name ?? null} onRetry={reanalyze} onRestart={() => go(null)} />
      ) : step === "review" && run && plan ? (
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
          onRebuild={() => void rebuild()}
          onReanalyze={reanalyze}
        />
      ) : null}
    </div>
  );
}
