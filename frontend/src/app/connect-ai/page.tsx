import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ArrowLeft, ShieldCheck, Sparkles, UserCheck } from "lucide-react";

import { ClientInstructions } from "@/components/agents/ClientInstructions";
import { InstallButtons } from "@/components/agents/InstallButtons";
import { Mascot } from "@/components/agents/Mascot";
import { Logo } from "@/components/brand/Logo";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("agents.meta");
  return {
    title: t("title"),
    description: t("description"),
  };
}

const ASK_KEYS = ["ask1", "ask2", "ask3", "ask4", "ask5"] as const;
const ASK_WHO: Record<(typeof ASK_KEYS)[number], string> = {
  ask1: "anyone",
  ask2: "anyone",
  ask3: "manager",
  ask4: "developer",
  ask5: "developer",
};

const GUARDRAIL_ICONS = [UserCheck, ShieldCheck, Sparkles] as const;
const GUARDRAIL_KEYS = ["rights", "checks", "traced"] as const;

export default async function ConnectAiPage() {
  const t = await getTranslations("agents.connectAi");
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border/60">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-3">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            <Logo className="h-7 w-7" />
            <span className="font-heading font-semibold text-foreground">
              CNP
            </span>
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-12">
        <section className="flex flex-col items-start gap-6 sm:flex-row sm:items-center">
          <Mascot mood="happy" className="h-20 w-24 shrink-0" />
          <div>
            <h1 className="font-heading text-3xl font-bold tracking-tight text-balance sm:text-4xl">
              {t("title")}
            </h1>
            <p className="mt-3 max-w-2xl text-muted-foreground">{t("lead")}</p>
          </div>
        </section>

        <section className="mt-8" aria-label={t("install")}>
          <InstallButtons />
        </section>

        <section className="mt-10" aria-labelledby="setup">
          <h2 id="setup" className="sr-only">
            {t("setUp")}
          </h2>
          <ClientInstructions />
        </section>

        <section className="mt-14 grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="font-heading text-xl font-semibold">{t("asksTitle")}</h2>
            <ul className="mt-4 space-y-2.5">
              {ASK_KEYS.map((key) => (
                <li
                  key={key}
                  className="flex items-baseline gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm"
                >
                  <span className="w-20 shrink-0 text-xs font-medium text-primary">
                    {t(`who.${ASK_WHO[key]}`)}
                  </span>
                  <span>{t(`asks.${key}`)}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="font-heading text-xl font-semibold">
              {t("guardrailsTitle")}
            </h2>
            <ul className="mt-4 space-y-4">
              {GUARDRAIL_KEYS.map((key, i) => {
                const Icon = GUARDRAIL_ICONS[i];
                return (
                  <li key={key} className="flex gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Icon className="h-4.5 w-4.5" />
                    </span>
                    <div>
                      <h3 className="text-sm font-semibold">
                        {t(`guardrails.${key}.title`)}
                      </h3>
                      <p className="mt-0.5 text-sm text-muted-foreground">
                        {t(`guardrails.${key}.text`)}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </section>
      </main>
    </div>
  );
}
