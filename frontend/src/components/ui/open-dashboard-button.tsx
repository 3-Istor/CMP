import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Maximize2 } from "lucide-react";
import Link from "next/link";

export function OpenDashboardButton({
  href,
  label,
  className,
}: {
  href: string;
  label: string;
  className?: string;
}) {
  return (
    <Link
      href={href}
      title={label}
      aria-label={label}
      className={cn(
        buttonVariants({ variant: "ghost", size: "icon-sm" }),
        "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      <Maximize2 />
    </Link>
  );
}
