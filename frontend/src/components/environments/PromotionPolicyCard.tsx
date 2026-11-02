"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { updatePromotionPolicy } from "@/lib/api";
import type { OnInconclusive, PromotionPolicy, SuiteSpec } from "@/types";
import { ListChecks, Plus, Save, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "../release/format";

const INCONCLUSIVE_OPTIONS: OnInconclusive[] = ["ask", "fail", "pass"];

function newSuite(kind: SuiteSpec["kind"], index: number): SuiteSpec {
  return kind === "external"
    ? { name: `e2e-${index}`, kind, workflow: "cnp-verify.yml", timeoutMinutes: 30, prodSafe: false }
    : {
        name: `metriques-${index}`,
        kind,
        windowMinutes: 15,
        minRequests: 50,
        errorRateMax: 1,
        p95Ms: null,
        onInconclusive: "ask",
        prodSafe: true,
      };
}

function NumberField({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  disabled: boolean;
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Input
        id={id}
        type="number"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        disabled={disabled}
      />
    </div>
  );
}

export function PromotionPolicyCard({
  deploymentId,
  policy,
  canAdminister,
  onSaved,
}: {
  deploymentId: number;
  policy: PromotionPolicy;
  canAdminister: boolean;
  onSaved: () => void;
}) {
  const t = useTranslations("environments");
  const [draft, setDraft] = useState<PromotionPolicy>(policy);
  const [saving, setSaving] = useState(false);
  const locked = !canAdminister || saving;

  const setSuite = (index: number, patch: Partial<SuiteSpec>) =>
    setDraft((d) => ({ ...d, suites: d.suites.map((s, i) => (i === index ? { ...s, ...patch } : s)) }));

  const save = async () => {
    setSaving(true);
    try {
      await updatePromotionPolicy(deploymentId, draft);
      toast.success(t("policy.saved"));
      onSaved();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ListChecks className="h-4 w-4 text-muted-foreground" />
          {t("policy.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <p className="text-muted-foreground">{t("policy.intro")}</p>

        <ol className="space-y-3">
          <li className="rounded-md border px-3 py-2 text-muted-foreground">
            1. <span className="font-medium text-foreground">smoke</span> · {t("policy.platformAlways")}
          </li>
          {draft.suites.map((suite, index) => (
            <li key={index} className="space-y-3 rounded-md border px-3 py-3">
              <div className="flex flex-wrap items-end gap-3">
                <span className="pb-2 text-muted-foreground">{index + 2}.</span>
                <div className="min-w-0 flex-1 space-y-1">
                  <Label htmlFor={`suite-name-${index}`} className="text-xs">
                    {t("policy.suiteName")}
                  </Label>
                  <Input
                    id={`suite-name-${index}`}
                    value={suite.name}
                    onChange={(e) => setSuite(index, { name: e.target.value })}
                    disabled={locked}
                  />
                </div>
                <span className="pb-2 text-xs text-muted-foreground">
                  {suite.kind === "external" ? t("policy.externalSuite") : t("policy.metricsSuite")}
                </span>
                {canAdminister && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("policy.removeSuite", { name: suite.name })}
                    onClick={() =>
                      setDraft((d) => ({ ...d, suites: d.suites.filter((_, i) => i !== index) }))
                    }
                    disabled={locked}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
              {suite.kind === "external" ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor={`suite-workflow-${index}`} className="text-xs">
                      {t("policy.workflowFile")}
                    </Label>
                    <Input
                      id={`suite-workflow-${index}`}
                      className="font-mono"
                      value={suite.workflow ?? ""}
                      onChange={(e) => setSuite(index, { workflow: e.target.value })}
                      disabled={locked}
                    />
                  </div>
                  <NumberField
                    id={`suite-timeout-${index}`}
                    label={t("policy.timeoutMinutes")}
                    value={suite.timeoutMinutes}
                    onChange={(v) => setSuite(index, { timeoutMinutes: v ?? 30 })}
                    disabled={locked}
                  />
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-4">
                  <NumberField
                    id={`suite-window-${index}`}
                    label={t("policy.windowMinutes")}
                    value={suite.windowMinutes}
                    onChange={(v) => setSuite(index, { windowMinutes: v ?? 15 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-min-${index}`}
                    label={t("policy.minRequests")}
                    value={suite.minRequests}
                    onChange={(v) => setSuite(index, { minRequests: v ?? 50 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-errors-${index}`}
                    label={t("policy.errorsMax")}
                    value={suite.errorRateMax}
                    onChange={(v) => setSuite(index, { errorRateMax: v ?? 1 })}
                    disabled={locked}
                  />
                  <NumberField
                    id={`suite-p95-${index}`}
                    label={t("policy.p95Max")}
                    value={suite.p95Ms}
                    onChange={(v) => setSuite(index, { p95Ms: v })}
                    disabled={locked}
                  />
                  <div className="space-y-1 sm:col-span-2">
                    <Label className="text-xs">{t("policy.ifNotEnoughTraffic")}</Label>
                    <Select
                      value={suite.onInconclusive ?? "ask"}
                      onValueChange={(v) => setSuite(index, { onInconclusive: v as OnInconclusive })}
                      disabled={locked}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue>{(v) => t(`inconclusive.${v as OnInconclusive}`)}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {INCONCLUSIVE_OPTIONS.map((value) => (
                          <SelectItem key={value} value={value}>
                            {t(`inconclusive.${value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <p className="text-xs text-muted-foreground sm:col-span-2">
                    {t("policy.noRealTraffic")}
                  </p>
                </div>
              )}
            </li>
          ))}
        </ol>

        {canAdminister && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setDraft((d) => ({ ...d, suites: [...d.suites, newSuite("external", d.suites.length + 1)] }))
              }
              disabled={locked || draft.suites.length >= 10}
            >
              <Plus className="mr-1 h-4 w-4" /> {t("policy.addExternal")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setDraft((d) => ({ ...d, suites: [...d.suites, newSuite("metrics", d.suites.length + 1)] }))
              }
              disabled={locked || draft.suites.length >= 10}
            >
              <Plus className="mr-1 h-4 w-4" /> {t("policy.addMetrics")}
            </Button>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField
            id="policy-soak"
            label={t("policy.soak")}
            value={draft.soakMinutes}
            onChange={(v) => setDraft((d) => ({ ...d, soakMinutes: v ?? 0 }))}
            disabled={locked}
          />
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="policy-approval">{t("policy.approval")}</Label>
            <Switch
              id="policy-approval"
              checked={draft.requireApproval}
              onCheckedChange={(requireApproval) => setDraft((d) => ({ ...d, requireApproval }))}
              disabled={locked}
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="policy-auto">{t("policy.auto")}</Label>
            <Switch
              id="policy-auto"
              checked={draft.autoPromote}
              onCheckedChange={(autoPromote) => setDraft((d) => ({ ...d, autoPromote }))}
              disabled={locked}
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{t("policy.adminNote")}</p>

        {canAdminister && (
          <Button onClick={save} disabled={saving}>
            <Save className="mr-2 h-4 w-4" />
            {t("policy.save")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
