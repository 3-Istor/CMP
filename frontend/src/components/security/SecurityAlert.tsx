"use client";

import { getSecuritySummary } from "@/lib/api";
import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";

/** Red strip on a project or app page while a mandatory action is open. */
export function SecurityAlert({
  project,
  app = null,
  onOpen,
}: {
  project: string;
  app?: string | null;
  onOpen: () => void;
}) {
  const [core, setCore] = useState(0);

  useEffect(() => {
    getSecuritySummary(project, app)
      .then((s) => setCore(s.core))
      .catch(() => setCore(0));
  }, [project, app]);

  if (core === 0) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2.5 text-left text-sm"
    >
      <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
      <span className="flex-1 font-medium">
        {core === 1
          ? "Attention : 1 action de sécurité obligatoire"
          : `Attention : ${core} actions de sécurité obligatoires`}
      </span>
      <span className="text-red-700 dark:text-red-300">Voir →</span>
    </button>
  );
}
