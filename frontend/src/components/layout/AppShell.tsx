"use client";

import { useSession } from "next-auth/react";
import { usePathname } from "next/navigation";
import { AppSidebar } from "./AppSidebar";

/**
 * Wraps every page with the persistent, collapsible app sidebar.
 *
 * The sidebar is hidden on the auth routes and until the session is
 * authenticated (its lists need a valid token to load).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { status } = useSession();

  const hideSidebar =
    pathname?.startsWith("/auth") || status !== "authenticated";

  if (hideSidebar) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen">
      <AppSidebar />
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
