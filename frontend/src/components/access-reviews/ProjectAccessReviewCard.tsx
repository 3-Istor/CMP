"use client";

import { UserCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getMyAccessReview } from "@/lib/api";
import { useFormat } from "@/lib/useFormat";
import type { MyAccessReview } from "@/types/accessReview";

import { ReviewTable } from "./ReviewItems";

/**
 * Shown on the project page to whoever reviews this project's access in the
 * open campaign (the owner, or its admins when the owner is gone).
 */
export function ProjectAccessReviewCard({ project }: { project: string }) {
  const t = useTranslations("accessReviews.card");
  const format = useFormat();
  const [review, setReview] = useState<MyAccessReview | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    getMyAccessReview(project)
      .then(setReview)
      .catch(() => setReview(null));
  }, [project]);

  useEffect(load, [load]);

  if (!review || review.items.length === 0) return null;
  const pending = review.items.filter((i) => !i.decision).length;
  if (pending === 0 && !open) return null;

  return (
    <Card className="border-amber-500/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserCheck className="h-4 w-4" aria-hidden />
          {t("title")}
        </CardTitle>
        <CardDescription>
          {t("description", {
            count: pending,
            due: review.due_at ? format.date(review.due_at) : "",
          })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button
          size="sm"
          variant={open ? "outline" : "default"}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? t("hide") : t("open")}
        </Button>
        {open && <ReviewTable items={review.items} me={review.me} onDone={load} />}
      </CardContent>
    </Card>
  );
}
