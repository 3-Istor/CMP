"use client";

import { ArrowLeft } from "lucide-react";
import { useSession } from "next-auth/react";
import Link from "next/link";

import { Logo } from "@/components/brand/Logo";

/** Logo bar for signed-out visitors only: signed-in users have the sidebar. */
export function PublicTopBar() {
  const { status } = useSession();
  if (status !== "unauthenticated") return null;
  return (
    <header className="border-b border-border/60">
      <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-3">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          <Logo className="h-7 w-7" />
          <span className="font-heading font-semibold text-foreground">CNP</span>
        </Link>
      </div>
    </header>
  );
}
