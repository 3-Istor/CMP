"use client";

import { UserNav } from "@/components/layout/UserNav";

export default function SecurityLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">Sécurité</h1>
            <p className="text-xs text-muted-foreground">
              Ce qu&apos;il faut corriger dans tes apps, et comment.
            </p>
          </div>
          <UserNav />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
