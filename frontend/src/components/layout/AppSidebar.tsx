"use client";

import { Logo } from "@/components/brand/Logo";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { useSecurityOverview } from "@/lib/hooks";
import { usePlatformRoles } from "@/lib/usePlatformRoles";
import { cn } from "@/lib/utils";
import {
  BookOpen,
  ChevronLeft,
  ExternalLink,
  Menu,
  PanelLeft,
  Search,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import { LinksMenu } from "./LinksMenu";
import { NotificationCenter } from "./NotificationCenter";
import { ProjectNav } from "./ProjectNav";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { projectFromPath } from "./projectSections";
import { recordRecentApp } from "./recentApps";
import { recordRecentProject } from "./recentProjects";
import { ThemeSwitch } from "./ThemeSwitch";
import { UserMenu } from "./UserMenu";
import {
  DOCS_URL,
  HOME_PAGE,
  PLATFORM_PAGES,
  WORK_PAGES,
  isPageVisible,
  useVisibleWork,
  type PageEntry,
} from "./shellData";

const STORAGE_KEY = "cnp.sidebar.collapsed";

// Backed by localStorage via useSyncExternalStore so it stays in sync across
// tabs and renders without a hydration mismatch (server always reports
// "expanded").
function useCollapsed(): [boolean, () => void] {
  const collapsed = useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => localStorage.getItem(STORAGE_KEY) === "1",
    () => false,
  );

  const toggle = () => {
    localStorage.setItem(STORAGE_KEY, collapsed ? "0" : "1");
    // Native "storage" only fires cross-tab.
    window.dispatchEvent(new Event("storage"));
  };

  return [collapsed, toggle];
}

interface BodyProps {
  collapsed: boolean;
  onSearch: () => void;
  onToggle?: () => void;
  onNavigate?: () => void;
}

export function AppSidebar({ onSearch }: { onSearch: () => void }) {
  const [collapsed, toggle] = useCollapsed();

  return (
    <aside
      className={cn(
        "sticky top-0 z-30 hidden h-screen shrink-0 flex-col border-r bg-card transition-[width] duration-200 md:flex",
        collapsed ? "w-16" : "w-64",
      )}
    >
      <SidebarBody collapsed={collapsed} onSearch={onSearch} onToggle={toggle} />
    </aside>
  );
}

export function MobileSidebar({ onSearch }: { onSearch: () => void }) {
  const t = useTranslations("shell.sidebar");
  const [open, setOpen] = useState(false);

  return (
    <div className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b bg-background px-3 md:hidden">
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("openMenu")}
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Menu className="h-5 w-5" aria-hidden />
      </Button>
      <Logo className="h-6 w-6" />
      <span className="font-bold leading-none">CNP</span>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left">
          <SheetTitle className="sr-only">{t("navigation")}</SheetTitle>
          <SheetDescription className="sr-only">
            {t("navigationHint")}
          </SheetDescription>
          <SidebarBody
            collapsed={false}
            onSearch={() => {
              setOpen(false);
              onSearch();
            }}
            onNavigate={() => setOpen(false)}
          />
        </SheetContent>
      </Sheet>
    </div>
  );
}

