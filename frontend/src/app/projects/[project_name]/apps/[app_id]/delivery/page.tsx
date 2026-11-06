"use client";

import { EnvironmentsTab } from "@/components/environments/EnvironmentsTab";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { DeploymentHistory } from "@/components/release/DeploymentHistory";
import { ReleaseTab } from "@/components/release/ReleaseTab";
import { useTranslations } from "next-intl";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  );
}

export default function AppDeliveryPage() {
  const t = useTranslations("appPage.delivery");
  const { appId, projectName, deployment } = useAppContext();
  const { can } = useProjectContext();
  const isKubernetes = deployment.provider_type === "kubernetes";
  const settingsHref = `/projects/${encodeURIComponent(projectName)}/apps/${appId}/settings#delivery`;

  return (
    <AppSection allowed={can("app.viewLogs")}>
      <div className="space-y-10">
        {isKubernetes && can("release.view") && (
          <>
            <Section title={t("pipeline")} description={t("pipelineHint")}>
              <EnvironmentsTab
                deploymentId={appId}
                mode="delivery"
                settingsHref={settingsHref}
              />
            </Section>
            <Section title={t("rollout")} description={t("rolloutHint")}>
              <ReleaseTab
                deploymentId={appId}
                projectName={projectName}
                showSettings={false}
                showHistory={false}
              />
            </Section>
          </>
        )}
        <Section title={t("history")} description={t("historyHint")}>
          <DeploymentHistory />
        </Section>
      </div>
    </AppSection>
  );
}
