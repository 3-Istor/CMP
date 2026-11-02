"use client";

import { getSecurityRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { AuditView } from "./AuditView";
import { LogsView } from "./LogsView";

type View = "audit" | "logs";

export function ActivityTab({
  project,
  app,
}: {
  project: string;
  app?: string;
}) {
  const t = useTranslations("activity.tab");
  const [view, setView] = useState<View>("audit");
  const [role, setRole] = useState<{ admin: boolean } | null>(null);
  const scope = app ? "app" : "project";

  useEffect(() => {
    getSecurityRole(project).then(
      (r) => setRole({ admin: r.cnp_admin || r.role === "admin" }),
      () => setRole({ admin: false }),
    );
  }, [project]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          {view === "audit"
            ? t("description.audit", { scope })
            : t("description.logs", { scope })}
        </p>
        <div className="flex rounded-full border p-0.5">
          {(["audit", "logs"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={cn(
                "rounded-full px-4 py-1 text-sm transition-colors",
                view === v
                  ? "bg-primary/15 font-medium"
                  : "text-muted-foreground",
              )}
            >
              {t(`views.${v}`)}
            </button>
          ))}
        </div>
      </div>
      {view === "audit" ? (
        role && (
          <AuditView
            project={project}
            app={app}
            canExport={role.admin}
            showLogins={role.admin}
          />
        )
      ) : (
        <LogsView
          project={project}
          namespace={app ? `${project}-${app}` : undefined}
        />
      )}
    </div>
  );
}
