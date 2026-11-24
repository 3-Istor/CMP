import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import Link from "next/link";

export type EmptyStateAction =
  | { label: string; href: string; onClick?: never }
  | { label: string; onClick: () => void; href?: never };

export interface EmptyStateProps {
  title: string;
  /** Why there is nothing to show, so the reader knows whether to wait or act. */
  reason?: string;
  action?: EmptyStateAction;
  /** One line, for cards and table bodies. */
  compact?: boolean;
  className?: string;
}

export function EmptyState({
  title,
  reason,
  action,
  compact = false,
  className,
}: EmptyStateProps) {
  const button =
    action &&
    (action.href !== undefined && /^https?:\/\//.test(action.href) ? (
      <a
        href={action.href}
        target="_blank"
        rel="noreferrer"
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        {action.label}
      </a>
    ) : action.href !== undefined ? (
      <Link
        href={action.href}
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        {action.label}
      </Link>
    ) : (
      <Button variant="outline" size="sm" onClick={action.onClick}>
        {action.label}
      </Button>
    ));

  if (compact) {
    return (
      <div
        className={cn(
          "flex flex-wrap items-center gap-x-2 gap-y-1 py-3 text-sm text-muted-foreground",
          className,
        )}
      >
        <span className="font-medium text-foreground">{title}</span>
        {reason && <span>{reason}</span>}
        {button}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 px-4 py-8 text-center text-sm",
        className,
      )}
    >
      <p className="font-medium">{title}</p>
      {reason && <p className="max-w-prose text-muted-foreground">{reason}</p>}
      {button}
    </div>
  );
}
