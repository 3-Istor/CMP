"use client";

import { Avatar } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { logout } from "@/lib/logout";
import { usePlatformAdmin } from "@/lib/usePlatformAdmin";
import { cn } from "@/lib/utils";
import { LogOut, Settings, User } from "lucide-react";
import { useSession } from "next-auth/react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { LanguageSwitcher } from "./LanguageSwitcher";

export function UserMenu({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("shell.profile");
  const { data: session, status } = useSession();
  const isAdmin = usePlatformAdmin();

  const user = session?.user;
  if (status === "loading") {
    return <Skeleton className="h-10 w-full rounded-md" />;
  }
  if (!user) return null;

  const displayName =
    user.name ||
    `${user.given_name ?? ""} ${user.family_name ?? ""}`.trim() ||
    user.email ||
    t("fallback");
  const roleLabel = isAdmin ? t("roleAdmin") : t("roleMember");

  return (
    <DropdownMenu className="block w-full">
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={displayName}
          aria-label={collapsed ? displayName : undefined}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent",
            collapsed && "justify-center",
          )}
        >
          <Avatar
            src={user.image}
            alt=""
            fallback={displayName}
            className="h-7 w-7"
          />
          {!collapsed && (
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">
                {displayName}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {roleLabel}
              </span>
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuItem asChild>
          <Link href="/account" onClick={onNavigate}>
            <User className="mr-2 h-4 w-4" aria-hidden />
            {t("myProfile")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/account" onClick={onNavigate}>
            <Settings className="mr-2 h-4 w-4" aria-hidden />
            {t("accountSettings")}
          </Link>
        </DropdownMenuItem>
        <div className="px-2 py-1.5">
          <p className="mb-1.5 text-xs text-muted-foreground">
            {t("language")}
          </p>
          <LanguageSwitcher />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logout}>
          <LogOut className="mr-2 h-4 w-4" aria-hidden />
          {t("logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
