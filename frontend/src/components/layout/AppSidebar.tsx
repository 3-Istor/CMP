"use client";

import { Logo } from "@/components/brand/Logo";
import { Avatar } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeploymentsList, useProjects } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import {
  Boxes,
  ChevronLeft,
  Crown,
  FolderKanban,
  Home,
  PanelLeft,
  ShieldCheck,
  User,
  Users,
  Wallet,
} from "lucide-react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "cnp.sidebar.collapsed";

/**
 * Collapsed state backed by localStorage via useSyncExternalStore so it stays
 * in sync across tabs and renders without a hydration mismatch (server always
 * reports "expanded").
 */
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
    // Notify listeners in the current tab (native "storage" only fires cross-tab)
    window.dispatchEvent(new Event("storage"));
  };

  return [collapsed, toggle];
}

export function AppSidebar() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const [collapsed, toggle] = useCollapsed();

  // Poll so the list reflects projects created/deleted elsewhere (15s active).
  const { projects, loading: loadingProjects } = useProjects(15000);
  // The sidebar list rarely needs fresh data: 15s while active, 30s when idle.
  const { deployments, loading: loadingApps } = useDeploymentsList(15000, 30000);

  // Only deployments that belong to a project (and are not deleted) are linkable
  const apps = deployments.filter(
    (d) => d.project_id && d.status !== "deleted",
  );

  const displayName =
    session?.user?.name ||
    `${session?.user?.given_name ?? ""} ${session?.user?.family_name ?? ""}`.trim() ||
    session?.user?.email ||
    "Profile";

  return (
    <aside
      className={cn(
        "sticky top-0 h-screen shrink-0 border-r bg-card flex flex-col transition-[width] duration-200",
        collapsed ? "w-16" : "w-64",
      )}
    >
      {/* ── Header: home + collapse toggle ── */}
      <div className="flex items-center gap-2 px-3 h-14 border-b shrink-0">
        <Link
          href="/"
          className={cn(
            "flex items-center gap-2 min-w-0 rounded-md hover:bg-accent transition-colors px-2 py-1.5",
            collapsed ? "justify-center w-full" : "flex-1",
          )}
          title="Home"
        >
          <Logo className="h-7 w-7 shrink-0" />
          {!collapsed && (
            <span className="font-bold leading-none truncate">CNP</span>
          )}
        </Link>
        {!collapsed && (
          <button
            onClick={toggle}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors shrink-0"
            title="Collapse sidebar"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
      </div>

      {collapsed && (
        <button
          onClick={toggle}
          className="mx-auto mt-2 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          title="Expand sidebar"
        >
          <PanelLeft className="h-4 w-4" />
        </button>
      )}

      {/* ── Quick links: Home + FinOps ── */}
      <nav className="px-2 pt-2 space-y-0.5">
        <NavLink
          href="/"
          active={pathname === "/"}
          collapsed={collapsed}
          icon={<Home className="h-4 w-4 shrink-0" />}
          label="Home"
        />
        <NavLink
          href="/finops"
          active={pathname.startsWith("/finops")}
          collapsed={collapsed}
          icon={<Wallet className="h-4 w-4 shrink-0" />}
          label="FinOps"
        />
      </nav>

      {/* ── Scrollable lists ── */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-4">
        {/* Projects */}
        <Section
          title="Projects"
          icon={<FolderKanban className="h-3.5 w-3.5" />}
          collapsed={collapsed}
        >
          {loadingProjects ? (
            <ListSkeleton collapsed={collapsed} />
          ) : projects.length === 0 ? (
            !collapsed && (
              <p className="px-2 py-1 text-xs text-muted-foreground">
                No projects
              </p>
            )
          ) : (
            projects.map((p) => {
              const RoleIcon =
                p.role === "owner"
                  ? Crown
                  : p.role === "admin"
                    ? ShieldCheck
                    : Users;
              return (
                <NavLink
                  key={p.name}
                  href={`/projects/${p.name}`}
                  active={pathname === `/projects/${p.name}`}
                  collapsed={collapsed}
                  icon={<RoleIcon className="h-4 w-4 shrink-0" />}
                  label={p.name}
                  labelClassName="capitalize"
                />
              );
            })
          )}
        </Section>

        {/* Apps */}
        <Section
          title="Apps"
          icon={<Boxes className="h-3.5 w-3.5" />}
          collapsed={collapsed}
        >
          {loadingApps ? (
            <ListSkeleton collapsed={collapsed} />
          ) : apps.length === 0 ? (
            !collapsed && (
              <p className="px-2 py-1 text-xs text-muted-foreground">No apps</p>
            )
          ) : (
            apps.map((app) => {
              const href = `/projects/${app.project_id}/apps/${app.id}`;
              return (
                <NavLink
                  key={app.id}
                  href={href}
                  active={pathname === href}
                  collapsed={collapsed}
                  icon={
                    app.template_icon ? (
                      <span className="text-base leading-none shrink-0 w-4 text-center">
                        {app.template_icon}
                      </span>
                    ) : (
                      <Boxes className="h-4 w-4 shrink-0" />
                    )
                  }
                  label={app.name}
                />
              );
            })
          )}
        </Section>
      </div>

      {/* ── Profile (bottom) ── */}
      <div className="border-t p-2 shrink-0">
        <Link
          href="/account"
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-2 hover:bg-accent transition-colors",
            pathname === "/account" && "bg-accent",
            collapsed && "justify-center",
          )}
          title={displayName}
        >
          {session?.user ? (
            <Avatar
              src={session.user.image}
              alt={displayName}
              fallback={displayName}
              className="h-7 w-7"
            />
          ) : (
            <User className="h-5 w-5 shrink-0" />
          )}
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium truncate">{displayName}</p>
              <p className="text-xs text-muted-foreground truncate">
                {session?.user?.email ?? "My Account"}
              </p>
            </div>
          )}
        </Link>
      </div>
    </aside>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function Section({
  title,
  icon,
  collapsed,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  collapsed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      {collapsed ? (
        <div className="flex justify-center py-1 text-muted-foreground">
          {icon}
        </div>
      ) : (
        <div className="flex items-center gap-1.5 px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {icon}
          {title}
        </div>
      )}
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function NavLink({
  href,
  active,
  collapsed,
  icon,
  label,
  labelClassName,
}: {
  href: string;
  active: boolean;
  collapsed: boolean;
  icon: React.ReactNode;
  label: string;
  labelClassName?: string;
}) {
  return (
    <Link
      href={href}
      title={label}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
        active
          ? "bg-primary/10 text-primary font-medium"
          : "text-foreground/80 hover:bg-accent hover:text-foreground",
        collapsed && "justify-center",
      )}
    >
      {icon}
      {!collapsed && (
        <span className={cn("truncate", labelClassName)}>{label}</span>
      )}
    </Link>
  );
}

function ListSkeleton({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="space-y-1">
      {[1, 2, 3].map((i) => (
        <Skeleton
          key={i}
          className={cn("h-7 rounded-md", collapsed ? "w-8 mx-auto" : "w-full")}
        />
      ))}
    </div>
  );
}
