/**
 * What each project role is allowed to do.
 *
 * One table, so the answer is the same on every screen. Import `can` rather
 * than comparing roles inline, a scattered `role === "admin"` is how the
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
  | "project.protect"
  | "project.export"
  | "project.move"
  | "app.view"
  | "app.list"
  | "app.deploy"
  | "app.operate"
  | "app.pause"
  | "app.delete"
  | "app.editIcon"
  | "app.viewLogs"
  | "app.viewTraces"
  | "app.viewConfig"
  | "app.editConfig"
  | "app.viewSecretsLink"
  | "secrets.view"
  | "app.viewObservability"
  | "release.view"
  | "release.operate"
  | "release.configure"
  | "members.view"
  | "members.manage"
  | "finops.view"
  | "finops.act"
  | "finops.editBudget"
  | "agents.configure"
  | "ai.configure"
  | "ai.view"
  | "agent.manage"
  | "topology.view"
  | "topology.link"
  | "import.view"
  | "import.manage"
  | "security.view"
  | "security.manage"
  | "alerts.view"
  | "activity.view";

/**
 * The weakest role that holds each capability.
 *
 * Note `finops.view` is `guest`: everyone who can see a project can see what
 * it costs. Acting on a recommendation is `admin`, because applying one
 * commits to the app's GitOps repo and changes what is running.
 */
const MIN_ROLE: Record<Capability, ProjectRole> = {
  "project.delete": "admin",
  "project.protect": "admin",
  "project.export": "owner",
  "project.move": "admin",
  "app.view": "guest",
  "app.list": "guest",
  "app.deploy": "admin",
  "app.operate": "member",
  "app.pause": "admin",
  "app.delete": "admin",
  "app.editIcon": "member",
  "app.viewLogs": "member",
  "app.viewTraces": "member",
  "app.viewConfig": "member",
  "app.editConfig": "admin",
  "app.viewSecretsLink": "admin",
  "secrets.view": "member",
  "app.viewObservability": "guest",
  "release.view": "guest",
  "release.operate": "member",
  "release.configure": "admin",
  "members.view": "member",
  "members.manage": "admin",
  "finops.view": "guest",
  "finops.act": "admin",
  "finops.editBudget": "owner",
  "agents.configure": "admin",
  // Mirrors the ai.configure, agent.create/update/suspend/delete stake actions.
  "ai.configure": "admin",
  "ai.view": "member",
  "agent.manage": "admin",
  // Mirrors backend routers/topology.py and routers/imports.py.
  "topology.view": "member",
  "topology.link": "admin",
  "import.view": "member",
  "import.manage": "admin",
  "security.view": "member",
  "security.manage": "admin",
  "alerts.view": "member",
  "activity.view": "member",
};

/**
 * Platform-group grants on every project, whatever the project role.
 * Mirror AUDITOR_CAPS / FINOPS_CAPS in backend/app/core/roles.py
 * (test_capabilities guards the drift).
 */
const AUDITOR_CAPABILITIES: ReadonlySet<Capability> = new Set<Capability>([
  "app.view",
  "app.list",
  "app.viewObservability",
  "app.viewConfig",
  "ai.view",
  "release.view",
  "members.view",
  "finops.view",
  "topology.view",
  "security.view",
  "alerts.view",
  "import.view",
  "activity.view",
]);
const FINOPS_CAPABILITIES: ReadonlySet<Capability> = new Set<Capability>([
  "app.list",
  "app.view",
  "finops.view",
]);

export function canAsPlatform(platformRoles: readonly string[], capability: Capability): boolean {
  return (
    (platformRoles.includes("security-auditor") && AUDITOR_CAPABILITIES.has(capability)) ||
    (platformRoles.includes("finops") && FINOPS_CAPABILITIES.has(capability))
  );
}

/**
 * Return true if *role* holds *capability*.
 *
 * A null or undefined role, not loaded yet, or no access to this project,
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

/**
 * The Keycloak group backing each role, shown on the project card footer.
 *
 * `owner` has no group, it is the `projects.owner_username` column, so it
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
