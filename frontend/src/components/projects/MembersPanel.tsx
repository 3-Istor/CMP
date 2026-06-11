"use client";

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
import { addProjectMember, removeProjectMember } from "@/lib/api";
import { useProjectMembers } from "@/lib/hooks";
import type { ProjectMember } from "@/types";
import { Loader2, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

interface Props {
  projectName: string;
}

export function MembersPanel({ projectName }: Props) {
  const { members, loading, error, refresh } = useProjectMembers(projectName);

  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = username.trim();
    if (!trimmed) return;

    setAdding(true);
    try {
      await addProjectMember(projectName, trimmed, role);
      toast.success(`Added ${trimmed} as ${role}`);
      setUsername("");
      setRole("member");
      refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`Failed to add member: ${msg}`);
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveMember = async (memberUsername: string) => {
    setRemoving(memberUsername);
    try {
      await removeProjectMember(projectName, memberUsername);
      toast.success(`Removed ${memberUsername} from project`);
      refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`Failed to remove member: ${msg}`);
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium mb-1">Add Member</h3>
        <p className="text-xs text-muted-foreground mb-3">
          Members will be added to the appropriate Keycloak group:
          <span className="font-mono ml-1">
            project-{projectName}-{role === "admin" ? "admins" : "members"}
          </span>
        </p>
        <form onSubmit={handleAddMember} className="flex gap-2">
          <div className="flex-1">
            <Input
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={adding}
              required
            />
          </div>
          <div className="w-36">
            <Select
              value={role}
              onValueChange={(v) => setRole(v as "admin" | "member")}
              disabled={adding}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="member">Member</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={adding || !username.trim()}>
            {adding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
          </Button>
        </form>
      </div>

      <div>
        <h3 className="text-sm font-medium mb-3">Current Members</h3>
        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive bg-destructive/5 p-4 text-sm text-destructive">
            <p className="font-medium">Failed to load members</p>
            <p className="text-xs mt-1 opacity-80">{error}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={refresh}
            >
              Retry
            </Button>
          </div>
        ) : members.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-muted-foreground">
            <Users className="h-8 w-8 mb-2 opacity-50" />
            <p className="text-sm font-medium">No members yet</p>
            <p className="text-xs mt-1">Add your first team member above.</p>
          </div>
        ) : (
          <div className="rounded-lg border divide-y divide-border">
            {members.map((member) => (
              <MemberRow
                key={member.username}
                member={member}
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

interface MemberRowProps {
  member: ProjectMember;
  removing: boolean;
  onRemove: () => void;
}

function MemberRow({ member, removing, onRemove }: MemberRowProps) {
  return (
    <div className="flex items-center justify-between px-4 py-3 hover:bg-muted/30 transition-colors">
      <div className="flex items-center gap-3 min-w-0">
        <div className="h-9 w-9 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
          <span className="text-sm font-semibold text-primary">
            {member.first_name?.[0]?.toUpperCase() ||
              member.username[0]?.toUpperCase() ||
              "?"}
          </span>
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium truncate">
              {member.first_name && member.last_name
                ? `${member.first_name} ${member.last_name}`
                : member.username}
            </p>
            <Badge
              variant={member.role === "admin" ? "default" : "secondary"}
              className="shrink-0"
            >
              {member.role === "admin" ? (
                <ShieldCheck className="mr-1 h-3 w-3" />
              ) : (
                <Users className="mr-1 h-3 w-3" />
              )}
              {member.role}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground truncate">
            {member.email || member.username}
          </p>
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={onRemove}
        disabled={removing}
        className="text-destructive hover:text-destructive hover:bg-destructive/10 shrink-0"
      >
        {removing ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
    </div>
  );
}
