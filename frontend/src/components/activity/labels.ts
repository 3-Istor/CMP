import type { ActivityEvent, ActivitySource } from "@/types";

export type Translate = (
  key: string,
  values?: Record<string, string | number>,
) => string;

export const SOURCE_META: Record<ActivitySource, { color: string }> = {
  cmp: { color: "#2563eb" },
  kubernetes: { color: "#ea580c" },
  vault: { color: "#059669" },
  keycloak: { color: "#7c3aed" },
  deployment: { color: "#db2777" },
};

export const SOURCE_ORDER: ActivitySource[] = [
  "cmp",
  "kubernetes",
  "vault",
  "keycloak",
  "deployment",
];

export const ACTION_GROUPS: { value: string; labelKey: string }[] = [
  { value: "project.", labelKey: "groups.project" },
  { value: "app.", labelKey: "groups.apps" },
  { value: "security.", labelKey: "groups.security" },
  { value: "database.", labelKey: "groups.databases" },
  { value: "finops.", labelKey: "groups.finops" },
  { value: "kubernetes.exec", labelKey: "groups.shells" },
  { value: "vault.", labelKey: "groups.vault" },
  { value: "keycloak.login_error", labelKey: "groups.logins" },
  { value: "deployment.", labelKey: "groups.deployments" },
];

const EXPOSURE_KEYS: Record<string, string> = {
  public: "public",
  project_users: "projectUsers",
  project_members: "projectMembers",
  project_admins: "projectAdmins",
  custom: "custom",
};

const K8S_VERB_KEYS: Record<string, string> = {
  create: "create",
  update: "update",
  patch: "update",
  delete: "delete",
  get: "get",
  list: "list",
};

const APP_ACTION_KEYS: Record<string, string> = {
  "app.sleep": "appSleep",
  "app.wake": "appWake",
  "app.restart": "appRestart",
  "app.icon.update": "iconUpdate",
  "app.icon.reset": "iconReset",
  "release.cut": "releaseCut",
  "release.promote": "releasePromote",
  "release.skip": "releaseSkip",
  "release.abort": "releaseAbort",
  "release.retry": "releaseRetry",
  "release.settings.update": "releaseSettings",
  "release.traffic.start": "trafficStart",
  "release.traffic.stop": "trafficStop",
};

const PLAIN_ACTION_KEYS: Record<string, string> = {
  "project.agents.update": "agentsUpdate",
  "agent.connect": "agentConnect",
  "agent.disconnect": "agentDisconnect",
  "finops.month.close": "monthClose",
  "finops.cost_centers.update": "costCenters",
  "catalog.sync": "catalogSync",
};

