"use client";

import { MAX_FAVORITES, useFavorites } from "@/lib/favorites";
import { cn } from "@/lib/utils";
import { Star } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

type Props =
  | { kind: "project"; id: string; className?: string }
  | { kind: "app"; id: number; className?: string };

export function FavoriteToggle(props: Props) {
  const t = useTranslations("favorites");
  const { isFavoriteProject, isFavoriteApp, toggleProject, toggleApp } =
    useFavorites();
  const pressed =
    props.kind === "project"
      ? isFavoriteProject(props.id)
      : isFavoriteApp(props.id);

  const onClick = async (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const result =
      props.kind === "project"
        ? await toggleProject(props.id)
        : await toggleApp(props.id);
    if (result === "limit") {
      toast.error(t("limitReached", { max: MAX_FAVORITES }));
    }
    if (result === "failed") toast.error(t("saveFailed"));
  };

  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={pressed ? t("remove") : t("add")}
      title={pressed ? t("remove") : t("add")}
      onClick={onClick}
      className={cn(
        "inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        pressed && "text-amber-700 dark:text-amber-500 hover:text-amber-500",
        props.className,
      )}
    >
      <Star className={cn("size-4", pressed && "fill-current")} aria-hidden />
    </button>
  );
}
