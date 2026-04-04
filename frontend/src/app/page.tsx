"use client";

import { CatalogGrid } from "@/components/catalog/CatalogGrid";
import { DeployModal } from "@/components/catalog/DeployModal";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { Separator } from "@/components/ui/separator";
import { createDeployment, getCatalog } from "@/lib/api";
import { useDeploymentsList } from "@/lib/hooks";
import type { CatalogTemplate } from "@/types";
import { useEffect, useState } from "react";
import { toast } from "sonner";

// Static fallback so the catalog renders without a running backend
const STATIC_CATALOG: CatalogTemplate[] = [
  {
    id: "wordpress",
    name: "WordPress",
    description: "WordPress CMS with MySQL on OpenStack and Nginx on AWS ASG.",
    icon: "📝",
    category: "CMS",
    fields: [
      {
        name: "db_password",
        label: "Database Password",
        type: "text",
        default: "changeme",
        options: null,
      },
      {
        name: "wp_admin_email",
        label: "Admin Email",
        type: "text",
        default: "admin@example.com",
        options: null,
      },
    ],
  },
  {
    id: "nextcloud",
    name: "Nextcloud",
    description: "Self-hosted file storage with DB on OpenStack, app on AWS.",
    icon: "☁️",
    category: "Storage",
    fields: [
      {
        name: "admin_password",
        label: "Admin Password",
        type: "text",
        default: "changeme",
        options: null,
      },
      {
        name: "storage_gb",
        label: "Storage (GB)",
        type: "number",
        default: 50,
        options: null,
      },
    ],
  },
  {
    id: "gitlab",
    name: "GitLab CE",
    description: "Self-hosted Git with DB on OpenStack, web on AWS ASG.",
    icon: "🦊",
    category: "DevOps",
    fields: [
      {
        name: "root_password",
        label: "Root Password",
        type: "text",
        default: "changeme",
        options: null,
      },
      {
        name: "external_url",
        label: "External URL",
        type: "text",
        default: "http://gitlab.example.com",
        options: null,
      },
    ],
  },
  {
    id: "grafana",
    name: "Grafana + Prometheus",
    description:
      "Monitoring stack with Prometheus on OpenStack, Grafana on AWS.",
    icon: "📊",
    category: "Monitoring",
    fields: [
      {
        name: "admin_password",
        label: "Grafana Admin Password",
        type: "text",
        default: "changeme",
        options: null,
      },
    ],
  },
];

export default function Home() {
  // Start with static catalog immediately — no waiting for backend
  const [templates, setTemplates] = useState<CatalogTemplate[]>(STATIC_CATALOG);
  const [selectedTemplate, setSelectedTemplate] =
    useState<CatalogTemplate | null>(null);
  const [deploying, setDeploying] = useState(false);
  const { refresh } = useDeploymentsList(-1); // Dashboard handles its own polling

  // Debug logging
  useEffect(() => {
    console.log("selectedTemplate changed:", selectedTemplate?.name || "null");
  }, [selectedTemplate]);

  useEffect(() => {
    // Try to enrich catalog from backend, but don't block on it
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000); // 3s timeout
    getCatalog()
      .then(setTemplates)
      .catch(() => {
        /* keep STATIC_CATALOG already set */
      })
      .finally(() => clearTimeout(timeout));
    return () => controller.abort();
  }, []);

  const handleDeploy = async (
    name: string,
    config: Record<string, string | number>,
  ) => {
    if (!selectedTemplate) return;
    setDeploying(true);
    try {
      await createDeployment({
        name,
        template_id: selectedTemplate.id,
        app_config: config,
      });
      toast.success(`Deployment of "${name}" started`);
      setSelectedTemplate(null);
      refresh();
    } catch (err) {
      toast.error(`Deploy failed: ${err}`);
    } finally {
      setDeploying(false);
    }
  };

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b px-6 py-4 flex items-center gap-3">
        <span className="text-2xl">⚡</span>
        <div>
          <h1 className="text-lg font-bold leading-none">ARCL CMP</h1>
          <p className="text-xs text-muted-foreground">
            Hybrid Cloud Management Platform
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          <span className="h-2 w-2 rounded-full bg-green-500 inline-block" />
          OpenStack + AWS
        </div>
      </header>

      <main className="px-6 py-8 space-y-10 max-w-7xl mx-auto">
        {/* Debug indicator */}
        {selectedTemplate && (
          <div className="fixed top-20 right-4 z-[100] bg-yellow-400 text-black px-4 py-2 rounded-lg shadow-lg font-mono text-sm">
            Selected: {selectedTemplate.name}
          </div>
        )}

        {/* Catalog */}
        <section>
          <h2 className="text-xl font-semibold mb-1">App Catalog</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Each app provisions 2 OpenStack VMs (DB) + 2 AWS instances (web) via
            ASG.
          </p>
          <CatalogGrid templates={templates} onDeploy={setSelectedTemplate} />
        </section>

        <Separator />

        {/* Dashboard */}
        <section>
          <h2 className="text-xl font-semibold mb-4">My Deployments</h2>
          <Dashboard />
        </section>
      </main>

      <DeployModal
        template={selectedTemplate}
        onClose={() => setSelectedTemplate(null)}
        onConfirm={handleDeploy}
        loading={deploying}
      />
    </div>
  );
}
