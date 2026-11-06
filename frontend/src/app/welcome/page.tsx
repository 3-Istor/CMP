import { AgentChatDemo } from "@/components/agents/AgentChatDemo";
import { AgentLiveStats } from "@/components/agents/AgentLiveStats";
import { Logo } from "@/components/brand/Logo";
import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Bot,
  CheckCircle2,
  DollarSign,
  Eye,
  FileText,
  Fingerprint,
  KeyRound,
  LayoutTemplate,
  ListChecks,
  MousePointerClick,
  Rocket,
  SlidersHorizontal,
  Sparkles,
  Users,
} from "lucide-react";

const PLATFORM = "CNP";

// ── Hero stats (illustrative figures) ───────────────────────────────────────
const STAT_KEYS = [
  { value: "90s", key: "repo" },
  { value: "8×", key: "faster" },
  { value: "99.9%", key: "success" },
  { value: "−35%", key: "spend" },
] as const;

// ── Services ────────────────────────────────────────────────────────────────
const SERVICES = [
  { icon: MousePointerClick, key: "oneClick" },
  { icon: LayoutTemplate, key: "templates" },
  { icon: KeyRound, key: "secrets" },
  { icon: Users, key: "rbac" },
  { icon: Eye, key: "visible" },
  { icon: Activity, key: "health" },
  { icon: DollarSign, key: "finops" },
  { icon: Fingerprint, key: "identity" },
] as const;

// ── Benefits ────────────────────────────────────────────────────────────────
const BENEFITS = [
  { icon: ListChecks, key: "steps" },
  { icon: SlidersHorizontal, key: "simple" },
  { icon: Activity, key: "visibility" },
  { icon: BookOpen, key: "catalogue" },
  { icon: FileText, key: "docs" },
] as const;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("welcome.meta");
  return {
    title: t("title"),
    description: t("description"),
  };
}

