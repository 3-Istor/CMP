"use client";

import { UserNav } from "@/components/layout/UserNav";
import { cn } from "@/lib/utils";
import { Lightbulb, TrendingUp, Wallet } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/finops", label: "Overview", icon: TrendingUp, exact: true },
  {
    href: "/finops/recommendations",
    label: "Recommandations",
    icon: Lightbulb,
    exact: false,
  },
  {
    href: "/finops/budgets",
    label: "Budgets & Alertes",
    icon: Wallet,
    exact: false,
  },
];

export default function FinopsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">FinOps</h1>
            <p className="text-xs text-muted-foreground">
              Suivi, analyse et optimisation des coûts d&apos;infrastructure.
            </p>
          </div>
          <UserNav />
        </div>

        <nav className="mt-4 flex gap-1">
          {TABS.map((tab) => {
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
