"use client";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import Link from "next/link";

export interface EmptyStateAction {
  label: string;
  href?: string;
  onClick?: () => void;
}

export interface EmptyStateProps {
  title: string;
  reason: string;
  action?: EmptyStateAction;
  compact?: boolean;
  children?: React.ReactNode;
}

export function EmptyStateButton({
  action,
  variant = "outline",
}: {
  action: EmptyStateAction;
  variant?: "outline" | "default";
}) {
  const className = buttonVariants({ size: "sm", variant });
  if (action.href && /^https?:\/\//.test(action.href)) {
    return (
      <a href={action.href} target="_blank" rel="noreferrer" className={className}>
        {action.label}
      </a>
    );
  }
  if (action.href) {
    return (
      <Link href={action.href} className={className}>
        {action.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={action.onClick} className={className}>
      {action.label}
    </button>
  );
}

export function EmptyState({ title, reason, action, compact, children }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-dashed text-center",
        compact ? "px-4 py-3" : "px-6 py-8",
      )}
    >
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-1 max-w-prose text-sm text-muted-foreground">{reason}</p>
      {(action || children) && (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          {action && <EmptyStateButton action={action} />}
          {children}
        </div>
      )}
    </div>
  );
}
