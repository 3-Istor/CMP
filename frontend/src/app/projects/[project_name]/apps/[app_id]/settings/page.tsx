"use client";

import { AppIconPanel } from "@/components/app-icon/AppIconPanel";
import { EnvironmentsTab } from "@/components/environments/EnvironmentsTab";
import { AppConfigPanel } from "@/components/projects/AppConfigPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { SecretsPresencePanel } from "@/components/projects/SecretsPresencePanel";
import { SecurityDataPanel } from "@/components/projects/SecurityDataPanel";
import { ReleaseSettingsCard } from "@/components/release/ReleaseSettingsCard";
import { AlertsCard } from "@/components/security/AlertsCard";
import { AppSettingsCard } from "@/components/security/SettingsCard";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getSecurityRole } from "@/lib/api";
import { useRelease } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

/**
 * A search box over its children: every match of the typed text is
 * highlighted (CSS Custom Highlight API, styled in globals.css) and the first
 * one scrolled into view; Enter jumps to the next. Matches field labels and
 * text, not values typed inside inputs.
 */
function SearchHighlight({ children }: { children: React.ReactNode }) {
  const t = useTranslations("apps.config");
  const [query, setQuery] = useState("");
  const [count, setCount] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const rangesRef = useRef<Range[]>([]);
  const indexRef = useRef(0);

  const scrollTo = (i: number) => {
    const range = rangesRef.current[i];
    range?.startContainer.parentElement?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
  };

  useEffect(() => {
    const root = containerRef.current;
    if (!root || typeof CSS === "undefined" || !CSS.highlights) return;
    const needle = query.trim().toLowerCase();

    const apply = () => {
      const ranges: Range[] = [];
      if (needle) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = node.textContent?.toLowerCase() ?? "";
          for (
            let i = text.indexOf(needle);
            i !== -1;
            i = text.indexOf(needle, i + needle.length)
          ) {
            const range = new Range();
            range.setStart(node, i);
            range.setEnd(node, i + needle.length);
            ranges.push(range);
          }
        }
      }
      rangesRef.current = ranges;
      setCount(ranges.length);
      CSS.highlights.set("config-search", new Highlight(...ranges));
    };

    apply();
    indexRef.current = 0;
    scrollTo(0);
    // Panels load and switch components after mount: re-match on changes.
    const observer = new MutationObserver(apply);
    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    return () => {
      observer.disconnect();
      CSS.highlights.delete("config-search");
    };
  }, [query]);

  return (
    <div className="space-y-4">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || count === 0) return;
            indexRef.current = (indexRef.current + 1) % count;
            scrollTo(indexRef.current);
          }}
          placeholder={t("searchPlaceholder")}
          aria-label={t("searchPlaceholder")}
          className="pl-8 pr-24"
        />
        {query.trim() && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
            {t("searchCount", { count })}
          </span>
        )}
      </div>
      <div ref={containerRef} className="space-y-6">
        {children}
      </div>
    </div>
  );
}

const SECTIONS = ["general", "secrets", "delivery", "security", "advanced"] as const;

function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id: (typeof SECTIONS)[number];
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-40 space-y-4">
      <div className="border-b pb-2">
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  );
}

export default function AppSettingsPage() {
  const t = useTranslations("apps.config");
  const ts = useTranslations("appPage.settings");
  const { appId, projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  const isRunning = deployment.status === "running";
  const [securityAdmin, setSecurityAdmin] = useState(false);
  const [active, setActive] = useState<string>("general");
  const { view: release } = useRelease(appId);
  const releaseInProgress = Boolean(
    release?.enabled &&
      release.canaryHash &&
      release.canaryHash !== release.stableHash &&
      (release.phase === "Progressing" || release.phase === "Paused"),
  );

  useEffect(() => {
    getSecurityRole(projectName).then(
      (r) => setSecurityAdmin(r.cnp_admin || r.role === "admin"),
      () => setSecurityAdmin(false),
    );
  }, [projectName]);


  const allowed =
    deployment.provider_type === "kubernetes" && can("app.viewConfig");
  const visibleSections = SECTIONS.filter(
    (id) =>
      (id !== "secrets" || isRunning) &&
      (id !== "delivery" || can("release.view")) &&
      (id !== "security" || can("app.viewObservability")),
  );
  const sectionsKey = visibleSections.join();

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((e) => e.isIntersecting);
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: "-160px 0px -60% 0px" },
    );
    sectionsKey.split(",").forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [allowed, sectionsKey]);


  return (
    <AppSection allowed={allowed}>
      <div className="grid gap-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav
          aria-label={ts("toc")}
          className="hidden lg:block lg:sticky lg:top-40 lg:self-start"
        >
          <ul className="space-y-0.5 text-sm">
            {visibleSections.map((id) => (
              <li key={id}>
                <a
                  href={`#${id}`}
                  aria-current={active === id ? "location" : undefined}
                  className={cn(
                    "block rounded-md px-3 py-1.5 transition-colors",
                    active === id
                      ? "bg-muted font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {ts(`sections.${id}.title`)}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0 space-y-12">
          <SettingsSection
            id="general"
            title={ts("sections.general.title")}
            description={ts("sections.general.description")}
          >
            <AppIconPanel />
            {isRunning && (
              <div id="security-data" className="scroll-mt-40">
                <SecurityDataPanel deploymentId={appId} />
              </div>
            )}
          </SettingsSection>

          {isRunning && (
            <SettingsSection
              id="secrets"
              title={ts("sections.secrets.title")}
              description={ts("sections.secrets.description")}
            >
              <SecretsPresencePanel deploymentId={appId} />
            </SettingsSection>
          )}

          {can("release.view") && (
            <SettingsSection
              id="delivery"
              title={ts("sections.delivery.title")}
              description={ts("sections.delivery.description")}
            >
              <ReleaseSettingsCard
                deploymentId={appId}
                inProgress={releaseInProgress}
              />
              <EnvironmentsTab deploymentId={appId} mode="settings" />
            </SettingsSection>
          )}

          {can("app.viewObservability") && (
            <SettingsSection
              id="security"
              title={ts("sections.security.title")}
              description={ts("sections.security.description")}
            >
              <AppSettingsCard
                project={projectName}
                app={deployment.name}
                canAdmin={securityAdmin}
              />
              <AlertsCard
                project={projectName}
                app={deployment.name}
                canAdmin={securityAdmin}
              />
            </SettingsSection>
          )}

          <SettingsSection
            id="advanced"
            title={ts("sections.advanced.title")}
            description={ts("sections.advanced.description")}
          >
            {isRunning ? (
              <SearchHighlight>
                <AppConfigPanel
                  deploymentId={appId}
                  readOnly={!can("app.editConfig")}
                />
              </SearchHighlight>
            ) : (
              <Card>
                <CardContent className="pt-6">
                  <p className="text-sm text-muted-foreground text-center">
                    {t.rich("notRunning", {
                      strong: (chunks) => (
                        <span className="font-medium text-foreground">
                          {chunks}
                        </span>
                      ),
                    })}
                  </p>
                </CardContent>
              </Card>
            )}
          </SettingsSection>
        </div>
      </div>
    </AppSection>
  );
}
