"use client";

import { cn } from "@/lib/utils";
import type { Project } from "@/types";

/** Styled native <select> — reliable and matches the input styling. */
function NativeSelect({
  value,
  onChange,
  children,
  className,
  "aria-label": ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm text-foreground outline-none",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "dark:bg-input/30",
        "[&>option]:bg-popover [&>option]:text-popover-foreground",
        className,
      )}
    >
      {children}
    </select>
  );
}

export interface FinopsFilterState {
  project: string; // "" = tous
  period: string; // 7d | 30d | 90d
  granularity: string; // daily | weekly | monthly
}

export function FinopsFilters({
  state,
  onChange,
  projects,
  showGranularity = true,
}: {
  state: FinopsFilterState;
  onChange: (next: FinopsFilterState) => void;
  projects: Project[];
  showGranularity?: boolean;
}) {
  const set = (patch: Partial<FinopsFilterState>) =>
    onChange({ ...state, ...patch });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <NativeSelect
        aria-label="Projet"
        value={state.project}
        onChange={(v) => set({ project: v })}
      >
        <option value="">Tous les projets</option>
        {projects.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </NativeSelect>

      <NativeSelect
        aria-label="Période"
        value={state.period}
        onChange={(v) => set({ period: v })}
      >
        <option value="7d">7 jours</option>
        <option value="30d">30 jours</option>
        <option value="90d">90 jours</option>
      </NativeSelect>

      {showGranularity && (
        <NativeSelect
          aria-label="Granularité"
          value={state.granularity}
          onChange={(v) => set({ granularity: v })}
        >
          <option value="daily">Quotidien</option>
          <option value="weekly">Hebdomadaire</option>
          <option value="monthly">Mensuel</option>
        </NativeSelect>
      )}
    </div>
  );
}
