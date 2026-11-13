"use client";

import { CheckCircle2, GitBranch, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  connectGitHubInstallation,
  getGitHubConnectionStatus,
  startGitHubLink,
  unlinkGitHub,
} from "@/lib/api";
import type { GitHubConnectionStatus } from "@/types";

export function GitHubLinkButton() {
  const t = useTranslations("account.github");
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getGitHubConnectionStatus());
    } catch {
      toast.error(t("statusFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    // Set by the OAuth bridge (api/auth/github/callback) on its way back.
    const outcome = searchParams.get("github");
    if (outcome) {
      window.history.replaceState({}, document.title, window.location.pathname);
      if (outcome === "linked") toast.success(t("callbackSaved"));
      else toast.error(t("callbackFailed"));
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = async (mode: "authorize" | "install") => {
    try {
      setBusy(true);
      const { authorization_url } = await startGitHubLink(mode);
      window.location.assign(authorization_url);
    } catch {
      toast.error(t("startFailed"));
      setBusy(false);
    }
  };

  const select = async (installationId: string) => {
    try {
      setBusy(true);
      await connectGitHubInstallation(installationId);
      toast.success(t("installationSelected"));
      await refresh();
    } catch {
      toast.error(t("installationFailed"));
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    try {
      setBusy(true);
      await unlinkGitHub();
      toast.success(t("unlinked"));
      await refresh();
    } catch {
      toast.error(t("unlinkFailed"));
    } finally {
      setBusy(false);
    }
  };

  const header = (
    <CardHeader>
      <CardTitle className="flex items-center gap-2">
        <GitBranch className="h-5 w-5" />
        {t("title")}
      </CardTitle>
      <CardDescription>{t("description")}</CardDescription>
    </CardHeader>
  );

  if (loading || !status) {
    return (
      <Card>
        {header}
        <CardContent>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">{t("loadingStatus")}</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!status.oauth_configured) {
    return (
      <Card>
        {header}
        <CardContent>
          <p className="text-sm text-muted-foreground">{t("unavailable")}</p>
        </CardContent>
      </Card>
    );
  }

  if (!status.identity_linked) {
    return (
      <Card>
        {header}
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">{t("needLink")}</p>
          <Button onClick={() => go("authorize")} disabled={busy}>
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("linkButton")}
          </Button>
          <p className="text-xs text-muted-foreground">{t("redirectNote")}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      {header}
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-green-600 dark:text-green-500">
          <CheckCircle2 className="h-5 w-5" />
          <span className="font-medium">
            {t("linkedAs", { login: status.github_login ?? "" })}
          </span>
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium">{t("installations")}</p>
          {status.installations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("noInstallation")}
            </p>
          ) : (
            <ul className="space-y-2">
              {status.installations.map((item) => (
                <li
                  key={item.id}
                  className="flex items-center justify-between rounded-lg bg-muted p-3"
                >
                  <span className="text-sm">{item.account}</span>
                  {status.github_installation_id === item.id ? (
                    <span className="text-xs text-muted-foreground">
                      {t("selected")}
                    </span>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => select(item.id)}
                    >
                      {t("select")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{t("linkedHelp")}</p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => go("install")}
            disabled={busy}
          >
            {t("installButton")}
          </Button>
          <Button variant="ghost" onClick={unlink} disabled={busy}>
            {t("unlinkButton")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
