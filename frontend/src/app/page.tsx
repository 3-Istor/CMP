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

export default function Home() {
  const [templates, setTemplates] = useState<CatalogTemplate[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [selectedTemplate, setSelectedTemplate] =
    useState<CatalogTemplate | null>(null);
  const [deploying, setDeploying] = useState(false);
  const { refresh } = useDeploymentsList(-1); // Dashboard handles its own polling

  // Debug logging
  useEffect(() => {
    console.log("selectedTemplate changed:", selectedTemplate?.name || "null");
  }, [selectedTemplate]);

  useEffect(() => {
    // Load catalog from backend
    getCatalog()
      .then(setTemplates)
      .catch((err) => {
        console.error("Failed to load catalog:", err);
        toast.error("Failed to load templates from repository");
      })
      .finally(() => setLoadingCatalog(false));
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
          Terraform-based Deployments
        </div>
      </header>

      <main className="px-6 py-8 space-y-10 max-w-7xl mx-auto">
        {/* Debug indicator */}
        {selectedTemplate && (
          <div className="fixed top-20 right-4 z-100 bg-yellow-400 text-black px-4 py-2 rounded-lg shadow-lg font-mono text-sm">
            Selected: {selectedTemplate.name}
          </div>
        )}

        {/* Catalog */}
        <section>
          <h2 className="text-xl font-semibold mb-1">App Catalog</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Deploy infrastructure from Git repository templates using Terraform.
          </p>
          {loadingCatalog ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
              <span className="ml-3 text-muted-foreground">
                Loading templates...
              </span>
            </div>
          ) : templates.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p className="text-lg mb-2">No templates found in repository</p>
              <p className="text-sm">
                Check that the backend is running and templates are enabled in
                the Git repository.
              </p>
            </div>
          ) : (
            <CatalogGrid templates={templates} onDeploy={setSelectedTemplate} />
          )}
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
