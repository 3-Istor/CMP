"use client";

import { useSession } from "next-auth/react";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { AppSidebar, MobileSidebar } from "./AppSidebar";
import { CommandPalette } from "./CommandPalette";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { status } = useSession();
  const t = useTranslations("shell.sidebar");
  const [paletteOpen, setPaletteOpen] = useState(false);

  const hideSidebar =
    pathname?.startsWith("/auth") ||
    pathname?.startsWith("/welcome") ||
    pathname?.startsWith("/connect") ||
    status !== "authenticated";

  if (hideSidebar) {
    return <>{children}</>;
  }

  const openPalette = () => setPaletteOpen(true);

  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground"
      >
        {t("skipToContent")}
      </a>
      <div className="flex min-h-screen flex-col md:flex-row">
        <AppSidebar onSearch={openPalette} />
        <MobileSidebar onSearch={openPalette} />
        <div
          id="main-content"
          tabIndex={-1}
          className="min-w-0 flex-1 outline-none"
        >
          {children}
        </div>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </>
  );
}
