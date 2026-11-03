"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { AlertTriangle, Lock } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

export interface CardState {
  loading: boolean;
  error: string | null;
  forbidden?: boolean;
  stale?: boolean;
  updatedAt?: Date | null;
  empty?: boolean;
  hasData: boolean;
  retry: () => void;
}

interface Props {
  title: React.ReactNode;
  action?: React.ReactNode;
  state: CardState;
  emptyText: string;
  skeletonClassName: string;
  className?: string;
  children: React.ReactNode;
}

export function OverviewCard({
  title,
  action,
  state,
  emptyText,
  skeletonClassName,
  className,
  children,
}: Props) {
  const t = useTranslations("appPage.common");
  const locale = useLocale();
  const { loading, error, forbidden, stale, updatedAt, empty, hasData, retry } =
    state;

  let body: React.ReactNode;
  if (forbidden) {
    body = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Lock className="h-4 w-4" aria-hidden />
        {t("restricted")}
      </p>
    );
  } else if (loading && !hasData) {
    body = <Skeleton className={skeletonClassName} />;
  } else if (error && !hasData) {
    body = (
      <div
        role="alert"
        className={cn(
          "flex flex-col items-start justify-center gap-2 text-sm",
          skeletonClassName,
        )}
      >
        <p className="flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-4 w-4" aria-hidden />
          {t("loadFailed")}
        </p>
        <Button variant="outline" size="sm" onClick={retry}>
          {t("retry")}
        </Button>
      </div>
    );
  } else if (empty) {
    body = <p className="text-sm text-muted-foreground">{emptyText}</p>;
  } else {
    body = children;
  }

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        {action}
      </CardHeader>
      <CardContent className="space-y-2">
        {body}
        {stale && !forbidden && (
          <p
            role="status"
            className="flex flex-wrap items-center gap-2 text-xs text-amber-700 dark:text-amber-400"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            {t("stale", {
              time: updatedAt
                ? updatedAt.toLocaleTimeString(locale, {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "-",
            })}
            <Button variant="ghost" size="xs" onClick={retry}>
              {t("retry")}
            </Button>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
