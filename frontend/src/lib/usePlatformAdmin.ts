"use client";

import { getSecurityRole } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import { useEffect, useState } from "react";

const answers = new Map<string, Promise<boolean>>();

function isPlatformAdminVia(project: string): Promise<boolean> {
  let answer = answers.get(project);
  if (!answer) {
    answer = getSecurityRole(project)
      .then((role) => role.cnp_admin)
      .catch(() => false);
    answers.set(project, answer);
  }
  return answer;
}

// The platform admin flag only comes with a project-scoped endpoint, so a user
// without any project is treated as a non-admin.
export function usePlatformAdmin(): boolean {
  const { projects } = useProjects();
  const firstProject = projects[0]?.name;
  const [answer, setAnswer] = useState<{ via: string; value: boolean } | null>(
    null,
  );

  useEffect(() => {
    if (!firstProject) return;
    let cancelled = false;
    isPlatformAdminVia(firstProject).then((value) => {
      if (!cancelled) setAnswer({ via: firstProject, value });
    });
    return () => {
      cancelled = true;
    };
  }, [firstProject]);

  return answer !== null && answer.via === firstProject && answer.value;
}
