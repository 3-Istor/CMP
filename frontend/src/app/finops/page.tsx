"use client";

import { FinopsOverviewDashboard } from "@/components/finops/FinopsOverviewDashboard";
import { Skeleton } from "@/components/ui/skeleton";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function OverviewInner() {
  const searchParams = useSearchParams();
  return (
    <FinopsOverviewDashboard
      initialProject={searchParams.get("project") ?? ""}
    />
  );
}

export default function FinopsOverviewPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <OverviewInner />
    </Suspense>
  );
}
