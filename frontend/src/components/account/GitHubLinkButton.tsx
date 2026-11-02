"use client";

import { CheckCircle2, GitBranch, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("account.github");
  const [isLinked, setIsLinked] = useState(false);
  const [installationId, setInstallationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [manualId, setManualId] = useState("");

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
        toast.error(t("callbackFailed"));
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

  const handleLink = () => {
    // Open GitHub App installation in a new tab
    // The callback URL should be set in the GitHub App settings to return to this page
    window.open(
      "https://github.com/apps/cnp-portal/installations/new",
      "_blank",
      "noopener,noreferrer",
    );

    // Show a message to the user
    toast.info(t("opened"));
  };

  const handleManualSave = async () => {
    const trimmedId = manualId.trim();

    if (!trimmedId) {
      toast.error(t("idRequired"));
      return;
    }

    try {
      setSaving(true);
      await saveGitHubInstallationId(trimmedId);

      toast.success(t("idSaved"));
      setManualId("");

      // Refresh status
      await fetchGitHubStatus();
    } catch (error) {
      console.error("Failed to save installation ID:", error);
      toast.error(t("idSaveFailed"));
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
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t("needApp")}</p>
            <Button onClick={handleLink} className="w-full" disabled={saving}>
              {saving ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {t("linking")}
                </>
              ) : (
                <>
                  <GitBranch className="mr-2 h-4 w-4" />
                  {t("linkButton")}
                </>
              )}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t("redirectNote")}
            </p>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <Separator className="w-full" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-background px-2 text-muted-foreground">
                  {t("or")}
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="manual-installation-id" className="text-sm">
                {t("alreadyInstalled")}
              </Label>
              <p className="text-xs text-muted-foreground">
                {t("alreadyHelp")}
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
                    t("saveId")
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">{t("idHelp")}</p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
