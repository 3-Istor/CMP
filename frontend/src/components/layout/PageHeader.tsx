"use client";

import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export const PAGE_WIDTH = "mx-auto w-full max-w-7xl px-6";

export interface PageTab {
  href: string;
  label: string;
  icon?: LucideIcon;
  exact?: boolean;
}

export function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
  tabs,
  tabsLabel,
  children,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: LucideIcon;
  actions?: React.ReactNode;
  tabs?: PageTab[];
  tabsLabel?: string;
  children?: React.ReactNode;
}) {
  const pathname = usePathname() ?? "";
  return (
    <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className={cn(PAGE_WIDTH, "pt-5", tabs ? "pb-0" : "pb-4")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            {Icon && (
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-[18px] w-[18px]" aria-hidden />
              </span>
            )}
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold tracking-tight">
                {title}
              </h1>
              {description && (
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {description}
                </p>
              )}
            </div>
          </div>
          {actions && (
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {actions}
            </div>
          )}
        </div>
        {children}
        {tabs && (
          <nav
            aria-label={tabsLabel}
            className="-mb-px mt-3 flex gap-1 overflow-x-auto [scrollbar-width:none]"
          >
            {tabs.map((tab) => {
              const active = tab.exact
                ? pathname === tab.href
                : pathname === tab.href || pathname.startsWith(`${tab.href}/`);
              const TabIcon = tab.icon;
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                    active
                      ? "border-primary text-foreground"
                      : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                  )}
                >
                  {TabIcon && <TabIcon className="h-4 w-4" aria-hidden />}
                  {tab.label}
                </Link>
              );
            })}
          </nav>
        )}
      </div>
    </header>
  );
}

export function PageBody({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <main className={cn(PAGE_WIDTH, "space-y-6 py-6", className)}>
      {children}
    </main>
  );
}
