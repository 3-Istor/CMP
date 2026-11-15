/** The run an import refusal points at: the one in progress, or the latest analysis. */
export type LatestRunPointer = {
  run_id: string;
  kind: string;
  status: string;
  api_path: string;
  ui_path: string;
};

export type ImportRefusal = {
  message: string;
  latestRun: LatestRunPointer | null;
};

function isPointer(value: unknown): value is LatestRunPointer {
  const pointer = value as LatestRunPointer | null;
  return (
    typeof pointer?.run_id === "string" &&
    typeof pointer.ui_path === "string" &&
    pointer.ui_path.startsWith("/projects/")
  );
}

/** An import API error: its message and, on a 409 caused by another run, that run. */
export function importRefusal(err: unknown): ImportRefusal {
  const raw = err instanceof Error ? err.message : String(err);
  let detail: unknown;
  try {
    detail = JSON.parse(raw)?.detail;
  } catch {
    return { message: raw, latestRun: null };
  }
  if (typeof detail === "string") return { message: detail, latestRun: null };
  const body = detail as { message?: unknown; latest_run?: unknown } | null;
  if (typeof body?.message === "string") {
    return {
      message: body.message,
      latestRun: isPointer(body.latest_run) ? body.latest_run : null,
    };
  }
  return { message: raw, latestRun: null };
}
