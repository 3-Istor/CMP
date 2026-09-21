import { Logo } from "@/components/brand/Logo";
import type { Metadata } from "next";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  BookOpen,
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

export const metadata: Metadata = {
  title: "CNP — Ship to hybrid cloud in a few clicks",
  description:
    "CNP is a self-service platform to deploy, secure and monitor your applications across OpenStack and AWS — no CLI, no Terraform, no IT ticket.",
};

const PLATFORM = "CNP";

// ── Hero stats (illustrative figures) ───────────────────────────────────────
const STATS = [
  { value: "90s", label: "from repo to running app" },
  { value: "8×", label: "faster than manual setup" },
  { value: "99.9%", label: "deployment success rate" },
  { value: "−35%", label: "cloud spend with FinOps" },
];

// ── Services ────────────────────────────────────────────────────────────────
const SERVICES = [
  {
    icon: MousePointerClick,
    title: "One-click deployments",
    desc: "Push your project live in a single click. CNP provisions the infrastructure, wires the networking and rolls out your app — you just press Deploy.",
  },
  {
    icon: LayoutTemplate,
    title: "Ready-made templates",
    desc: "Start from a curated template (FastAPI · Python, static HTML/CSS) or bring your own — import any existing Git repository and we take it from there.",
  },
  {
    icon: KeyRound,
    title: "Secret management",
    desc: "Store API keys, tokens and credentials in a vault-backed store. Secrets are encrypted, scoped to your project and injected at runtime — never in your code.",
  },
  {
    icon: Users,
    title: "RBAC team management",
    desc: "Invite teammates and control exactly who can view, deploy or administer each project with fine-grained role-based access control.",
  },
  {
    icon: Eye,
    title: "Apps visible in-platform",
    desc: "Every deployed app gets a live entry on CNP — open it, share it and reach your running service directly from the dashboard.",
  },
  {
    icon: Activity,
    title: "Real-time health",
    desc: "Continuous health checks surface the live status of every application, so you catch issues the moment they appear instead of hours later.",
  },
  {
    icon: DollarSign,
    title: "Built-in FinOps",
    desc: "Track the cost of each deployment in real time, spot the expensive ones and keep your cloud bill under control — no spreadsheets required.",
  },
  {
    icon: Fingerprint,
    title: "Identity-first accounts",
    desc: "Sign in through your Identity Provider (IDP). Single sign-on, centralised identity and zero extra passwords to manage.",
  },
];

// ── Benefits ────────────────────────────────────────────────────────────────
const BENEFITS = [
  {
    icon: ListChecks,
    title: "See every deployment step live",
    desc: "Watch each stage of your deployment unfold in real time — provisioning, build, rollout — with full transparency instead of a black box.",
  },
  {
    icon: SlidersHorizontal,
    title: "As simple or as deep as you want",
    desc: "Sensible defaults get non-experts shipping in minutes, while every setting stays fully customizable for teams who want fine control.",
  },
  {
    icon: Activity,
    title: "Real-time visibility",
    desc: "Live monitoring keeps you on top of your apps' health and behaviour at a glance, so you spend less time guessing and more time building.",
  },
  {
    icon: BookOpen,
    title: "A catalogue that does the heavy lifting",
    desc: "A growing catalogue of services and templates removes boilerplate and makes development and deployment dramatically easier.",
  },
  {
    icon: FileText,
    title: "Documentation, automatically",
    desc: "CNP generates documentation straight onto the repository of every deployed application — your project stays documented without the busywork.",
  },
];

export default async function WelcomePage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string }>;
}) {
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
                Cloud Native Platform
              </p>
            </div>
          </div>
          <Link
            href={loginHref}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85"
          >
            Log in
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
              Self-service deployments on OpenStack + AWS
            </span>

            <h1 className="mx-auto mt-6 max-w-3xl font-heading text-4xl font-bold tracking-tight sm:text-6xl">
              Ship your apps to the cloud in{" "}
              <span className="text-primary">a few clicks</span>
            </h1>

            <p className="mx-auto mt-5 max-w-2xl text-base text-muted-foreground sm:text-lg">
              {PLATFORM} lets your team deploy, secure and monitor full
              application stacks across a hybrid cloud — no CLI, no Terraform
              knowledge, no IT ticket. From Git repo to a running, monitored app
              in under two minutes.
            </p>

            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                href={loginHref}
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-primary px-7 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85 sm:w-auto"
              >
                <Rocket className="h-4 w-4" />
                Get started — log in
              </Link>
              <a
                href="#services"
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg border border-border bg-card px-7 text-sm font-medium text-foreground transition-colors hover:bg-muted sm:w-auto"
              >
                Explore the platform
              </a>
            </div>

            {/* Stats */}
            <dl className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-4">
              {STATS.map((s) => (
                <div key={s.label} className="bg-card px-4 py-6">
                  <dt className="font-heading text-3xl font-bold text-primary">
                    {s.value}
                  </dt>
                  <dd className="mt-1 text-xs text-muted-foreground">
                    {s.label}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-[11px] text-muted-foreground/70">
              Illustrative figures based on typical platform usage.
            </p>
          </div>
        </section>

        {/* ── Services ── */}
        <section id="services" className="mx-auto max-w-6xl px-6 py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              Everything you need to ship
            </h2>
            <p className="mt-4 text-muted-foreground">
              One platform that covers the whole journey — from your first
              deploy to running apps in production, with the cost and access
              controls to keep them healthy.
            </p>
          </div>

          <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {SERVICES.map((s) => (
              <div
                key={s.title}
                className="group flex flex-col rounded-xl border border-border bg-card p-5 ring-1 ring-transparent transition-colors hover:border-primary/40 hover:ring-primary/20"
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                  <s.icon className="h-5 w-5" />
                </div>
                <h3 className="mt-4 font-heading text-base font-semibold">
                  {s.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {s.desc}
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
                  Why teams choose {PLATFORM}
                </span>
                <h2 className="mt-3 font-heading text-3xl font-bold tracking-tight sm:text-4xl">
                  Less plumbing. More shipping.
                </h2>
                <p className="mt-4 text-muted-foreground">
                  {PLATFORM} removes the friction between your code and the
                  cloud, so your team spends its energy on the product — not on
                  infrastructure.
                </p>
                <div className="mt-8 flex flex-col gap-3 sm:flex-row lg:flex-col xl:flex-row">
                  <Link
                    href={loginHref}
                    className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-6 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85"
                  >
                    Start deploying
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>
              </div>

              <ul className="grid gap-4">
                {BENEFITS.map((b) => (
                  <li
                    key={b.title}
                    className="flex gap-4 rounded-xl border border-border bg-background p-5"
                  >
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <b.icon className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="font-heading text-base font-semibold">
                        {b.title}
                      </h3>
                      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                        {b.desc}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
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
              Your next deployment is one click away
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
              Log in with your identity provider and deploy your first project
              on {PLATFORM} today.
            </p>
            <div className="mt-8 flex justify-center">
              <Link
                href={loginHref}
                className="inline-flex h-12 items-center justify-center gap-2 rounded-lg bg-primary px-8 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/85"
              >
                <Rocket className="h-4 w-4" />
                Log in to get started
              </Link>
            </div>
            <ul className="mx-auto mt-8 flex max-w-md flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
              {["No CLI required", "No Terraform", "No IT ticket"].map((t) => (
                <li key={t} className="inline-flex items-center gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-primary" />
                  {t}
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
            <span className="hidden sm:inline">
              · Cloud Native Platform
            </span>
          </div>
          <p className="text-xs">The excellence since 2026.</p>
        </div>
      </footer>
    </div>
  );
}
