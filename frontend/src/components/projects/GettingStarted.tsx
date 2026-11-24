"use client";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { getBudget, getProjectMembers, getReleaseHistory } from "@/lib/api";
import type { Capability } from "@/lib/permissions";
import {
  ChevronRight,
  Circle,
  CircleCheck,
  CircleHelp,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

type StepId = "repo" | "deployed" | "release" | "member" | "budget";
type StepStatus = "done" | "pending" | "unknown" | "loading";

const STEP_IDS: readonly StepId[] = [
  "repo",
  "deployed",
  "release",
  "member",
  "budget",
];

const REQUIRED_CAPABILITY: Record<StepId, Capability> = {
  repo: "app.deploy",
  deployed: "app.deploy",
  release: "release.operate",
  member: "members.manage",
  budget: "finops.editBudget",
};

type RemoteFacts = Partial<Record<Exclude<StepId, "repo" | "deployed">, boolean | null>>;

function readDismissed(storageKey: string): boolean {
  try {
    return window.localStorage.getItem(storageKey) === "1";
  } catch {
    return false;
  }
}

function writeDismissed(storageKey: string) {
  try {
    window.localStorage.setItem(storageKey, "1");
  } catch {
    return;
  }
}

export function GettingStarted() {
  const t = useTranslations("gettingStarted");
  const tp = useTranslations("projectHome.gettingStarted");
  const [open, setOpen] = useState(false);
  const {
    projectName,
    apps,
    loadingApps,
    appsError,
    can,
  } = useProjectContext();

  const storageKey = `cmp.gettingStarted.${projectName}.dismissed`;
  const [dismissed, setDismissed] = useState<boolean | null>(null);
  const [facts, setFacts] = useState<RemoteFacts>({});

  const appIdsKey = apps.map((app) => app.id).join(",");
  const canOperateReleases = can("release.operate");
  const canManageMembers = can("members.manage");
  const canEditBudget = can("finops.editBudget");

  useEffect(() => {
    setDismissed(readDismissed(storageKey));
  }, [storageKey]);

  useEffect(() => {
    if (loadingApps || appsError) return;
    let active = true;
    const record = (
      id: keyof RemoteFacts,
      value: boolean | null,
    ) => {
      if (active) setFacts((previous) => ({ ...previous, [id]: value }));
    };

    if (canOperateReleases) {
      const appIds = appIdsKey ? appIdsKey.split(",").map(Number) : [];
      if (appIds.length === 0) {
        record("release", false);
      } else {
        Promise.allSettled(appIds.map((id) => getReleaseHistory(id))).then(
          (results) => {
            const hasRelease = results.some(
              (r) => r.status === "fulfilled" && r.value.length > 0,
            );
            const hasFailure = results.some((r) => r.status === "rejected");
            if (hasRelease) record("release", true);
            else record("release", hasFailure ? null : false);
          },
        );
      }
    }

    if (canManageMembers) {
      getProjectMembers(projectName)
        .then(({ members }) =>
          record(
            "member",
            members.some((member) => member.role !== "owner"),
          ),
        )
        .catch(() => record("member", null));
    }

    if (canEditBudget) {
      getBudget(projectName)
        .then((budget) => record("budget", budget !== null))
        .catch(() => record("budget", null));
    }

    return () => {
      active = false;
    };
  }, [
    projectName,
    loadingApps,
    appsError,
    appIdsKey,
    canOperateReleases,
    canManageMembers,
    canEditBudget,
  ]);

  if (loadingApps || appsError || dismissed !== false) return null;

  const encodedProject = encodeURIComponent(projectName);
  const projectHref = `/projects/${encodedProject}`;
  const firstAppHref = apps[0]
    ? `${projectHref}/apps/${apps[0].id}`
    : projectHref;
  const hrefs: Record<StepId, string> = {
    repo: firstAppHref,
    deployed: firstAppHref,
    release: apps[0] ? `${firstAppHref}/delivery` : projectHref,
    member: `${projectHref}/members`,
    budget: `/finops/budgets?project=${encodedProject}`,
  };

  const statusOf = (id: StepId): StepStatus => {
    if (id === "repo") {
      return apps.some((app) => app.github_repo_url) ? "done" : "pending";
    }
    if (id === "deployed") {
      return apps.some((app) => app.status === "running") ? "done" : "pending";
    }
    const value = facts[id];
    if (value === undefined) return "loading";
    if (value === null) return "unknown";
    return value ? "done" : "pending";
  };

  const steps = STEP_IDS.filter((id) => can(REQUIRED_CAPABILITY[id])).map(
    (id) => ({ id, status: statusOf(id) }),
  );
  if (steps.length === 0) return null;

  const doneCount = steps.filter((step) => step.status === "done").length;
  if (doneCount === steps.length) return null;

  const hide = () => {
    setDismissed(true);
    writeDismissed(storageKey);
  };

  const next = steps.find((step) => step.status !== "done");

  return (
    <section
      aria-labelledby="getting-started-title"
      className="flex flex-col gap-3 rounded-xl border bg-card p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="getting-started-title" className="text-sm font-semibold">
          {t("title")}
        </h2>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">{t("progress", { done: doneCount, total: steps.length })}</span>
          <span aria-hidden>·</span>
          <button type="button" onClick={hide} title={t("dismiss")} className="text-primary hover:underline">
            {tp("hide")}
          </button>
        </span>
      </div>
      <Progress value={(doneCount / steps.length) * 100} aria-label={t("progress", { done: doneCount, total: steps.length })} />
      {next && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">{tp("next")}</span>
          <Link href={hrefs[next.id]} className="inline-flex items-center gap-1 font-medium hover:underline">
            {t(`steps.${next.id}`)}
            <ChevronRight className="h-4 w-4" aria-hidden />
          </Link>
        </p>
      )}
      <Button
        variant="ghost"
        size="xs"
        className="self-start text-muted-foreground"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? tp("hideSteps") : tp("showSteps")}
      </Button>
      {open && (
        <ol className="space-y-2">
          {steps.map((step) => (
            <li key={step.id} className="flex items-center gap-3 text-sm">
              <StepIcon status={step.status} label={t(`state.${step.status}`)} />
              <Link
                href={hrefs[step.id]}
                className={
                  step.status === "done"
                    ? "flex-1 text-muted-foreground line-through hover:underline"
                    : "flex-1 font-medium hover:underline"
                }
              >
                {t(`steps.${step.id}`)}
              </Link>
              {step.status === "unknown" && (
                <span className="text-xs text-muted-foreground">{t("unknown")}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function StepIcon({ status, label }: { status: StepStatus; label: string }) {
  const className = "h-4 w-4 shrink-0";
  const icon = {
    done: <CircleCheck className={`${className} text-primary`} aria-hidden />,
    pending: <Circle className={`${className} text-muted-foreground`} aria-hidden />,
    unknown: <CircleHelp className={`${className} text-muted-foreground`} aria-hidden />,
    loading: <Circle className={`${className} animate-pulse text-muted-foreground`} aria-hidden />,
  }[status];
  return (
    <span role="img" aria-label={label} className="inline-flex">
      {icon}
    </span>
  );
}