function SidebarBody({ collapsed, onSearch, onToggle, onNavigate }: BodyProps) {
  const pathname = usePathname();
  const t = useTranslations("shell.sidebar");
  const { projects, apps, loading } = useVisibleWork();
  const security = useSecurityOverview();
  const hasCoreAlert = security.some((s) => s.core > 0);
  const projectName = projectFromPath(pathname);
  const platformRoles = usePlatformRoles();

  useEffect(() => {
    if (projectName) recordRecentProject(projectName);
  }, [projectName]);

  const pathParts = (pathname ?? "").split("/");
  const appId = pathParts[3] === "apps" ? Number(pathParts[4]) : NaN;
  useEffect(() => {
    if (Number.isInteger(appId)) recordRecentApp(appId);
  }, [appId]);

  const counts: Partial<Record<PageEntry["key"], number | undefined>> = {
    projects: loading ? undefined : projects.length,
    apps: loading ? undefined : apps.length,
  };

  const renderLink = (page: PageEntry) => (
    <NavLink
      key={page.key}
      page={page}
      active={page.isActive(pathname)}
      collapsed={collapsed}
      label={t(page.key)}
      count={counts[page.key]}
      alert={page.key === "security" && hasCoreAlert}
      alertLabel={t("securityAlert")}
      onNavigate={onNavigate}
    />
  );

  return (
    <>
      <div className="flex h-14 shrink-0 items-center gap-2 border-b px-3">
        <Link
          href="/"
          onClick={onNavigate}
          className={cn(
            "flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent",
            collapsed ? "w-full justify-center" : "flex-1",
          )}
          aria-label={t("home")}
        >
          <Logo className="h-7 w-7 shrink-0" />
          {!collapsed && (
            <span className="truncate font-bold leading-none">CNP</span>
          )}
        </Link>
        {onToggle && !collapsed && (
          <button
            type="button"
            onClick={onToggle}
            className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            title={t("collapse")}
            aria-label={t("collapse")}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>

      {onToggle && collapsed && (
        <button
          type="button"
          onClick={onToggle}
          className="mx-auto mt-2 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title={t("expand")}
          aria-label={t("expand")}
        >
          <PanelLeft className="h-4 w-4" aria-hidden />
        </button>
      )}

      <div className="px-2 pt-2">
        <button
          type="button"
          onClick={onSearch}
          aria-keyshortcuts="Control+K Meta+K"
          aria-label={collapsed ? t("search") : undefined}
          title={t("search")}
          className={cn(
            "flex w-full items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
            collapsed && "justify-center",
          )}
        >
          <Search className="h-4 w-4 shrink-0" aria-hidden />
          {!collapsed && (
            <>
              <span className="flex-1 truncate text-left">{t("search")}</span>
              <kbd className="rounded border bg-muted px-1.5 text-[11px] font-medium">
                {t("searchHint")}
              </kbd>
            </>
          )}
        </button>
      </div>

      {projectName ? (
        <>
          <div className="px-2 pt-2">
            <ProjectSwitcher
              key={projectName}
              projectName={projectName}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          </div>
          <ProjectNav
            key={projectName}
            projectName={projectName}
            collapsed={collapsed}
            onNavigate={onNavigate}
          />
        </>
      ) : (
        <nav
          aria-label={t("navigation")}
          className="flex-1 space-y-4 overflow-y-auto px-2 py-3"
        >
          <div className="space-y-0.5">{renderLink(HOME_PAGE)}</div>
          <Group title={t("groupWork")} collapsed={collapsed}>
            {WORK_PAGES.map(renderLink)}
          </Group>
          <Group title={t("groupPlatform")} collapsed={collapsed}>
            {PLATFORM_PAGES.filter((page) =>
              isPageVisible(page, platformRoles),
            ).map(renderLink)}
          </Group>
        </nav>
      )}

      <div className="shrink-0 space-y-1 border-t p-2">
        <NotificationCenter collapsed={collapsed} onNavigate={onNavigate} />
        <LinksMenu collapsed={collapsed} />
        <a
          href={DOCS_URL}
          target="_blank"
          rel="noopener noreferrer"
          title={t("docs")}
          aria-label={collapsed ? t("docs") : undefined}
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground/80 transition-colors hover:bg-accent hover:text-foreground",
            collapsed && "justify-center",
          )}
        >
          <BookOpen className="h-4 w-4 shrink-0" aria-hidden />
          {!collapsed && (
            <>
              <span className="flex-1 truncate">{t("docs")}</span>
              <ExternalLink
                className="h-3 w-3 shrink-0 text-muted-foreground"
                aria-hidden
              />
            </>
          )}
        </a>
        <div className={cn("px-1 py-1", collapsed && "flex justify-center")}>
          <ThemeSwitch vertical={collapsed} />
        </div>
        <UserMenu collapsed={collapsed} onNavigate={onNavigate} />
      </div>
    </>
  );
}

function Group({
  title,
  collapsed,
  children,
}: {
  title: string;
  collapsed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div role="group" aria-label={title}>
      {collapsed ? (
        <div className="mx-2 mb-1 border-t" />
      ) : (
        <div className="px-2 pb-1 text-[11.5px] font-medium text-muted-foreground">
          {title}
        </div>
      )}
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function NavLink({
  page,
  active,
  collapsed,
  label,
  count,
  alert,
  alertLabel,
  onNavigate,
}: {
  page: PageEntry;
  active: boolean;
  collapsed: boolean;
  label: string;
  count?: number;
  alert: boolean;
  alertLabel: string;
  onNavigate?: () => void;
}) {
  const Icon = page.icon;
  return (
    <Link
      href={page.href}
      title={label}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? label : undefined}
      onClick={onNavigate}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
        active
          ? "bg-primary/10 font-medium text-primary"
          : "text-foreground/80 hover:bg-accent hover:text-foreground",
        collapsed && "justify-center",
      )}
    >
      <span className="relative flex">
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        {alert && collapsed && (
          <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-red-600" />
        )}
      </span>
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      {!collapsed && count !== undefined && (
        <span className="text-xs tabular-nums text-muted-foreground">
          {count}
        </span>
      )}
      {alert && !collapsed && (
        <span
          role="img"
          aria-label={alertLabel}
          title={alertLabel}
          className="h-2 w-2 shrink-0 rounded-full bg-red-600"
        />
      )}
    </Link>
  );
}
