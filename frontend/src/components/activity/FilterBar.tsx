"use client";

/**
 * Filters on an even grid, then toggles on the left and actions on the right,
 * so nothing wraps onto a stray second line however many filters there are.
 */
export function FilterBar({
  selects,
  toggles,
  actions,
}: {
  selects: React.ReactNode;
  toggles?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-xl border bg-muted/30 p-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        {selects}
      </div>
      {(toggles || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            {toggles}
          </div>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </div>
      )}
    </div>
  );
}
