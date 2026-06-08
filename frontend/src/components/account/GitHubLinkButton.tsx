"use client";

import { CheckCircle2, GitBranch, Loader2 } from "lucide-react";
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
import { getGitHubStatus } from "@/lib/api";

export function GitHubLinkButton() {
  const [isLinked, setIsLinked] = useState(false);
  const [installationId, setInstallationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchGitHubStatus();
  }, []);

  const fetchGitHubStatus = async () => {
    try {
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
    // Redirect to GitHub App installation
    window.location.href =
      "https://github.com/apps/cnp-portal/installations/new";
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
            <Button onClick={handleLink} className="w-full">
              <GitBranch className="mr-2 h-4 w-4" />
              Link GitHub Account
            </Button>
            <p className="text-xs text-muted-foreground">
              You'll be redirected to GitHub to authorize the CNP Portal app.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