function str(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

export function sourceLabel(t: Translate, source: string): string {
  return source in SOURCE_META ? t(`sources.${source}`) : source;
}

function exposureLabel(t: Translate, value: string): string {
  const key = EXPOSURE_KEYS[value];
  return key ? t(`exposure.${key}`) : value;
}

function roleLabel(t: Translate, role: string): string {
  return role === "admin" || role === "member" ? t(`roles.${role}`) : role;
}

function configChanges(t: Translate, details: Record<string, unknown>): string {
  const changes =
    (details.changes as { path: string; before: unknown; after: unknown }[]) ??
    [];
  if (changes.length === 0) return "";
  const shown = changes
    .slice(0, 2)
    .map((c) => `${c.path} ${str(c.before) || "∅"} → ${str(c.after)}`)
    .join(", ");
  const list = changes.length > 2 ? `${shown}, +${changes.length - 2}` : shown;
  return ` ${t("events.detail", { detail: list })}`;
}

function kubernetesWhat(t: Translate, e: ActivityEvent): string {
  const [resource, name, sub] = e.target.split("/");
  const verb = e.action.replace("kubernetes.", "");
  if (sub === "exec") return t("events.k8sShell", { name: name ?? "" });
  if (sub === "attach") return t("events.k8sAttach", { name: name ?? "" });
  if (sub === "portforward")
    return t("events.k8sPortForward", { name: name ?? "" });
  if (resource === "secrets" && (verb === "get" || verb === "list"))
    return t("events.k8sReadSecret", { name: name ?? "" }).trim();
  const verbKey = K8S_VERB_KEYS[verb];
  const verbText = verbKey ? t(`events.k8sVerbs.${verbKey}`) : verb;
  return `${verbText} ${resource}${name ? ` ${name}` : ""}`;
}

/** The event as one sentence, without its author. */
export function describe(e: ActivityEvent, t: Translate): string {
  const d = e.details;
  const app = e.app ?? e.target;
  const appKey = APP_ACTION_KEYS[e.action];
  if (appKey) return t(`events.${appKey}`, { app });
  const plainKey = PLAIN_ACTION_KEYS[e.action];
  if (plainKey) return t(`events.${plainKey}`);
  switch (e.action) {
    case "project.create":
      return t("events.projectCreate", { project: e.project ?? "" });
    case "project.delete":
      return t("events.projectDelete", { project: e.project ?? "" });
    case "project.member.add":
      return t("events.memberAdd", {
        user: str(d.username) || e.target,
        role: roleLabel(t, str(d.role) || "member"),
      });
    case "project.member.remove":
      return t("events.memberRemove", { user: e.target });
    case "project.deletion_protection.update":
      return t(
        d.enabled === false ? "events.protectionOff" : "events.protectionOn",
      );
    case "project.customer_signup.update":
      return t(
        d.enabled === false ? "events.signupOff" : "events.signupOn",
      );
    case "app.create":
      return t("events.appCreate", { app });
    case "app.delete":
      return t("events.appDelete", { app });
    case "app.config.update":
      return `${t("events.configUpdate", { app })}${configChanges(t, d)}`;
    case "app.exposure.update": {
      const before = exposureLabel(t, str(d.exposure_before));
      const after = exposureLabel(t, str(d.exposure));
      if (after)
        return t("events.exposureChange", {
          app,
          from: before || "?",
          to: after,
        });
      return t("events.backupsChange", { app });
    }
    case "security.scan.request":
      return t("events.scanRequest", { app });
    case "security.exception.request":
      return t("events.exceptionRequest", { app });
    case "security.exception.approve":
      return t("events.exceptionApprove");
    case "security.exception.revoke":
      return t("events.exceptionRevoke");
    case "security.settings.update":
      return t("events.settingsUpdate", { app });
    case "security.policy.update":
      return t("events.policyUpdate");
    case "security.alerts.update":
      return t("events.alertsUpdate");
    case "security.alerts.test":
      return t("events.alertsTest");
    case "database.backup":
      return t("events.databaseBackup", { app });
    case "database.restore":
      return t("events.databaseRestore", { app });
    case "finops.budget.update":
      return t("events.budgetUpdate");
    case "finops.recommendation.apply":
      return t("events.recommendationApply");
    case "finops.recommendation.ignore":
      return t("events.recommendationIgnore");
    case "finops.recommendation.notify":
      return t("events.recommendationNotify");
    case "alerting.update":
      return t(
        d.enabled === false ? "events.alertDisabled" : "events.alertUpdated",
        { target: e.target },
      );
    case "deployment.sync":
      return t("events.deploymentSync", { target: e.target });
    case "keycloak.login":
      return t("events.login", { target: e.target });
    case "keycloak.login_error":
      return d.error
        ? `${t("events.loginFailed")} ${t("events.detail", { detail: str(d.error) })}`
        : t("events.loginFailed");
    case "vault.read":
      return t("events.vaultRead", { target: e.target });
    case "vault.list":
      return t("events.vaultList", { target: e.target });
    case "vault.create":
    case "vault.update":
      return t("events.vaultWrite", { target: e.target });
    case "vault.delete":
      return t("events.vaultDelete", { target: e.target });
  }
  if (e.source === "kubernetes") return kubernetesWhat(t, e);
  return `${e.action} ${e.target}`.trim();
}

export function timeLabel(t: Translate, locale: string, iso: string): string {
  const when = new Date(iso);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  const hm = when.toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (sameDay) return t("time.today", { time: hm });
  return `${when.toLocaleDateString(locale, { day: "numeric", month: "short" })} ${hm}`;
}

export function errorMessage(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  try {
    const parsed = JSON.parse(text);
    return parsed.detail ?? text;
  } catch {
    return text;
  }
}
