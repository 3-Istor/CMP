"use client";

import { projectHref } from "@/components/layout/projectSections";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { getAgentTemplates } from "@/lib/api";
import { riskProfile } from "@/lib/ai/risk";
import { useProjects } from "@/lib/hooks";
import { useCardData } from "@/lib/useCardData";
import { cn } from "@/lib/utils";
import type { AgentTemplate } from "@/types/ai";
import { AlertCircle, Bot, Wand2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useId, useState } from "react";
import { RiskLamps } from "./CapabilityMap";
import { TEMPLATE_ICON, templateBadges } from "./CreateAgentWizard";
import { RiskBadge } from "./shared";

export function wizardHref(project: string, template: string) {
  return `${projectHref(project, "ai/new")}?template=${encodeURIComponent(template)}`;
}

function templateProfile(template: AgentTemplate) {
  const risks = new Set(templateBadges(template));
  return riskProfile({
    executesCode: risks.has("executes_code"),
    untrustedInput: risks.has("untrusted_content"),
    privateData: risks.has("private_data"),
    external: risks.has("external_send"),
  });
}

function UseTemplate({ template, onClose }: { template: AgentTemplate | null; onClose: () => void }) {
  const t = useTranslations("ai.templateGallery");
  const { projects, loading } = useProjects();
  const names = projects.map((p) => p.name).sort();
  const [chosen, setChosen] = useState("");
  const selectId = useId();
  const project = chosen || names[0] || "";
  return (
    <Dialog open={template !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("pickTitle", { template: template?.title ?? "" })}</DialogTitle>
          <DialogDescription>{t("pickHint")}</DialogDescription>
        </DialogHeader>
        {loading && names.length === 0 ? (
          <Skeleton className="h-9 rounded-lg" />
        ) : names.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("noProject")}</p>
        ) : (
          <div className="space-y-1.5">
            <Label htmlFor={selectId}>{t("project")}</Label>
            <select
              id={selectId}
              value={project}
              onChange={(e) => setChosen(e.target.value)}
              className="h-9 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm dark:bg-input/30"
            >
              {names.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          {template && project && (
            <Link href={wizardHref(project, template.id)} className={buttonVariants()}>
              <Wand2 aria-hidden />
              {t("open")}
            </Link>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TemplateCardSkeleton() {
  const t = useTranslations("ai.templateGallery");
  return (
    <li className="flex flex-col gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10" aria-hidden>
      <div className="flex items-start gap-3">
        <div className="size-9 shrink-0 animate-pulse rounded-lg bg-muted" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="h-4 w-36 animate-pulse rounded bg-muted" />
          <div className="h-3 w-full animate-pulse rounded bg-muted" />
          <div className="h-3 w-3/4 animate-pulse rounded bg-muted" />
        </div>
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">{t("tools")}</p>
        <div className="h-8 animate-pulse rounded-lg bg-muted" />
        <div className="h-8 animate-pulse rounded-lg bg-muted" />
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">{t("ruleOfTwo")}</p>
        <div className="h-5 w-32 animate-pulse rounded bg-muted" />
      </div>
      <Button className="mt-auto self-start" disabled>
        <Wand2 aria-hidden />
        {t("use")}
      </Button>
    </li>
  );
}

export function TemplatesGallery() {
  const t = useTranslations("ai.templateGallery");
  const tTpl = useTranslations("ai.templates");
  const tRisk = useTranslations("ai.ruleOfTwo");
  const templates = useCardData("ai-templates", getAgentTemplates);
  const [using, setUsing] = useState<AgentTemplate | null>(null);

  if (templates.loading && !templates.data) {
    return (
      <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy>
        {Array.from({ length: 6 }, (_, i) => <TemplateCardSkeleton key={i} />)}
      </ul>
    );
  }
  if (!templates.data) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        <AlertCircle className="size-4" aria-hidden />
        {t("error")}
      </p>
    );
  }
  return (
    <>
      <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.data.map((template) => {
          const Icon = TEMPLATE_ICON[template.icon] ?? Bot;
          const profile = templateProfile(template);
          return (
            <li key={template.id} className="flex flex-col gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="size-[18px]" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{template.title}</p>
                  <p className="text-sm text-muted-foreground">{template.description}</p>
                </div>
              </div>
              {template.pair && <Badge variant="secondary" className="self-start">{tTpl("pair")}</Badge>}
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">{t("tools")}</p>
                {template.suggested_tools.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t("noTools")}</p>
                ) : (
                  <ul className="space-y-1.5">
                    {template.suggested_tools.map((tool) => (
                      <li key={tool.name ?? tool.match} className="flex flex-wrap items-center gap-2 rounded-lg border px-2.5 py-1.5">
                        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={tool.why}>
                          {tool.name ?? tool.match}
                        </span>
                        {tool.badges.map((risk) => <RiskBadge key={risk} risk={risk} />)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">
                  {t("ruleOfTwo")} · {tRisk("verdict", { count: profile.lit.length })}
                </p>
                <RiskLamps lit={profile.lit} compact />
              </div>
              <Button className={cn("mt-auto self-start")} onClick={() => setUsing(template)}>
                <Wand2 aria-hidden />
                {t("use")}
              </Button>
            </li>
          );
        })}
      </ul>
      <UseTemplate template={using} onClose={() => setUsing(null)} />
    </>
  );
}
