"use client";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getBudget, getProjectMembers, getReleaseHistory } from "@/lib/api";
import type { Capability } from "@/lib/permissions";
import {
  ChevronRight,
  Circle,
  CircleCheck,
  CircleHelp,
  Loader2,
  X,
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
    release: apps[0] ? `${firstAppHref}/release` : projectHref,
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

  return (
    <section
      aria-labelledby="getting-started-title"
      className="rounded-xl border bg-card p-6 space-y-4"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 id="getting-started-title" className="font-semibold">
            {t("title")}
          </h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {t("progress", { done: doneCount, total: steps.length })}
          </Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={hide}
            title={t("dismiss")}
            aria-label={t("dismiss")}
          >
            <X aria-hidden />
          </Button>
        </div>
      </div>
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
              <span className="text-xs text-muted-foreground">
                {t("unknown")}
              </span>
            )}
            <ChevronRight
              className="h-4 w-4 shrink-0 text-muted-foreground"
              aria-hidden
            />
          </li>
        ))}
      </ol>
    </section>
  );
}

function StepIcon({ status, label }: { status: StepStatus; label: string }) {
  const className = "h-4 w-4 shrink-0";
  const icon = {
    done: <CircleCheck className={`${className} text-primary`} aria-hidden />,
    pending: <Circle className={`${className} text-muted-foreground`} aria-hidden />,
    unknown: <CircleHelp className={`${className} text-muted-foreground`} aria-hidden />,
    loading: <Loader2 className={`${className} animate-spin text-muted-foreground`} aria-hidden />,
  }[status];
  return (
    <span role="img" aria-label={label} className="inline-flex">
      {icon}
    </span>
  );
}
