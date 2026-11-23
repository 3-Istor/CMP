"use client";

import { cn } from "@/lib/utils";
import { Loader2, Lock, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

/** Project line inside a cluster card: name (link when href), favorite / protected / moving marks, app chips, action. */
export function ProjectRow({
  name,
  href,
  favorite,
  protectedProject,
  moving,
  apps,
  total,
  action,
}: {
  name: string;
  href?: string;
  favorite?: boolean;
  protectedProject?: boolean;
  moving?: boolean;
  apps?: React.ReactNode;
  total?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const t = useTranslations("cluster");
  return (
    <div className={cn("grid gap-2 rounded-lg border bg-background p-2.5", moving && "border-dashed")}>
      <div className="flex flex-wrap items-center gap-2">
        {href ? (
          <Link href={href} className="font-semibold hover:underline focus-visible:outline-2 focus-visible:outline-ring">
            {name}
          </Link>
        ) : (
          <span className="font-semibold">{name}</span>
        )}
        {favorite && <Star role="img" aria-label={t("favorite")} className="size-3.5 fill-amber-500 text-amber-500" />}
        {protectedProject && <Lock role="img" aria-label={t("protected")} className="size-3.5 text-muted-foreground" />}
        {moving && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Loader2 aria-hidden className="size-3 motion-safe:animate-spin" />
            {t("moving")}
          </span>
        )}
        <span className="ml-auto">{action}</span>
      </div>
      {total && <div className="-mt-1">{total}</div>}
      {apps && <div className="flex flex-wrap gap-1.5">{apps}</div>}
    </div>
  );
}
