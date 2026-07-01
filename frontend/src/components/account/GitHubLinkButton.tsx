"use client";

import { CheckCircle2, GitBranch, Loader2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { getGitHubStatus, saveGitHubInstallationId } from "@/lib/api";

export function GitHubLinkButton() {
  const [isLinked, setIsLinked] = useState(false);
  const [installationId, setInstallationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [manualId, setManualId] = useState("");

  const searchParams = useSearchParams();

  useEffect(() => {
    handleCallback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCallback = async () => {
    // Check if we have an installation_id in the URL (GitHub callback)
    const callbackInstallationId = searchParams.get("installation_id");

    if (callbackInstallationId) {
      console.log(
        "GitHub callback detected with installation_id:",
        callbackInstallationId,
      );

      try {
        setSaving(true);
        await saveGitHubInstallationId(callbackInstallationId);

        // Clean the URL
        window.history.replaceState(
          {},
          document.title,
          window.location.pathname,
        );

        toast.success("GitHub account linked successfully!");

        // Refresh status
        await fetchGitHubStatus();
      } catch (error) {
        console.error("Failed to save installation ID from callback:", error);
        toast.error(
          "Failed to link GitHub account. Please try again or enter the ID manually.",
        );
      } finally {
        setSaving(false);
      }
    } else {
      // No callback, just fetch current status
      await fetchGitHubStatus();
    }
  };

  const fetchGitHubStatus = async () => {
    try {
      setLoading(true);
      const data = await getGitHubStatus();
      setIsLinked(!!data.github_installation_id);
      setInstallationId(data.github_installation_id);
    } catch (error) {
      console.error("Failed to fetch GitHub status:", error);
      toast.error("Failed to load GitHub integration status");
    } finally {
      setLoading(false);
    }
  };

  const handleLink = () => {
    // Open GitHub App installation in a new tab
    // The callback URL should be set in the GitHub App settings to return to this page
    window.open(
      "https://github.com/apps/cnp-portal/installations/new",
      "_blank",
      "noopener,noreferrer",
    );

    // Show a message to the user
    toast.info(
      "GitHub app installation opened in a new tab. Return here after installation to complete the setup.",
    );
  };

  const handleManualSave = async () => {
    const trimmedId = manualId.trim();

    if (!trimmedId) {
      toast.error("Please enter an installation ID");
      return;
    }

    try {
      setSaving(true);
      await saveGitHubInstallationId(trimmedId);

      toast.success("GitHub installation ID saved successfully!");
      setManualId("");

      // Refresh status
      await fetchGitHubStatus();
    } catch (error) {
      console.error("Failed to save installation ID:", error);
      toast.error(
        "Failed to save installation ID. Please check the ID and try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            GitHub Integration
          </CardTitle>
          <CardDescription>
            Link your GitHub account to deploy Kubernetes applications
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">Loading status...</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="h-5 w-5" />
          GitHub Integration
        </CardTitle>
        <CardDescription>
          Link your GitHub account to deploy Kubernetes applications
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLinked ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-green-600 dark:text-green-500">
              <CheckCircle2 className="h-5 w-5" />
              <span className="font-medium">GitHub account linked</span>
            </div>
            <div className="rounded-lg bg-muted p-3">
              <p className="text-xs text-muted-foreground mb-1">
                Installation ID
              </p>
              <p className="text-sm font-mono">{installationId}</p>
            </div>
            <p className="text-xs text-muted-foreground">
              You can now deploy Kubernetes applications with GitOps. The
              platform will automatically create and manage repositories on your
              behalf.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              To deploy Kubernetes applications, you need to install the CNP
              GitHub App. This allows the platform to create and manage
              repositories on your behalf.
            </p>
            <Button onClick={handleLink} className="w-full" disabled={saving}>
              {saving ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Linking...
                </>
              ) : (
                <>
                  <GitBranch className="mr-2 h-4 w-4" />
                  Link GitHub Account
                </>
              )}
            </Button>
            <p className="text-xs text-muted-foreground">
              You&apos;ll be redirected to GitHub to authorize the CNP Portal
              app.
            </p>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <Separator className="w-full" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-background px-2 text-muted-foreground">
                  Or
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="manual-installation-id" className="text-sm">
                Already installed the app?
              </Label>
              <p className="text-xs text-muted-foreground">
                If the CNP GitHub App is already installed on your organization,
                enter the Installation ID here.
              </p>
              <div className="flex gap-2">
                <Input
                  id="manual-installation-id"
                  type="text"
                  placeholder="12345678"
                  value={manualId}
                  onChange={(e) => setManualId(e.target.value)}
                  disabled={saving}
                  className="flex-1"
                />
                <Button
                  onClick={handleManualSave}
                  disabled={saving || !manualId.trim()}
                  variant="outline"
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    "Save ID"
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                You can find your Installation ID in your GitHub App settings.
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
