"use client";

import type { SecurityFinding } from "@/types";
import { useTranslations } from "next-intl";

type Field = "title" | "detail" | "fix" | "location";

/**
 * A finding's text in the interface language. The backend stores French text
 * plus a key per field; a key this build does not know falls back to the text.
 */
export function useFindingText() {
  const t = useTranslations("securityFindings");
  return (finding: SecurityFinding, field: Field): string => {
    const entry = finding.i18n?.[field];
    if (entry && t.has(entry.key)) return t(entry.key, entry.params);
    return finding[field] ?? "";
  };
}
