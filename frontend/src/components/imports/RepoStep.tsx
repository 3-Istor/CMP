"use client";

import { GitBranch, Loader2, Lock, PlugZap, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  getGitHubConnectionStatus,
  getImportableRepos,
  startGitHubLink,
} from "@/lib/api";
import type { GitHubConnectionStatus, ImportableRepo } from "@/types";

/** Read by the account page to offer the way back after linking GitHub. */
export const IMPORT_RETURN_KEY = "cnp-import-return";

export function RepoStep({
  projectName,
  initialRepo,
  initialBranch,
  onStart,
}: {
  projectName: string;
  initialRepo?: string;
  initialBranch?: string;
  onStart: (repo: string, branch: string | null) => Promise<void>;
}) {
  const t = useTranslations("imports.repo");
  const ids = { search: useId(), branch: useId(), list: useId() };
  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [repos, setRepos] = useState<ImportableRepo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(initialRepo ?? null);
  const [branch, setBranch] = useState(initialBranch ?? "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([getGitHubConnectionStatus().catch(() => null), getImportableRepos(projectName)])
      .then(([gh, list]) => {
        setStatus(gh);
        setRepos(list);
      })
      .catch((err) => {
        setError(err instanceof ApiError && err.status === 404 ? t("disabled") : t("loadFailed"));
        setRepos([]);
      });
  }, [projectName, t]);

  const shown = useMemo(
    () => (repos ?? []).filter((r) => r.full_name.toLowerCase().includes(query.toLowerCase())),
    [repos, query],
  );
  const repo = repos?.find((r) => r.full_name === selected) ?? null;

  const connect = async (mode: "authorize" | "install") => {
    setBusy(true);
    try {
      try {
        sessionStorage.setItem(IMPORT_RETURN_KEY, `/projects/${encodeURIComponent(projectName)}/import`);
      } catch {
        // storage blocked: the account page just offers no way back
      }
      const { authorization_url } = await startGitHubLink(mode);
      window.location.assign(authorization_url);
    } catch {
      toast.error(t("connectFailed"));
      setBusy(false);
    }
  };

  const start = async () => {
    if (!repo) return;
    setBusy(true);
    try {
      await onStart(repo.full_name, branch.trim() || null);
    } finally {
      setBusy(false);
    }
  };

  if (repos === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {t("loading")}
      </p>
    );
  }

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }

  const linked = status?.identity_linked ?? false;
  if (repos.length === 0) {
    return (
      <div className="space-y-3 rounded-xl border border-dashed p-6">
        <h3 className="flex items-center gap-2 font-medium">
          <PlugZap className="h-5 w-5" aria-hidden />
          {linked ? t("installTitle") : t("connectTitle")}
        </h3>
        <p className="max-w-prose text-sm text-muted-foreground">
          {linked ? t("installBody") : t("connectBody")}
        </p>
        {status?.oauth_configured === false ? (
          <p className="text-sm text-muted-foreground">{t("oauthUnavailable")}</p>
        ) : (
          <Button onClick={() => void connect(linked ? "install" : "authorize")} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <PlugZap aria-hidden />}
            {linked ? t("installButton") : t("connectButton")}
          </Button>
        )}
        <p className="text-xs text-muted-foreground">{t("readOnlyNote")}</p>
      </div>
    );
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        void start();
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor={ids.search}>{t("search")}</Label>
        <div className="relative">
          <Search className="pointer-events-none absolute top-2 left-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <Input
            id={ids.search}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
            autoComplete="off"
          />
        </div>
      </div>
      <fieldset className="space-y-1.5">
        <legend className="mb-1.5 text-sm font-medium">{t("choose")}</legend>
        <ul id={ids.list} className="max-h-72 space-y-1 overflow-y-auto rounded-lg border p-1">
          {shown.map((r) => (
            <li key={r.full_name}>
              <label className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-accent has-[:checked]:bg-primary/10">
                <input
                  type="radio"
                  name="repo"
                  value={r.full_name}
                  checked={selected === r.full_name}
                  onChange={() => {
                    setSelected(r.full_name);
                    setBranch(r.default_branch);
                  }}
                  className="accent-primary"
                />
                <span className="flex-1 truncate font-mono">{r.full_name}</span>
                {r.private && (
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <Lock className="h-3 w-3" aria-hidden />
                    {t("private")}
                  </span>
                )}
              </label>
            </li>
          ))}
          {shown.length === 0 && <li className="px-2 py-1.5 text-sm text-muted-foreground">{t("noMatch")}</li>}
        </ul>
      </fieldset>
      <div className="max-w-sm space-y-1.5">
        <Label htmlFor={ids.branch}>{t("branch")}</Label>
        <div className="relative">
          <GitBranch className="pointer-events-none absolute top-2 left-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <Input
            id={ids.branch}
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder={repo?.default_branch}
            className="pl-8 font-mono"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <p className="text-xs text-muted-foreground">{t("branchHelp")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!repo || busy}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          {t("analyze")}
        </Button>
        {linked && (
          <Button type="button" variant="ghost" onClick={() => void connect("install")} disabled={busy}>
            {t("moreRepos")}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{t("readOnlyNote")}</p>
    </form>
  );
}
