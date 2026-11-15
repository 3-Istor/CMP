import type { ImportFailure } from "@/types";

/** A button of the failure panel, wired to an existing import endpoint. */
export type FailureButton = "rebuild" | "analyze";
export type FailureHint = "fix_in_repo" | "push_rebuilds" | "contact_platform";

export type FailurePlan = {
  buttons: FailureButton[];
  hints: FailureHint[];
};

/**
 * What the failure panel offers: a retry restarts the same kind of run, a
 * re-analysis starts an analysis of the branch. A fix in the repository is a
 * hint, followed by a rebuild unless the push rebuilds on its own.
 */
export function failurePlan(failure: ImportFailure, kind: "analyze" | "build"): FailurePlan {
  const buttons: FailureButton[] = [];
  const hints: FailureHint[] = [];
  const add = (button: FailureButton) => {
    if (!buttons.includes(button)) buttons.push(button);
  };
  for (const action of failure.actions) {
    switch (action) {
      case "retry":
        add(kind === "build" ? "rebuild" : "analyze");
        break;
      case "reanalyze":
        add("analyze");
        break;
      case "fix_in_repo":
        hints.push("fix_in_repo");
        if (kind === "build" && failure.rebuilds_on_push) hints.push("push_rebuilds");
        break;
      case "contact_platform":
        hints.push("contact_platform");
        break;
    }
  }
  return { buttons, hints };
}

/** Blocking vulnerabilities of one app, for its row of the progress table. */
export function blockingCount(failure: ImportFailure | null, app: string): number {
  return failure ? failure.vulnerabilities.filter((v) => v.app === app).length : 0;
}