export default async function WelcomePage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string }>;
}) {
  const t = await getTranslations("welcome");
  const { callbackUrl } = await searchParams;
  const loginHref = `/auth/signin?callbackUrl=${encodeURIComponent(
    callbackUrl || "/",
  )}`;

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* ── Header ── */}
      <header className="sticky top-0 z-50 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <Logo className="h-9 w-9" />
            <div className="leading-tight">
              <p className="font-heading text-lg font-bold tracking-tight">
                {PLATFORM}
              </p>
              <p className="hidden text-xs text-muted-foreground sm:block">
                {t("tagline")}
              </p>
            </div>
          </div>
          <Link
            href={loginHref}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85"
          >
            {t("logIn")}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </header>

      <main>
        {/* ── Hero ── */}
        <section className="relative overflow-hidden">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 -z-10"
            style={{
              background:
                "radial-gradient(60% 60% at 50% 0%, color-mix(in oklch, var(--primary) 22%, transparent), transparent 70%)",
            }}
          />
          <div className="mx-auto max-w-6xl px-6 pt-20 pb-16 text-center">
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
              {t("hero.badge")}
            </span>

            <h1 className="mx-auto mt-6 max-w-3xl font-heading text-4xl font-bold tracking-tight text-balance sm:text-6xl">
              {t.rich("hero.title", {
                accent: (chunks) => <span className="text-primary">{chunks}</span>,
              })}
            </h1>

            <p className="mx-auto mt-5 max-w-2xl text-base text-muted-foreground sm:text-lg">
              {t("hero.lead", { platform: PLATFORM })}
            </p>

            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                href={loginHref}
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-primary px-7 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85 sm:w-auto"
              >
                <Rocket className="h-4 w-4" />
                {t("hero.getStarted")}
              </Link>
              <Link
                href="/connect-ai"
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg border border-border bg-card px-7 text-sm font-medium text-foreground transition-colors hover:bg-muted sm:w-auto"
              >
                <Bot className="h-4 w-4 text-primary" />
                {t("hero.connect")}
              </Link>
            </div>

            {/* What a non-developer sees on their laptop */}
            <div className="mt-14">
              <AgentChatDemo />
            </div>
          </div>
        </section>

        {/* ── Services ── */}
        <section id="services" className="mx-auto max-w-6xl px-6 py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              {t("services.title")}
            </h2>
            <p className="mt-4 text-muted-foreground">{t("services.intro")}</p>
          </div>

          <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {SERVICES.map((s) => (
              <div
                key={s.key}
                className="group flex flex-col rounded-xl border border-border bg-card p-5 ring-1 ring-transparent transition-colors hover:border-primary/40 hover:ring-primary/20"
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                  <s.icon className="h-5 w-5" />
                </div>
                <h3 className="mt-4 font-heading text-base font-semibold">
                  {t(`services.${s.key}.title`)}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {t(`services.${s.key}.desc`)}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Benefits ── */}
        <section className="border-y border-border/60 bg-card/40">
          <div className="mx-auto max-w-6xl px-6 py-20">
            <div className="grid items-start gap-12 lg:grid-cols-[0.9fr_1.1fr]">
              <div className="lg:sticky lg:top-24">
                <span className="text-sm font-semibold text-primary">
                  {t("benefits.kicker", { platform: PLATFORM })}
                </span>
                <h2 className="mt-3 font-heading text-3xl font-bold tracking-tight sm:text-4xl">
                  {t("benefits.title")}
                </h2>
                <p className="mt-4 text-muted-foreground">
                  {t("benefits.text", { platform: PLATFORM })}
                </p>
                <div className="mt-8 flex flex-col gap-3 sm:flex-row lg:flex-col xl:flex-row">
                  <Link
                    href={loginHref}
                    className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-6 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85"
                  >
                    {t("benefits.start")}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>
              </div>

              <ul className="grid gap-4">
                {BENEFITS.map((b) => (
                  <li
                    key={b.key}
                    className="flex gap-4 rounded-xl border border-border bg-background p-5"
                  >
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <b.icon className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="font-heading text-base font-semibold">
                        {t(`benefits.${b.key}.title`)}
                      </h3>
                      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                        {t(`benefits.${b.key}.desc`)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* ── By the numbers ── */}
        <section className="mx-auto max-w-6xl px-6 pt-20 text-center">
          <h2 className="font-heading text-2xl font-bold tracking-tight">
            {t("numbers")}
          </h2>
          <dl className="mx-auto mt-8 grid max-w-3xl grid-cols-2 gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-4">
            {STAT_KEYS.map((s) => (
              <div key={s.key} className="bg-card px-4 py-6">
                <dt className="font-heading text-3xl font-bold text-primary">
                  {s.value}
                </dt>
                <dd className="mt-1 text-xs text-muted-foreground">
                  {t(`stats.${s.key}`)}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-[11px] text-muted-foreground">
            {t("stats.illustrative")}
          </p>
          <AgentLiveStats />
        </section>

        {/* ── Final CTA ── */}
        <section className="mx-auto max-w-6xl px-6 py-24">
          <div className="relative overflow-hidden rounded-3xl border border-border bg-card px-6 py-16 text-center">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 -z-10"
              style={{
                background:
                  "radial-gradient(50% 80% at 50% 0%, color-mix(in oklch, var(--primary) 25%, transparent), transparent 70%)",
              }}
            />
            <Logo className="mx-auto h-14 w-14" />
            <h2 className="mt-6 font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              {t("cta.title")}
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
              {t("cta.text", { platform: PLATFORM })}
            </p>
            <div className="mt-8 flex justify-center">
              <Link
                href={loginHref}
                className="inline-flex h-12 items-center justify-center gap-2 rounded-lg bg-primary px-8 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85"
              >
                <Rocket className="h-4 w-4" />
                {t("cta.button")}
              </Link>
            </div>
            <ul className="mx-auto mt-8 flex max-w-md flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
              {(["cli", "terraform", "ticket"] as const).map((key) => (
                <li key={key} className="inline-flex items-center gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-primary" />
                  {t(`cta.${key}`)}
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      {/* ── Footer ── */}
      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm text-muted-foreground sm:flex-row">
          <div className="flex items-center gap-2.5">
            <Logo className="h-6 w-6" />
            <span className="font-heading font-semibold text-foreground">
              {PLATFORM}
            </span>
            <span className="hidden sm:inline">· {t("footer.tagline")}</span>
          </div>
          <p className="text-xs">{t("footer.excellence")}</p>
        </div>
      </footer>
    </div>
  );
}
