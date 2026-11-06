"use client";

import { useProjectContext } from "@/components/projects/ProjectContext";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const SELECT =
  "h-8 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

/** The `?app=<id>` filter of a project section, kept in the URL so old app tab links land filtered. */
export function useAppFilter() {
  const { apps } = useProjectContext();
  const raw = useSearchParams().get("app");
  const app = raw
    ? apps.find((a) => String(a.id) === raw || a.name === raw)
    : undefined;
  return app;
}

export function AppFilter() {
  const t = useTranslations("projectNav");
  const { apps } = useProjectContext();
  const app = useAppFilter();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const select = (id: string) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("app", id);
    else next.delete("app");
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname);
  };

  return (
    <select
      aria-label={t("appFilter")}
      className={SELECT}
      value={app ? String(app.id) : ""}
      onChange={(e) => select(e.target.value)}
    >
      <option value="">{t("allApps")}</option>
      {apps.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  );
}
