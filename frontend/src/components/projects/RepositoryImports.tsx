"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cancelRepositoryImport, createRepositoryImport, getRepositoryImports, resumeRepositoryImport, retryRepositoryImport, type RepositoryImport } from "@/lib/api";

const terminal = new Set(["completed", "failed", "cancelled"]);
const labels: Record<string, string> = {
  queued: "En attente", preparing: "Analyse et préparation", waiting_pr: "Arrêt : PR à valider",
  building: "Construction et scan de l’image", publishing: "Publication", deploying: "Déploiement",
  completed: "Terminé", failed: "Arrêt sur erreur", cancelled: "Annulé",
};

export function RepositoryImports({ projectName, onDeploymentChange }: { projectName: string; onDeploymentChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<RepositoryImport[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState(".");
  const [port, setPort] = useState("8080");
  const [healthPath, setHealthPath] = useState("/");
  const [consent, setConsent] = useState(false);

  const reload = useCallback(async () => {
    try {
      const imports = await getRepositoryImports(projectName);
      setJobs(imports);
      if (imports.some((job) => job.deployment_id)) onDeploymentChange();
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Impossible de lire les imports");
    }
  }, [projectName, onDeploymentChange]);

  useEffect(() => {
    const timer = setTimeout(() => { void reload(); }, 0);
    return () => clearTimeout(timer);
  }, [reload]);
  const active = jobs.some((job) => !terminal.has(job.status) && job.status !== "waiting_pr");
  useEffect(() => {
    if (!active || error) return;
    const timer = setInterval(() => { void reload(); }, 5000);
    return () => clearInterval(timer);
  }, [active, error, reload]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await createRepositoryImport({ project_name: projectName, app_name: name, repository_url: url,
        project_path: path, port: Number(port), health_path: healthPath, allow_changes: consent });
      setOpen(false); setConsent(false); await reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Import refusé"); }
    finally { setBusy(false); }
  }

  async function action(job: RepositoryImport, action: "resume" | "retry" | "cancel") {
    setBusy(true); setError("");
    try {
      if (action === "resume") await resumeRepositoryImport(job.id);
      else if (action === "retry") await retryRepositoryImport(job.id);
      else await cancelRepositoryImport(job.id);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Action refusée"); }
    finally { setBusy(false); }
  }

  return <section className="space-y-3" aria-label="Imports de repos">
    <Button variant="outline" onClick={() => setOpen(true)}>Importer un repo</Button>
    {error && !open && <div role="alert" className="text-sm text-destructive">{error} <Button variant="ghost" size="sm" onClick={() => void reload()}>Actualiser</Button></div>}
    {jobs.map((job) => <article key={job.id} className="rounded-lg border p-4 space-y-2">
      <p className="font-medium">{job.app_name} — {labels[job.status] ?? job.status}</p>
      <p className="text-sm text-muted-foreground" aria-live="polite">{job.message}</p>
      <a className="text-sm underline" href={job.repository_url} target="_blank" rel="noreferrer">Voir le repo source</a>
      {job.pull_request && <a className="ml-4 text-sm underline" href={`${job.repository_url}/pull/${job.pull_request}`} target="_blank" rel="noreferrer">Voir la PR #{job.pull_request}</a>}
      {job.status === "waiting_pr" && <Button className="ml-4" size="sm" disabled={busy} onClick={() => void action(job, "resume")}>Reprendre après approbation et fusion</Button>}
      {(job.status === "failed" || job.status === "cancelled") && !job.deployment_id && <Button className="ml-4" size="sm" disabled={busy} onClick={() => void action(job, "retry")}>Revérifier les droits et relancer</Button>}
      {!terminal.has(job.status) && job.status !== "deploying" && <Button className="ml-2" variant="ghost" size="sm" disabled={busy} onClick={() => void action(job, "cancel")}>Annuler</Button>}
      {Object.keys(job.report).length > 0 && <details><summary className="cursor-pointer text-sm">Rapport des contrôles</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(job.report, null, 2)}</pre></details>}
    </article>)}
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader><DialogTitle>Importer dans {projectName}</DialogTitle></DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <p className="text-sm text-muted-foreground">Le portail vérifie vos droits avant tout accès au repo. Votre identité GitHub doit être liée au compte CNP. Les fichiers nécessaires sont proposés par PR, à faire approuver et fusionner avant de reprendre ici. Le déploiement utilise ce projet existant après validation complète.</p>
          <div className="space-y-1"><Label htmlFor="import-name">Nom de l’application</Label><Input id="import-name" value={name} onChange={(e) => setName(e.target.value)} required pattern="[a-z][a-z0-9-]{0,40}[a-z0-9]" /></div>
          <div className="space-y-1"><Label htmlFor="import-url">Repo GitHub (branche principale main)</Label><Input id="import-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)} required placeholder="https://github.com/organisation/application" /></div>
          <div className="space-y-1"><Label htmlFor="import-path">Dossier de l’application</Label><Input id="import-path" value={path} onChange={(e) => setPath(e.target.value)} required /></div>
          <div className="space-y-1"><Label htmlFor="import-port">Port HTTP du conteneur</Label><Input id="import-port" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} required /></div>
          <div className="space-y-1"><Label htmlFor="import-health">Chemin HTTP pour vérifier le démarrage</Label><Input id="import-health" value={healthPath} onChange={(e) => setHealthPath(e.target.value)} required placeholder="/ ou /health" /></div>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required /><span>J’autorise CNP à cloner ce repo et à proposer les fichiers nécessaires sur une branche par PR. Je validerai la PR avant le déploiement.</span></label>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={busy || !consent}>{busy ? "Vérification…" : "Vérifier les droits et lancer l’import"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  </section>;
}
