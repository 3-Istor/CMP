"use client";

import { cn } from "@/lib/utils";
import { useFinopsMeta } from "@/lib/hooks";
import {
  Landmark,
  Leaf,
  Lightbulb,
  Sprout,
  TrendingUp,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

export default function FinopsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("finops");
  const pathname = usePathname();
  const role = useFinopsMeta()?.role;

  const TABS = [
    { href: "/finops", label: t("layout.tabs.overview"), icon: TrendingUp, exact: true },
    {
      href: "/finops/recommendations",
      label: t("layout.tabs.recommendations"),
      icon: Lightbulb,
      exact: false,
    },
    { href: "/finops/savings", label: t("layout.tabs.savings"), icon: Leaf, exact: false },
    { href: "/finops/greenops", label: t("layout.tabs.greenops"), icon: Sprout, exact: false },
    {
      href: "/finops/budgets",
      label: t("layout.tabs.budgets"),
      icon: Wallet,
      exact: false,
    },
  ];
  const FINANCE_TAB = {
    href: "/finops/finance",
    label: t("layout.tabs.finance"),
    icon: Landmark,
    exact: false,
  };
  const tabs =
    role === "admin" || role === "finance" ? [...TABS, FINANCE_TAB] : TABS;

  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">FinOps</h1>
            <p className="text-xs text-muted-foreground">
              {t("layout.subtitle")}
            </p>
          </div>
        </div>

        <nav className="mt-4 flex gap-1">
          {tabs.map((tab) => {
            const active = tab.exact
              ? pathname === tab.href
              : pathname.startsWith(tab.href);
            const Icon = tab.icon;
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
