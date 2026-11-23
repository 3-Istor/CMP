import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Cloud state pill: up neutral, degraded amber, anything else red. */
export function CloudStateBadge({
  state,
  className,
  children,
}: {
  state: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Badge
      variant={state === "up" ? "secondary" : state === "degraded" ? "outline" : "destructive"}
      className={cn(
        state === "degraded" && "border-amber-600/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
        className,
      )}
    >
      {children}
    </Badge>
  );
}
