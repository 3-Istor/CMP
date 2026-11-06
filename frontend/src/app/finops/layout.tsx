"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useFinopsMeta } from "@/lib/hooks";
import {
  Landmark,
  Leaf,
  Lightbulb,
  Sprout,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { useTranslations } from "next-intl";

export default function FinopsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = useTranslations("finops");
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
      <PageHeader
        title="FinOps"
        description={t("layout.subtitle")}
        icon={Wallet}
        tabs={tabs.map((tab) => ({ ...tab, exact: tab.exact }))}
        tabsLabel="FinOps"
      />
      <PageBody>{children}</PageBody>
    </div>
  );
}
