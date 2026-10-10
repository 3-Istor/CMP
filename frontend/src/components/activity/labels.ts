import type { ActivityEvent, ActivitySource } from "@/types";

export const SOURCE_META: Record<ActivitySource, { label: string; color: string }> = {
  cmp: { label: "CMP", color: "#2563eb" },
  kubernetes: { label: "kubectl", color: "#ea580c" },
  vault: { label: "Vault", color: "#059669" },
  keycloak: { label: "Keycloak", color: "#7c3aed" },
  deployment: { label: "Déploiements", color: "#db2777" },
};

export const SOURCE_ORDER: ActivitySource[] = [
  "cmp",
  "kubernetes",
  "vault",
  "keycloak",
  "deployment",
];

export const ACTION_GROUPS: { value: string; label: string }[] = [
  { value: "project.", label: "Projet et membres" },
  { value: "app.", label: "Applications" },
  { value: "security.", label: "Sécurité" },
  { value: "database.", label: "Bases de données" },
  { value: "finops.", label: "FinOps" },
  { value: "kubernetes.exec", label: "Shells dans un pod" },
  { value: "vault.", label: "Secrets Vault" },
  { value: "keycloak.login_error", label: "Échecs de connexion" },
  { value: "deployment.", label: "Déploiements" },
];

const EXPOSURE: Record<string, string> = {
  public: "public",
  project_users: "utilisateurs du projet",
  project_members: "membres du projet",
  project_admins: "admins du projet",
  custom: "personnalisée",
};

function str(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function configChanges(details: Record<string, unknown>): string {
  const changes = (details.changes as { path: string; before: unknown; after: unknown }[]) ?? [];
  if (changes.length === 0) return "";
  const shown = changes
    .slice(0, 2)
    .map((c) => `${c.path} ${str(c.before) || "∅"} → ${str(c.after)}`)
    .join(", ");
  return changes.length > 2 ? ` (${shown}, +${changes.length - 2})` : ` (${shown})`;
}

function kubernetesWhat(e: ActivityEvent): string {
  const [resource, name, sub] = e.target.split("/");
  const verb = e.action.replace("kubernetes.", "");
  if (sub === "exec") return `a ouvert un shell dans ${name}`;
  if (sub === "attach") return `s'est attaché à ${name}`;
  if (sub === "portforward") return `a ouvert un port-forward vers ${name}`;
  if (resource === "secrets" && (verb === "get" || verb === "list"))
    return `a lu le secret ${name ?? ""}`.trim();
  const verbs: Record<string, string> = {
    create: "a créé",
    update: "a modifié",
    patch: "a modifié",
    delete: "a supprimé",
    get: "a lu",
    list: "a listé",
  };
  return `${verbs[verb] ?? verb} ${resource}${name ? ` ${name}` : ""}`;
}

/** The event as one sentence, without its author. */
export function describe(e: ActivityEvent): string {
  const d = e.details;
  const app = e.app ?? e.target;
  switch (e.action) {
    case "project.create":
      return `a créé le projet ${e.project ?? ""}`;
    case "project.delete":
      return `a supprimé le projet ${e.project ?? ""}`;
    case "project.member.add":
      return `a ajouté ${str(d.username) || e.target} au projet (${str(d.role) || "member"})`;
    case "project.member.remove":
      return `a retiré ${e.target} du projet`;
    case "app.create":
      return `a créé l'app ${app}`;
    case "app.delete":
      return `a supprimé l'app ${app}`;
    case "app.config.update":
      return `a modifié la config de ${app}${configChanges(d)}`;
    case "app.exposure.update": {
      const before = EXPOSURE[str(d.exposure_before)] ?? str(d.exposure_before);
      const after = EXPOSURE[str(d.exposure)] ?? str(d.exposure);
      if (after) return `a passé l'exposition de ${app} de ${before || "?"} à ${after}`;
      return `a modifié les sauvegardes de ${app}`;
    }
    case "security.scan.request":
      return `a lancé un scan de sécurité de ${app}`;
    case "security.exception.request":
      return `a demandé une exception de sécurité sur ${app}`;
    case "security.exception.approve":
      return `a approuvé une exception de sécurité`;
    case "security.exception.revoke":
      return `a révoqué une exception de sécurité`;
    case "security.settings.update":
      return `a modifié les réglages de sécurité de ${app}`;
    case "security.policy.update":
      return `a modifié la politique de sécurité du projet`;
    case "security.alerts.update":
      return `a modifié la cible des alertes de sécurité`;
    case "security.alerts.test":
      return `a testé les alertes de sécurité`;
    case "database.backup":
      return `a lancé une sauvegarde de la base de ${app}`;
    case "database.restore":
      return `a restauré la base de ${app}`;
    case "finops.budget.update":
      return `a modifié le budget du projet`;
    case "finops.recommendation.apply":
      return `a appliqué une recommandation FinOps`;
    case "finops.recommendation.ignore":
      return `a ignoré une recommandation FinOps`;
    case "finops.recommendation.notify":
      return `a notifié une recommandation FinOps`;
    case "deployment.sync":
      return `a déployé ${e.target}`;
    case "keycloak.login":
      return `s'est connecté (${e.target})`;
    case "keycloak.login_error":
      return `a échoué à se connecter${d.error ? ` (${str(d.error)})` : ""}`;
    case "vault.read":
      return `a lu le secret ${e.target}`;
    case "vault.list":
      return `a listé les secrets ${e.target}`;
    case "vault.create":
    case "vault.update":
      return `a écrit le secret ${e.target}`;
    case "vault.delete":
      return `a supprimé le secret ${e.target}`;
  }
  if (e.source === "kubernetes") return kubernetesWhat(e);
  return `${e.action} ${e.target}`.trim();
}

export function timeLabel(iso: string): string {
  const when = new Date(iso);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  const hm = when.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return `aujourd'hui ${hm}`;
  return `${when.toLocaleDateString("fr-FR", { day: "numeric", month: "short" })} ${hm}`;
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
