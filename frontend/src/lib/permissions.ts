/**
 * What each project role is allowed to do.
 *
 * One table, so the answer is the same on every screen. Import `can` rather
 * than comparing roles inline — a scattered `role === "admin"` is how the
 * Members tab and the app page end up disagreeing.
 *
 * Mirrors `backend/app/core/roles.py` and the endpoint matrix in
 * `backend/app/routers/projects.py`. Changing a minimum here without
 * changing it there only changes what the UI offers, not what the API
 * accepts.
 */

import type { AssignableRole, ProjectRole } from "@/types";

/** Role precedence. Higher wins. */
export const ROLE_RANK: Record<ProjectRole, number> = {
  guest: 0,
  member: 1,
  admin: 2,
  owner: 3,
};

export type Capability =
  | "project.delete"
  | "app.deploy"
  | "app.delete"
  | "app.viewLogs"
  | "app.viewConfig"
  | "app.editConfig"
  | "app.viewSecretsLink"
  | "app.viewObservability"
  | "members.view"
  | "members.manage"
  | "finops.view"
  | "finops.act"
  | "finops.editBudget";

/**
 * The weakest role that holds each capability.
 *
 * Note `finops.view` is `guest`: everyone who can see a project can see what
 * it costs. Acting on a recommendation is `admin`, because applying one
 * commits to the app's GitOps repo and changes what is running.
 */
const MIN_ROLE: Record<Capability, ProjectRole> = {
  "project.delete": "admin",
  "app.deploy": "admin",
  "app.delete": "admin",
  "app.viewLogs": "member",
  "app.viewConfig": "member",
  "app.editConfig": "admin",
  "app.viewSecretsLink": "admin",
  "app.viewObservability": "member",
  "members.view": "member",
  "members.manage": "admin",
  "finops.view": "guest",
  "finops.act": "admin",
  "finops.editBudget": "owner",
};

/**
 * Return true if *role* holds *capability*.
 *
 * A null or undefined role — not loaded yet, or no access to this project —
 * holds nothing. Callers should render controls as absent while the role is
 * still loading rather than assume the permissive case.
 */
export function can(
  role: ProjectRole | null | undefined,
  capability: Capability,
): boolean {
  if (!role) return false;
  return ROLE_RANK[role] >= ROLE_RANK[MIN_ROLE[capability]];
}

/** Human label for a role, for badges and selects. */
export const ROLE_LABEL: Record<ProjectRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

/** One-line summary of a role, shown under the project name on cards. */
export const ROLE_BLURB: Record<ProjectRole, string> = {
  owner: "Owner",
  admin: "Full access",
  member: "Read only",
  guest: "View only",
};

/**
 * The Keycloak group backing each role, shown on the project card footer.
 *
 * `owner` has no group — it is the `projects.owner_username` column — so it
 * reads as the admins group, which the owner always effectively holds.
 */
export const GROUP_SUFFIX: Record<ProjectRole, string> = {
  owner: "admins",
  admin: "admins",
  member: "members",
  guest: "guests",
};

/** Roles an admin can assign, strongest first. */
export const ASSIGNABLE_ROLES: readonly AssignableRole[] = [
  "admin",
  "member",
  "guest",
] as const;
