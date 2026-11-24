"use client";

import { EmptyState } from "@/components/data/EmptyState";
import { useAppContext } from "@/components/projects/AppContext";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { getImportTasks } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { ImportTask, ImportTasks } from "@/types";
import { CheckCircle2, Circle } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

/** The finish-the-import state of the current app, null until known or for template apps. */
export function useImportTasks(): ImportTasks | null {
  const { appId, deployment } = useAppContext();
  const imported = deployment.template_id.startsWith("import-");
  const { data } = useCardData(`import-tasks-${appId}`, () => getImportTasks(appId), {
    enabled: imported,
  });
  return imported ? data : null;
}

/** True once the app is known to be an import whose repository the platform does not manage. */
export function isUnmanagedImport(tasks: ImportTasks | null): tasks is ImportTasks {
  return Boolean(tasks?.imported && !tasks.managed);
}

function finishHref(tasks: ImportTasks): string | null {
  return tasks.setup_pr?.state === "open" && tasks.setup_pr.url
    ? tasks.setup_pr.url
    : tasks.import_href;
}

/** Shown in place of a section that writes the app's repository. */
export function RepoNotManagedNotice({
  tasks,
  compact,
}: {
  tasks: ImportTasks;
  compact?: boolean;
}) {
  const t = useTranslations("imports.finish");
  const href = finishHref(tasks);
  return (
    <EmptyState
      compact={compact}
      title={t("notManagedTitle")}
      reason={t("notManagedReason")}
      action={href ? { label: t("finishAction"), href } : undefined}
    />
  );
}

function TaskRow({ task }: { task: ImportTask }) {
  const t = useTranslations("imports.finish");
  const label = t.has(`tasks.${task.key}`) ? t(`tasks.${task.key}`, task.params) : task.label;
  return (
    <li className={task.done ? "flex flex-wrap items-center gap-2" : "flex flex-wrap items-center gap-2 text-muted-foreground"}>
      {task.done ? (
        <CheckCircle2 className="h-4 w-4 text-primary" aria-label={t("done")} />
      ) : (
        <Circle className="h-4 w-4" aria-label={t("todo")} />
      )}
      <span>{label}</span>
      {!task.done && task.unlocks.length > 0 && (
        <Badge variant="outline">
          {t("unlocks", {
            what: task.unlocks.map((u) => t(`unlock.${u.replace(".", "_")}`)).join(", "),
          })}
        </Badge>
      )}
    </li>
  );
}

/** "Finish the import": what is left before the imported app matches a template app. */
export function FinishImportBanner() {
  const t = useTranslations("imports.finish");
  const tasks = useImportTasks();
  if (!tasks?.imported || tasks.total === 0 || tasks.done >= tasks.total) return null;
  const prUrl = tasks.setup_pr?.state === "open" ? tasks.setup_pr.url : null;
  const percent = Math.round((tasks.done / tasks.total) * 100);
  return (
    <section
      aria-label={t("title")}
      className="space-y-3 rounded-xl bg-accent p-4 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">
          {t("heading", { done: tasks.done, total: tasks.total })}
        </h2>
        <div className="flex flex-wrap gap-2">
          {tasks.agent_href && (
            <Link href={tasks.agent_href} className={buttonVariants({ variant: "outline", size: "sm" })}>
              {t("assignAgent")}
            </Link>
          )}
          {prUrl && (
            <a href={prUrl} target="_blank" rel="noreferrer" className={buttonVariants({ size: "sm" })}>
              {t("openSetupPr")}
            </a>
          )}
        </div>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={tasks.total}
        aria-valuenow={tasks.done}
        className="h-1.5 w-full overflow-hidden rounded-full bg-card"
      >
        <div className="h-full bg-primary" style={{ width: `${percent}%` }} />
      </div>
      <ul className="space-y-1">
        {tasks.tasks.map((task) => (
          <TaskRow key={task.key} task={task} />
        ))}
      </ul>
    </section>
  );
}
