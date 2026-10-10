"use client";

import { cn } from "@/lib/utils";

/** Styled native <select>, as in the FinOps filters. */
export function NativeSelect({
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
        "h-8 max-w-56 rounded-lg border border-input bg-background px-2.5 text-sm outline-none",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "dark:bg-input/30",
        className,
      )}
    >
      {children}
    </select>
  );
}
