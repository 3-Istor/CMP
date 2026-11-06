"use client";

import { Avatar } from "@/components/ui/avatar";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  addProjectMember,
  removeProjectMember,
  searchKeycloakUsers,
} from "@/lib/api";
import { clearProjectsCache, useProjectMembers } from "@/lib/hooks";
import {
  ASSIGNABLE_ROLES,
  GROUP_SUFFIX,
} from "@/lib/permissions";
import type { AssignableRole, KeycloakUserResult, ProjectMember } from "@/types";
import {
  Crown,
  Eye,
  Loader2,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

interface Props {
  projectName: string;
  /**
   * Whether the viewer may add or remove members. Passed in rather than
   * resolved here so the project page reads the role once and both halves of
   * the page agree on it.
   */
  canManage?: boolean;
}

export function MembersPanel({ projectName, canManage = false }: Props) {
  const t = useTranslations("projects.members");
  const tr = useTranslations("projects");
  const tc = useTranslations("common");
  const tCode = (chunks: React.ReactNode) => <span className="font-mono">{chunks}</span>;
  const { members, guestsSupported, loading, error, refresh } =
    useProjectMembers(projectName);

  const [query, setQuery] = useState("");
  const [role, setRole] = useState<AssignableRole>("member");
  const [results, setResults] = useState<KeycloakUserResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<KeycloakUserResult | null>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Debounced Keycloak search
  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    setSelected(null);

    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (value.trim().length < 2) {
      setResults([]);
      setShowDropdown(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const data = await searchKeycloakUsers(value.trim());
        setResults(data);
        setShowDropdown(data.length > 0);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
  }, []);

  const handleSelect = (user: KeycloakUserResult) => {
    setSelected(user);
    setQuery(
      user.first_name && user.last_name
        ? `${user.first_name} ${user.last_name} (${user.username})`
        : user.username,
    );
    setShowDropdown(false);
    setResults([]);
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canManage) return;
    const username = selected?.username ?? query.trim();
    if (!username) return;

    setAdding(true);
    try {
      await addProjectMember(projectName, username, role);
      toast.success(t("added", { user: username, role: tr(`roles.${role}`) }));
      setQuery("");
      setSelected(null);
      setRole("member");
      // The target user's own project list now carries a different role.
      clearProjectsCache();
      refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // A 409 means the project has no guests group yet; the backend's message
      // names the Terraform module to re-run, so show it as-is and leave it up
      // long enough to read.
      const isMissingGroup = err instanceof ApiError && err.status === 409;
      if (isMissingGroup) {
        toast.error(msg, { duration: 12_000 });
      } else {
        toast.error(t("addFailed", { message: msg }));
      }
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveMember = async (memberUsername: string) => {
    if (!canManage) return;
    setRemoving(memberUsername);
    try {
      await removeProjectMember(projectName, memberUsername);
      toast.success(t("removed", { user: memberUsername }));
      clearProjectsCache();
      refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t("removeFailed", { message: msg }));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Add Member, admins only */}
      {!canManage ? (
        <p className="text-xs text-muted-foreground">
          {t("onlyAdmins")}
        </p>
      ) : (
      <div>
        <h3 className="text-sm font-medium mb-1">{t("addTitle")}</h3>
        <p className="text-xs text-muted-foreground mb-3">
          {t.rich("addHelp", { project: projectName, group: GROUP_SUFFIX[role], code: tCode })}
          {role === "guest" && t("guestNote")}
        </p>

        {!guestsSupported && (
          <p className="text-xs text-muted-foreground mb-3">
            {t.rich("guestsUnavailable", { project: projectName, code: tCode })}
          </p>
        )}

        <form onSubmit={handleAddMember} className="flex gap-2">
          {/* Search input with autocomplete */}
          <div ref={searchRef} className="relative flex-1">
            <div className="relative">
              <Input
                placeholder={t("searchPlaceholder")}
                value={query}
                onChange={(e) => handleQueryChange(e.target.value)}
                onFocus={() => results.length > 0 && setShowDropdown(true)}
                disabled={adding}
                autoComplete="off"
              />
              {searching && (
                <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-muted-foreground" />
              )}
            </div>

            {/* Dropdown results */}
            {showDropdown && results.length > 0 && (
              <div className="absolute z-50 top-full mt-1 w-full rounded-md border bg-popover shadow-md overflow-hidden">
                {results.map((user) => (
                  <button
                    key={user.username}
                    type="button"
                    className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-accent transition-colors"
                    onMouseDown={(e) => {
                      e.preventDefault(); // prevent blur before click
                      handleSelect(user);
                    }}
                  >
                    <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                      <span className="text-xs font-semibold text-primary">
                        {(user.first_name?.[0] || user.username[0]).toUpperCase()}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">
                        {user.first_name && user.last_name
                          ? `${user.first_name} ${user.last_name}`
                          : user.username}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">
                        {user.email || user.username}
                      </p>
                    </div>
                    <span className="ml-auto text-xs font-mono text-muted-foreground shrink-0">
                      {user.username}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Role selector. Note SelectValue takes a render-function child
              here, not a placeholder prop. */}
          <div className="w-36 shrink-0">
            <Select
              value={role}
              onValueChange={(v) => setRole(v as AssignableRole)}
              disabled={adding}
            >
              <SelectTrigger>
                <SelectValue>
                  {(v) => tr(`roles.${(v as AssignableRole) ?? "member"}`)}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {ASSIGNABLE_ROLES.filter(
                  (r) => r !== "guest" || guestsSupported,
                ).map((r) => (
                  <SelectItem key={r} value={r}>
                    {tr(`roles.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Submit */}
          <Button
            type="submit"
            disabled={adding || !query.trim()}
            title={t("addButton")}
          >
            {adding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
          </Button>
        </form>
      </div>
      )}

      {/* ── Member list ── */}
      <div>
        <h3 className="text-sm font-medium mb-3">
          {t("current")}{" "}
          {!loading && members.length > 0 && (
            <span className="text-muted-foreground font-normal">
              ({members.length})
            </span>
          )}
        </h3>

        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive bg-destructive/5 p-4 text-sm text-destructive">
            <p className="font-medium">{t("loadFailed")}</p>
            <p className="text-xs mt-1">{error}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={refresh}
            >
              {tc("retry")}
            </Button>
          </div>
        ) : members.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-muted-foreground">
            <Users className="h-8 w-8 mb-2 opacity-50" />
            <p className="text-sm font-medium">{t("empty")}</p>
            {canManage && (
              <p className="text-xs mt-1">{t("emptyHint")}</p>
            )}
          </div>
        ) : (
          <div className="rounded-lg border divide-y divide-border">
            {members.map((member) => (
              <MemberRow
                key={member.username}
                member={member}
                canManage={canManage}
                removing={removing === member.username}
                onRemove={() => handleRemoveMember(member.username)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Member row ────────────────────────────────────────────────────────────────

interface MemberRowProps {
  member: ProjectMember;
  canManage: boolean;
  removing: boolean;
  onRemove: () => void;
}

const ROLE_ICON: Record<ProjectMember["role"], typeof Crown> = {
  owner: Crown,
  admin: ShieldCheck,
  member: Users,
  guest: Eye,
};

function MemberRow({
  member,
  canManage,
  removing,
  onRemove,
}: MemberRowProps) {
  const t = useTranslations("projects.members");
  const tr = useTranslations("projects");
  const isOwner = member.role === "owner";
  const RoleIcon = ROLE_ICON[member.role] ?? Users;

  return (
    <div className="flex items-center justify-between px-4 py-3 hover:bg-muted/30 transition-colors">
      <div className="flex items-center gap-3 min-w-0">
        <Avatar
          className="h-9 w-9"
          src={member.picture}
          alt=""
          fallback={
            member.first_name && member.last_name
              ? `${member.first_name} ${member.last_name}`
              : member.username
          }
        />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium truncate">
              {member.first_name && member.last_name
                ? `${member.first_name} ${member.last_name}`
                : member.username}
            </p>
            <Badge
              variant={
                isOwner || member.role === "admin"
                  ? "default"
                  : member.role === "guest"
                    ? "outline"
                    : "secondary"
              }
              className={`shrink-0 ${isOwner ? "bg-amber-400 hover:bg-amber-400 text-amber-950" : ""}`}
            >
              <RoleIcon className="mr-1 h-3 w-3" />
              {tr(`roles.${member.role}`)}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground truncate">
            {member.email || member.username}
          </p>
        </div>
      </div>
      {isOwner ? (
        <span
          className="text-xs text-muted-foreground shrink-0 px-2"
          title={t("ownerLocked")}
        >
          {tr("roles.owner")}
        </span>
      ) : !canManage ? null : (
        <Button
          variant="ghost"
          size="sm"
          onClick={onRemove}
          disabled={removing}
          className="text-destructive hover:text-destructive hover:bg-destructive/10 shrink-0"
          title={t("remove", { username: member.username })}
        >
          {removing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="h-4 w-4" />
          )}
        </Button>
      )}
    </div>
  );
}
