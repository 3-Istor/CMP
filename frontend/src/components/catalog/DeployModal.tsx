"use client";

import { AlertCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getGitHubStatus } from "@/lib/api";
import type { CatalogTemplate } from "@/types";

interface Props {
  template: CatalogTemplate | null;
  onClose: () => void;
  onConfirm: (name: string, config: Record<string, string | number>) => void;
  loading?: boolean;
}

export function DeployModal({ template, onClose, onConfirm, loading }: Props) {
  const [appName, setAppName] = useState("");
  const [fieldValues, setFieldValues] = useState<
    Record<string, string | number>
  >({});
  const [hasGitHub, setHasGitHub] = useState(false);
  const [checkingGitHub, setCheckingGitHub] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const isOpen = !!template;
  const isKubernetes = template?.category === "paas";

  // Check GitHub status when opening a Kubernetes template
  useEffect(() => {
    if (template && isKubernetes) {
      setCheckingGitHub(true);
      getGitHubStatus()
        .then((data) => setHasGitHub(!!data.github_installation_id))
        .catch(() => setHasGitHub(false))
        .finally(() => setCheckingGitHub(false));
    }
  }, [template, isKubernetes]);

  // Reset form when template changes
  useEffect(() => {
    if (template) {
      setAppName("");

      setFieldValues(
        Object.fromEntries(
          template.fields.map((f) => [f.name, f.default ?? ""]),
        ),
      );
      // Focus the name input after the panel animates in
      setTimeout(() => inputRef.current?.focus(), 150);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template?.id]);

  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen && !loading) onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, loading, onClose]);

  const setField = (name: string, value: string | number) =>
    setFieldValues((prev) => ({ ...prev, [name]: value }));

  // Check if all required fields are filled
  const isFormValid = () => {
    if (!appName.trim()) return false;
    if (!template) return false;

    // Check all required fields
    for (const field of template.fields) {
      if (field.required) {
        const value = fieldValues[field.name];
        if (value === null || value === undefined || value === "") {
          return false;
        }
      }
    }
    return true;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isFormValid()) return;
    if (isKubernetes && !hasGitHub) return; // Prevent submit if GitHub not linked
    onConfirm(appName.trim(), fieldValues);
  };

  const canDeploy = isFormValid() && (!isKubernetes || hasGitHub);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={() => !loading && onClose()}
        className={`fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity duration-200 ${
          isOpen
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
      />

      {/* Side panel */}
      <div
        className={`fixed right-0 top-0 z-50 h-full w-full max-w-md bg-card border-l border-border shadow-2xl flex flex-col transition-transform duration-300 ease-out ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {template && (
          <>
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-6 py-5">
              <div className="flex items-center gap-3">
                <span className="text-3xl">{template.icon}</span>
                <div>
                  <h2 className="text-base font-semibold">
                    Deploy {template.name}
                  </h2>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    2 OpenStack VMs + 2 AWS instances via ASG
                  </p>
                </div>
              </div>
              <button
                onClick={onClose}
                disabled={loading}
                className="rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                aria-label="Close"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>

            {/* Form */}
            <form
              onSubmit={handleSubmit}
              className="flex flex-col flex-1 overflow-y-auto"
            >
              <div className="flex-1 space-y-5 px-6 py-6">
                {/* App name */}
                <div className="space-y-2">
                  <Label htmlFor="app-name" className="text-sm font-medium">
                    App Name <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    ref={inputRef}
                    id="app-name"
                    placeholder={`my-${template.id}`}
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                    required
                  />
                  <p className="text-xs text-muted-foreground">
                    Used to identify this deployment. Must be unique.
                  </p>
                </div>

                {/* Template-specific fields */}
                {template.fields.length > 0 && (
                  <div className="space-y-4">
                    <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Configuration
                    </div>
                    {template.fields.map((field) => (
                      <div key={field.name} className="space-y-2">
                        <Label
                          htmlFor={field.name}
                          className="text-sm font-medium"
                        >
                          {field.label}
                          {field.required && (
                            <span className="text-destructive ml-1">*</span>
                          )}
                        </Label>
                        {field.type === "select" && field.options ? (
                          <select
                            id={field.name}
                            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                            value={String(
                              fieldValues[field.name] ?? field.default ?? "",
                            )}
                            onChange={(e) =>
                              setField(field.name, e.target.value)
                            }
                            required={field.required}
                          >
                            {field.required && (
                              <option value="" disabled>
                                Select an option...
                              </option>
                            )}
                            {field.options.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Input
                            id={field.name}
                            type={field.type === "number" ? "number" : "text"}
                            value={String(
                              fieldValues[field.name] ?? field.default ?? "",
                            )}
                            onChange={(e) =>
                              setField(
                                field.name,
                                field.type === "number"
                                  ? Number(e.target.value)
                                  : e.target.value,
                              )
                            }
                            required={field.required}
                            placeholder={
                              field.required ? "Required" : "Optional"
                            }
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* GitHub Account Warning for Kubernetes */}
                {isKubernetes && !checkingGitHub && !hasGitHub && (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>GitHub Account Required</AlertTitle>
                    <AlertDescription>
                      To deploy Kubernetes applications, you need to link your
                      GitHub account first.{" "}
                      <Link
                        href="/account"
                        className="underline font-medium hover:no-underline"
                      >
                        Go to Account Settings
                      </Link>
                    </AlertDescription>
                  </Alert>
                )}

                {/* Info box */}
                <div className="rounded-lg border border-border bg-muted/40 p-4 space-y-1.5 text-xs text-muted-foreground">
                  <div className="font-medium text-foreground">
                    What will be provisioned
                  </div>
                  {isKubernetes ? (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />
                        Private GitHub repository with template code
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
                        Kubernetes namespace with RBAC
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
                        ArgoCD Application for GitOps
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-yellow-400" />
                        Vault secrets path
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
                        2× OpenStack VMs - stateful DB layer
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-orange-400" />
                        2× AWS t3.micro - stateless web layer (ASG + ALB)
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-yellow-400" />
                        Auto-rollback if AWS step fails
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Footer */}
              <div className="border-t border-border px-6 py-4 flex gap-3 justify-end bg-muted/20">
                <Button
                  type="button"
                  variant="outline"
                  onClick={onClose}
                  disabled={loading}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={loading || !canDeploy}>
                  {loading ? (
                    <span className="flex items-center gap-2">
                      <span className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                      Deploying…
                    </span>
                  ) : (
                    "Deploy"
                  )}
                </Button>
              </div>
            </form>
          </>
        )}
      </div>
    </>
  );
}
