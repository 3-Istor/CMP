"use client";

import { useProjects } from "@/lib/hooks";
import { isProjectInFlight } from "@/types";
import { useEffect } from "react";
import { pollWhileVisible } from "./visiblePolling";

/**
 * The backend records a project as soon as it accepts the request and updates
 * its status at each bootstrap or teardown step, so polling the list is enough
 * to show progress.
 */
export function useProjectsLive() {
  const result = useProjects();
  const { projects, refresh } = result;
  const inFlight = projects.some(isProjectInFlight);
  useEffect(() => {
    if (!inFlight) return;
    return pollWhileVisible(refresh, 3000);
  }, [inFlight, refresh]);
  return result;
}
