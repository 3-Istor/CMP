"use client";

import { Loader2, UserPlus, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/components/activity/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addPlatformRoleMember,
  getPlatformRoles,
  removePlatformRoleMember,
  searchKeycloakUsers,
} from "@/lib/api";
import type { KeycloakUserResult, PlatformRole } from "@/types";

/** Platform groups; add and remove go through the passkey step-up in request(). */
export function PlatformRolesCard() {
  const t = useTranslations("access.platformRoles");
  const [roles, setRoles] = useState<PlatformRole[] | null>(null);

  const load = useCallback(() => {
    getPlatformRoles()
      .then(setRoles)
      .catch((err) => {
        setRoles([]);
        toast.error(errorMessage(err));
      });
  }, []);

  useEffect(load, [load]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {roles === null ? (
          <Skeleton className="h-24" />
        ) : (
          <ul className="divide-y divide-border">
            {roles.map((role) => (
              <GroupRow key={role.group} role={role} onChange={load} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function GroupRow({ role, onChange }: { role: PlatformRole; onChange: () => void }) {
  const t = useTranslations("access.platformRoles");
  const listId = useId();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KeycloakUserResult[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    const id = window.setTimeout(() => {
      searchKeycloakUsers(q).then(setResults, () => setResults([]));
    }, 300);
    return () => window.clearTimeout(id);
  }, [query]);

  const change = async (username: string, add: boolean) => {
    setBusy(username);
    try {
      await (add ? addPlatformRoleMember : removePlatformRoleMember)(role.group, username);
      toast.success(t(add ? "added" : "removed", { user: username, group: role.group }));
      if (add) setQuery("");
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="space-y-2 py-3 first:pt-0 last:pb-0">
      <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
        <code>{role.group}</code>
        {!role.managed && <Badge variant="secondary">{t("terraform")}</Badge>}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {role.members.length === 0 && (
          <span className="text-xs text-muted-foreground">{t("empty")}</span>
        )}
        {role.members.map((user) => (
          <Badge key={user} variant="outline" className="gap-1 font-mono">
            {user}
            {role.managed && (
              <button
                type="button"
                onClick={() => change(user, false)}
                disabled={busy !== null}
                aria-label={t("remove", { user })}
                title={t("remove", { user })}
                className="rounded-sm text-muted-foreground hover:text-destructive"
              >
                {busy === user ? (
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                ) : (
                  <X className="h-3 w-3" aria-hidden />
                )}
              </button>
            )}
          </Badge>
        ))}
      </div>
      {role.managed && (
        <form
          className="flex max-w-sm gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim()) change(query.trim(), true);
          }}
        >
          <Input
            aria-label={t("username")}
            placeholder={t("username")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            list={listId}
            autoComplete="off"
            className="h-8"
          />
          <datalist id={listId}>
            {results.map((u) => (
              <option key={u.username} value={u.username}>
                {u.email}
              </option>
            ))}
          </datalist>
          <Button type="submit" size="sm" disabled={busy !== null || !query.trim()}>
            <UserPlus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("add")}
          </Button>
        </form>
      )}
    </li>
  );
}
