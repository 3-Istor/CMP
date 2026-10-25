"use client";

import { CheckCircle2, GitBranch, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { connectGitHubAccount, getGitHubConnectionStatus } from "@/lib/api";
import type { GitHubConnectionStatus } from "@/types";

export function GitHubLinkButton() {
  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await getGitHubConnectionStatus());
    } catch (error) {
      setStatus(null);
      setError(error instanceof Error ? error.message : "Vérification GitHub indisponible");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => { void reload(); }, 0);
    const refresh = () => { void reload(); };
    window.addEventListener("focus", refresh);
    return () => { clearTimeout(timer); window.removeEventListener("focus", refresh); };
  }, [reload]);

  async function connect() {
    setSaving(true);
    setError("");
    try {
      const result = await connectGitHubAccount();
      toast.success(`Compte @${result.github_login} relié à l’installation CNP existante`);
      await reload();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Liaison GitHub refusée");
    } finally {
      setSaving(false);
    }
  }

  const linked = status?.identity_linked && !!status.github_installation_id;
  return <Card aria-label="Connexion GitHub">
    <CardHeader>
      <CardTitle className="flex items-center gap-2"><GitBranch className="h-5 w-5" />Connexion GitHub</CardTitle>
      <CardDescription>Reliez votre identité GitHub et l’installation CNP déjà approuvée pour utiliser vos repos.</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Vérification de la connexion…</p>}
      {status && <>
        <p className="flex items-center gap-2 text-sm">
          {status.identity_linked && <CheckCircle2 className="h-4 w-4 text-green-600" />}
          {status.identity_linked ? `Identité GitHub liée : @${status.github_login}` : "Votre identité GitHub doit d’abord être liée à votre compte CNP."}
        </p>
        {!status.identity_linked && <>
          {status.account_url ? <>
            <Button variant="outline" disabled={saving || loading} onClick={() => { if (status.account_url) window.open(status.account_url, "_blank", "noopener,noreferrer"); }}>Lier mon compte GitHub dans CNP</Button>
            <p className="text-sm text-muted-foreground">La page CNP ouvre « Comptes liés / Linked accounts ». Cliquez sur « Lier / Link » à côté de GitHub, puis revenez ici et actualisez.</p>
          </> : <p role="status" className="rounded border p-3 text-sm">La liaison GitHub n’est pas configurée sur le SSO CNP. Un administrateur doit configurer le fournisseur GitHub dans Keycloak avant de poursuivre.</p>}
        </>}
        {linked ? <p className="flex items-center gap-2 font-medium text-green-600"><CheckCircle2 className="h-5 w-5" />Installation CNP rattachée à votre compte</p> : <>
          <Button onClick={() => void connect()} disabled={!status.identity_linked || saving || loading}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Rattacher l’installation CNP existante
          </Button>
          <p className="text-sm text-muted-foreground">CNP vérifie votre compte GitHub et votre appartenance à l’organisation avant d’enregistrer la liaison.</p>
        </>}
      </>}
      {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
      <Button variant="ghost" size="sm" disabled={loading || saving} onClick={() => void reload()}><RefreshCw className="mr-2 h-4 w-4" />Actualiser la connexion GitHub</Button>
      <p className="text-xs text-muted-foreground">L’App est installée une seule fois par l’organisation. Si un repo ne lui est pas accessible ou si ses permissions doivent changer, un administrateur GitHub doit intervenir. Les droits sur chaque repo seront revérifiés avant l’import.</p>
    </CardContent>
  </Card>;
}
