"use client";

import { errorMessage } from "@/components/activity/labels";
import {
  ProjectSection,
  useProjectContext,
} from "@/components/projects/ProjectContext";
import { AlertsCard } from "@/components/security/AlertsCard";
import { ProjectPolicyCard } from "@/components/security/SettingsCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import {
  deleteProject,
  getCustomerSignup,
  getDeletionProtection,
  getSecurityRole,
  setCustomerSignup,
  setDeletionProtection,
} from "@/lib/api";
import { Loader2, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

function Section({
  title,
  description,
  danger = false,
  children,
}: {
  title: string;
  description: string;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="border-b pb-2">
        <h2
          className={
            danger
              ? "text-base font-semibold text-destructive"
              : "text-base font-semibold"
          }
        >
          {title}
        </h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  );
}

function ToggleRow({
  title,
  description,
  checked,
  disabled,
  busy,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean | null;
  disabled: boolean;
  busy: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-6 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {busy && (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        )}
        {checked === null ? (
          <span className="text-xs text-muted-foreground">-</span>
        ) : (
          <Switch
            checked={checked}
            disabled={disabled || busy}
            onCheckedChange={onChange}
            aria-label={title}
          />
        )}
      </div>
    </div>
  );
}

export default function ProjectSettingsPage() {
  const t = useTranslations("projectSettings");
  const ta = useTranslations("actions");
  const tc = useTranslations("confirm");
  const router = useRouter();
  const { projectName, can, apps } = useProjectContext();
  const canProtect = can("project.protect");
  const canDelete = can("project.delete");

  // null until read: a switch never shows a state it has not loaded.
  const [protectedOn, setProtectedOn] = useState<boolean | null>(null);
  const [signupOn, setSignupOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<"protect" | "signup" | null>(null);
  const [securityRole, setSecurityRole] = useState<{
    admin: boolean;
    cnpAdmin: boolean;
  } | null>(null);
  const [confirmUnprotect, setConfirmUnprotect] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    getDeletionProtection(projectName).then(
      ({ enabled }) => setProtectedOn(enabled),
      () => setProtectedOn(null),
    );
    getCustomerSignup(projectName).then(
      ({ enabled }) => setSignupOn(enabled),
      () => setSignupOn(null),
    );
    getSecurityRole(projectName).then(
      (r) =>
        setSecurityRole({
          admin: r.cnp_admin || r.role === "admin",
          cnpAdmin: r.cnp_admin,
        }),
      () => setSecurityRole({ admin: false, cnpAdmin: false }),
    );
  }, [projectName]);

  const changeProtection = async (enabled: boolean) => {
    if (!canProtect) throw new Error(ta("errors.notAllowed"));
    setBusy("protect");
    try {
      const res = await setDeletionProtection(projectName, enabled);
      setProtectedOn(res.enabled);
      toast.success(
        ta(res.enabled ? "toasts.protectedOn" : "toasts.protectedOff"),
      );
    } finally {
      setBusy(null);
    }
  };

  const onProtectChange = (value: boolean) => {
    if (!value) {
      setConfirmUnprotect(true);
      return;
    }
    changeProtection(true).catch((err) =>
      toast.error(ta("errors.protect", { message: errorMessage(err) })),
    );
  };

  const onSignupChange = (value: boolean) => {
    setBusy("signup");
    setCustomerSignup(projectName, value)
      .then((res) => {
        setSignupOn(res.enabled);
        toast.success(
          ta(
            res.enabled
              ? "toasts.customerSignupOn"
              : "toasts.customerSignupOff",
          ),
        );
      })
      .catch((err) => {
        toast.error(ta("errors.customerSignup", { message: errorMessage(err) }));
        // The flag is saved even when Keycloak fails: show what is saved.
        getCustomerSignup(projectName)
          .then(({ enabled }) => setSignupOn(enabled))
          .catch(() => {});
      })
      .finally(() => setBusy(null));
  };

  const handleDelete = async () => {
    if (!canDelete) throw new Error(ta("errors.notAllowed"));
    await deleteProject(projectName);
    toast.success(ta("toasts.projectDeleting", { name: projectName }));
    router.push("/");
  };

  return (
    <ProjectSection title={t("title")} capability="app.viewObservability">
      <div className="max-w-3xl space-y-12">
        <Section
          title={t("access.title")}
          description={t("access.description")}
        >
          <Card>
            <CardContent className="divide-y">
              <ToggleRow
                title={t("access.protection")}
                description={t("access.protectionHint")}
                checked={protectedOn}
                disabled={!canProtect}
                busy={busy === "protect"}
                onChange={onProtectChange}
              />
              <ToggleRow
                title={t("access.signup")}
                description={t("access.signupHint")}
                checked={signupOn}
                disabled={!canProtect}
                busy={busy === "signup"}
                onChange={onSignupChange}
              />
            </CardContent>
          </Card>
          {!canProtect && (
            <p className="text-xs text-muted-foreground">{t("adminsOnly")}</p>
          )}
        </Section>

        <Section
          title={t("security.title")}
          description={t("security.description")}
        >
          {securityRole?.cnpAdmin && <ProjectPolicyCard project={projectName} />}
          {securityRole && (
            <AlertsCard
              project={projectName}
              app={null}
              canAdmin={securityRole.admin}
            />
          )}
        </Section>

        {canDelete && (
          <Section
            title={t("danger.title")}
            description={t("danger.description")}
            danger
          >
            <Card className="border-destructive/40">
              <CardContent className="flex flex-wrap items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{t("danger.delete")}</p>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {protectedOn
                      ? ta("reasons.protected")
                      : apps.length > 0
                        ? tc("projectHasApps.description", {
                            count: apps.length,
                          })
                        : t("danger.deleteHint")}
                  </p>
                </div>
                <Button
                  variant="destructive"
                  disabled={protectedOn === true}
                  onClick={() => setDeleting(true)}
                >
                  <Trash2 aria-hidden />
                  {t("danger.delete")}
                </Button>
              </CardContent>
            </Card>
          </Section>
        )}
      </div>

      <ConfirmDialog
        open={confirmUnprotect}
        onOpenChange={setConfirmUnprotect}
        level={1}
        title={tc("unprotect.title", { name: projectName })}
        description={tc("unprotect.description")}
        confirmLabel={tc("unprotect.confirm")}
        onConfirm={() => changeProtection(false)}
      />

      <ConfirmDialog
        open={deleting && apps.length === 0}
        onOpenChange={setDeleting}
        level={2}
        destructive
        title={tc("deleteProject.title", { name: projectName })}
        description={tc("deleteProject.description")}
        consequences={[
          tc("deleteProject.consequences.apps"),
          tc("deleteProject.consequences.vault"),
          tc("deleteProject.consequences.grafana"),
          tc("deleteProject.consequences.keycloak"),
          tc("deleteProject.consequences.argocd"),
        ]}
        expectedName={projectName}
        confirmLabel={tc("deleteProject.confirm")}
        onConfirm={handleDelete}
      />

      <Dialog open={deleting && apps.length > 0} onOpenChange={setDeleting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {tc("projectHasApps.title", { name: projectName })}
            </DialogTitle>
            <DialogDescription>
              {tc("projectHasApps.description", { count: apps.length })}
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-1 text-sm">
            {apps.map((app) => (
              <li key={app.id}>
                <Link
                  href={`/projects/${encodeURIComponent(projectName)}/apps/${app.id}/settings`}
                  className="font-medium text-primary hover:underline"
                  aria-label={tc("projectHasApps.open", { name: app.name })}
                >
                  {app.name}
                </Link>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(false)}>
              {tc("close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ProjectSection>
  );
}
