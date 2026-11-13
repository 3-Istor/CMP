"use client";

import { CheckCircle2, GitBranch, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  getGitHubStatus,
  saveGitHubInstallationId,
} from "@/lib/api";

const isLinkingDisabled = (error: unknown) =>
  error instanceof ApiError && error.status === 409;

export function GitHubLinkButton() {
  const t = useTranslations("account.github");
  const [isLinked, setIsLinked] = useState(false);
  const [installationId, setInstallationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const searchParams = useSearchParams();

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

        toast.success(t("callbackSaved"));

        // Refresh status
        await fetchGitHubStatus();
      } catch (error) {
        console.error("Failed to save installation ID from callback:", error);
        window.history.replaceState(
          {},
          document.title,
          window.location.pathname,
        );
        toast.error(
          isLinkingDisabled(error)
            ? t("linkingDisabled")
            : t("callbackFailed"),
        );
        await fetchGitHubStatus();
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
      toast.error(t("statusFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    handleCallback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading || saving) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            {t("title")}
          </CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">{t("loadingStatus")}</span>
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
          {t("title")}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isLinked ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-green-600 dark:text-green-500">
              <CheckCircle2 className="h-5 w-5" />
              <span className="font-medium">{t("linked")}</span>
            </div>
            <div className="rounded-lg bg-muted p-3">
              <p className="text-xs text-muted-foreground mb-1">
                {t("installationId")}
              </p>
              <p className="text-sm font-mono">{installationId}</p>
            </div>
            <p className="text-xs text-muted-foreground">{t("linkedHelp")}</p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("unavailable")}</p>
        )}
      </CardContent>
    </Card>
  );
}
