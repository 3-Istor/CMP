"use client";

import { getScorecard } from "@/lib/api";
import type { Deployment, Scorecard } from "@/types";
import { useEffect, useMemo, useState } from "react";

const TTL_MS = 5 * 60 * 1000;

interface Entry {
  at: number;
  promise: Promise<Scorecard>;
}

const entries = new Map<number, Entry>();

export function loadScorecard(appId: number, force = false): Promise<Scorecard> {
  const known = entries.get(appId);
  if (!force && known && Date.now() - known.at < TTL_MS) return known.promise;
  const promise = getScorecard(appId);
  const entry = { at: Date.now(), promise };
  entries.set(appId, entry);
  promise.catch(() => {
    if (entries.get(appId) === entry) entries.delete(appId);
  });
  return promise;
}

export function useScorecards(
  appIds: number[],
  enabled = true,
): Record<number, Scorecard> {
  const [cards, setCards] = useState<Record<number, Scorecard>>({});
  const key = appIds.join(",");
  const ids = useMemo(() => (key ? key.split(",").map(Number) : []), [key]);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    for (const id of ids) {
      loadScorecard(id)
        .then((card) => live && setCards((prev) => ({ ...prev, [id]: card })))
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [ids, enabled]);

  return cards;
}

export function hasScorecard(app: Deployment): boolean {
  return (
    app.provider_type === "kubernetes" &&
    (app.status === "running" || app.status === "degraded")
  );
}

export function actionHref(
  action: string,
  projectName: string,
  appId: number,
  repoUrl: string | null,
): string | null {
  const project = encodeURIComponent(projectName);
  const app = `/projects/${project}/apps/${appId}`;
  switch (action) {
    case "run_scan":
    case "fix_critical":
    case "fix_high":
      return `${app}/security`;
    case "set_budget":
      return `/finops/budgets?project=${project}`;
    case "reduce_cost":
      return `${app}/costs`;
    case "apply_recommendations":
      return "/finops/recommendations";
    case "fix_health":
    case "fix_restarts":
      return `${app}/logs`;
    case "add_readme":
    case "expand_readme":
      return repoUrl;
    default:
      return null;
  }
}
