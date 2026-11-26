"use client";

import { useAppEnv, type AppEnv } from "@/components/projects/AppContext";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const ENVS: AppEnv[] = ["prod", "staging"];

/** Prod | Staging: rewrites ?env and keeps the other query params (e.g. ?trace=). */
export function AppEnvSwitch() {
  const t = useTranslations("apps");
  const env = useAppEnv();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();

  const pick = (next: AppEnv) => {
    const params = new URLSearchParams(search.toString());
    if (next === "staging") params.set("env", "staging");
    else params.delete("env");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <div
      role="group"
      aria-label={t("nav.environment")}
      className="ml-auto inline-flex shrink-0 self-center rounded-md bg-muted p-0.5"
    >
      {ENVS.map((e) => (
        <button
          key={e}
          type="button"
          aria-pressed={env === e}
          onClick={() => pick(e)}
          className={cn(
            "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
            env === e
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`env.${e}`)}
        </button>
      ))}
    </div>
  );
}
