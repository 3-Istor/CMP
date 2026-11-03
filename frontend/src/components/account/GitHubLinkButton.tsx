"use client";

import { CheckCircle2, GitBranch, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { connectGitHubAccount, getGitHubConnectionStatus, startGitHubLink } from "@/lib/api";
import type { GitHubConnectionStatus } from "@/types";

export function GitHubLinkButton() {
  const t = useTranslations("account.github.connection");
  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [callbackError, setCallbackError] = useState("");
  const [installation, setInstallation] = useState("");

  const reload = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await getGitHubConnectionStatus();
      setStatus(result);
      setInstallation(result.github_installation_id || "");
    } catch (error) {
      setStatus(null);
      setError(error instanceof Error ? error.message : t("statusUnavailable"));
    } finally { setLoading(false); }
  }, [t]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const url = new URL(window.location.href);
      const outcome = url.searchParams.get("github");
      if (outcome === "linked") toast.success(t("linkedChoose"));
      if (outcome === "failed") setCallbackError(t("callbackFailed"));
      if (outcome) {
        url.searchParams.delete("github");
        window.history.replaceState(null, "", url.pathname + url.search + url.hash);
      }
      void reload();
    }, 0);
    const refresh = () => { void reload(); };
    window.addEventListener("focus", refresh);
    return () => { clearTimeout(timer); window.removeEventListener("focus", refresh); };
  }, [reload, t]);

  async function authorize(mode: "authorize" | "install" = "authorize") {
    setSaving(true); setError(""); setCallbackError("");
    try {
      const result = await startGitHubLink(mode);
      window.location.assign(result.authorization_url);
    } catch (error) {
      setError(error instanceof Error ? error.message : t("connectionUnavailable"));
      setSaving(false);
    }
  }

  async function connect() {
    setSaving(true); setError("");
    try {
      const result = await connectGitHubAccount(installation);
      toast.success(t("installationSaved", { account: result.account, login: result.github_login }));
      await reload();
    } catch (error) { setError(error instanceof Error ? error.message : t("installationRefused")); }
    finally { setSaving(false); }
  }

  return <Card aria-label={t("title")}>
    <CardHeader>
      <CardTitle className="flex items-center gap-2"><GitBranch className="h-5 w-5" />{t("title")}</CardTitle>
      <CardDescription>{t("description")}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {loading && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="h-4 w-4 animate-spin" />{t("checking")}</p>}
      {status && <>
        {!status.oauth_configured && <p role="status" className="rounded border p-3 text-sm">{t("configurationRequired")}</p>}
        {status.identity_linked && <p className="flex items-center gap-2 text-sm"><CheckCircle2 className="h-4 w-4 text-green-600" />{t("linkedAs", { login: status.github_login || "" })}</p>}
        <Button variant="outline" disabled={!status.oauth_configured || saving || loading} onClick={() => void authorize()}>
          {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{status.identity_linked ? t("reconnect") : t("connect")}
        </Button>
        {status.installation_url && <Button variant="outline" disabled={saving || loading} onClick={() => void authorize("install")}>{t("install")}</Button>}
        {status.identity_linked && <>
          <label className="block space-y-1 text-sm"><span>{t("installationLabel")}</span>
            <select aria-label={t("installationLabel")} className="w-full rounded border bg-background p-2" value={installation} onChange={(event) => setInstallation(event.target.value)} disabled={saving || loading}>
              <option value="">{t("chooseInstallation")}</option>
              {status.installations.map((item) => <option key={item.id} value={item.id}>{item.account} ({item.account_type})</option>)}
            </select>
          </label>
          {!status.installations.length && <p role="status" className="text-sm">{t("noInstallations")}</p>}
          <Button onClick={() => void connect()} disabled={!installation || saving || loading}>{t("useInstallation")}</Button>
          {status.github_installation_id && <p className="text-sm text-green-600">{t("installationVerified")}</p>}
        </>}
      </>}
      {(error || callbackError) && <p role="alert" className="text-sm text-destructive">{error || callbackError}</p>}
      <Button variant="ghost" size="sm" disabled={loading || saving} onClick={() => void reload()}><RefreshCw className="mr-2 h-4 w-4" />{t("refresh")}</Button>
      <p className="text-xs text-muted-foreground">{t("permissionsHelp")}</p>
    </CardContent>
  </Card>;
}
