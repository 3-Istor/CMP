"use client";

import { cn } from "@/lib/utils";
import type { Project } from "@/types";
import { useTranslations } from "next-intl";
import { CLOUDS, CLOUD_META } from "./format";

/** Styled native <select>, reliable and matching the input styling. */
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
  cloud: string; // "" = tous | onprem | aws | gcp
}

export function FinopsFilters({
  state,
  onChange,
  projects,
  showProject = true,
  showGranularity = true,
  showCloud = false,
}: {
  state: FinopsFilterState;
  onChange: (next: FinopsFilterState) => void;
  projects: Project[];
  showProject?: boolean;
  showGranularity?: boolean;
  showCloud?: boolean;
}) {
  const t = useTranslations("finops");
  const set = (patch: Partial<FinopsFilterState>) =>
    onChange({ ...state, ...patch });

  return (
    <div className="flex flex-wrap items-center gap-2">
      {showProject && (
        <NativeSelect
          aria-label={t("common.project")}
          value={state.project}
          onChange={(v) => set({ project: v })}
        >
          <option value="">{t("common.allProjects")}</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </NativeSelect>
      )}

      <NativeSelect
        aria-label={t("common.period")}
        value={state.period}
        onChange={(v) => set({ period: v })}
      >
        <option value="7d">{t("common.periods.7d")}</option>
        <option value="30d">{t("common.periods.30d")}</option>
        <option value="90d">{t("common.periods.90d")}</option>
      </NativeSelect>

      {showCloud && (
        <NativeSelect
          aria-label={t("filters.cloud")}
          value={state.cloud}
          onChange={(v) => set({ cloud: v })}
        >
          <option value="">{t("common.allClouds")}</option>
          {CLOUDS.map((cloud) => (
            <option key={cloud} value={cloud}>
              {CLOUD_META[cloud].label}
            </option>
          ))}
        </NativeSelect>
      )}

      {showGranularity && (
        <NativeSelect
          aria-label={t("filters.granularity")}
          value={state.granularity}
          onChange={(v) => set({ granularity: v })}
        >
          <option value="daily">{t("filters.daily")}</option>
          <option value="weekly">{t("filters.weekly")}</option>
          <option value="monthly">{t("filters.monthly")}</option>
        </NativeSelect>
      )}
    </div>
  );
}
