"use client";

import { STEP_UP_FAILED_EVENT, takeStepUpReturn } from "@/lib/stepUp";
import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { toast } from "sonner";

export function StepUpNotice() {
  const t = useTranslations("stepUp");

  useEffect(() => {
    if (takeStepUpReturn()) toast.success(t("confirmed"));
    const onFailed = () => toast.error(t("failed"));
    window.addEventListener(STEP_UP_FAILED_EVENT, onFailed);
    return () => window.removeEventListener(STEP_UP_FAILED_EVENT, onFailed);
  }, [t]);

  return null;
}
