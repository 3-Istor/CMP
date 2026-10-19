import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, ShieldCheck, Sparkles, UserCheck } from "lucide-react";

import { ClientInstructions } from "@/components/agents/ClientInstructions";
import { InstallButtons } from "@/components/agents/InstallButtons";
import { Mascot } from "@/components/agents/Mascot";
import { Logo } from "@/components/brand/Logo";

export const metadata: Metadata = {
  title: "Connect your AI assistant · CNP",
  description:
    "Use CNP from Claude, ChatGPT, Codex, OpenCode, Cursor or VS Code: create, change and release apps in plain language.",
};

const ASKS = [
  {
    who: "Anyone",
    text: "“Put a page online with the agenda of the October seminar.”",
  },
  {
    who: "Anyone",
    text: "“Add the venue map to my seminar page and publish it.”",
  },
  {
    who: "Manager",
    text: "“Give me a one-screen status of my team's apps this week.”",
  },
  {
    who: "Developer",
    text: "“Why is the canary of billing-api stuck? Read the logs first.”",
  },
  {
    who: "Developer",
    text: "“Release what is on main as a minor version and follow the rollout.”",
  },
];

const GUARDRAILS = [
  {
    icon: UserCheck,
    title: "Your rights, nothing more",
    text: "The assistant acts as you: it sees and changes only what you can in the CMP.",
  },
  {
    icon: ShieldCheck,
    title: "Checks that match what is at stake",
    text: "On a sandbox or demo project it just does it. On a project with real users it asks you first. On a critical one, a person approves in the CMP.",
  },
  {
    icon: Sparkles,
    title: "Everything is traced",
    text: "Each action shows who asked and through which assistant. You can disconnect an assistant at any time from your account.",
  },
];

export default function ConnectAiPage() {
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
              Connect your AI assistant to CNP
            </h1>
            <p className="mt-3 max-w-2xl text-muted-foreground">
              Ask in plain language, the assistant creates the app, writes the
              code, releases it and follows the rollout, with your company
              login and your rights.
            </p>
          </div>
        </section>

        <section className="mt-8" aria-label="Install">
          <InstallButtons />
        </section>

        <section className="mt-10" aria-labelledby="setup">
          <h2 id="setup" className="sr-only">
            Set up
          </h2>
          <ClientInstructions />
        </section>

        <section className="mt-14 grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="font-heading text-xl font-semibold">
              Things you can ask
            </h2>
            <ul className="mt-4 space-y-2.5">
              {ASKS.map((ask) => (
                <li
                  key={ask.text}
                  className="flex items-baseline gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm"
                >
                  <span className="w-20 shrink-0 text-xs font-medium text-primary">
                    {ask.who}
                  </span>
                  <span>{ask.text}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="font-heading text-xl font-semibold">
              What keeps it safe
            </h2>
            <ul className="mt-4 space-y-4">
              {GUARDRAILS.map((g) => (
                <li key={g.title} className="flex gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <g.icon className="h-4.5 w-4.5" />
                  </span>
                  <div>
                    <h3 className="text-sm font-semibold">{g.title}</h3>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                      {g.text}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>
    </div>
  );
}
