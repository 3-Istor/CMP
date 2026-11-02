"use client";

import { Avatar } from "@/components/ui/avatar";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { LogOut, User } from "lucide-react";
import { useSession } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { LanguageSwitcher } from "./LanguageSwitcher";

export function UserNav() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const t = useTranslations("shell.userNav");

  const user = session?.user;
  const loading = status === "loading";

  const handleLogout = async () => {
    try {
      // Drop the cached access token so nothing reuses it after sign-out
      const { clearTokenCache } = await import("@/lib/api");
      clearTokenCache();

      // Call federated logout endpoint
      const response = await fetch("/api/auth/federated-logout", {
        method: "POST",
      });

      if (response.ok) {
        const data = await response.json();
        // Redirect browser to Keycloak logout URL (clears SSO cookie)
        window.location.href = data.logoutUrl;
      } else {
        console.error("Logout failed");
        // Fallback: just redirect to home
        window.location.href = "/";
      }
    } catch (error) {
      console.error("Logout error:", error);
      window.location.href = "/";
    }
  };

  if (loading) {
    return <Skeleton className="h-8 w-8 rounded-full" />;
  }

  if (!user) {
    return null;
  }

  const displayName =
    user.name || `${user.given_name || ""} ${user.family_name || ""}`.trim();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="relative h-8 w-8 rounded-full ring-2 ring-border hover:ring-primary transition-all">
          <Avatar
            src={user.image}
            alt={displayName || user.email || t("userFallback")}
            fallback={displayName || user.email || "U"}
            className="h-8 w-8"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>
          <div className="flex flex-col space-y-1">
            <p className="text-sm font-medium leading-none">{displayName}</p>
            <p className="text-xs leading-none text-muted-foreground">
              {user.email}
            </p>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => router.push("/account")}>
          <User className="mr-2 h-4 w-4" />
          <span>{t("myAccount")}</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5">
          <p className="mb-1.5 text-xs text-muted-foreground">
            {t("language")}
          </p>
          <LanguageSwitcher />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={handleLogout}>
          <LogOut className="mr-2 h-4 w-4" />
          <span>{t("logout")}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
