"use client";

import { useProjects } from "@/lib/hooks";
import { isProjectInFlight } from "@/types";
import { useEffect } from "react";

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
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [inFlight, refresh]);
  return result;
}
