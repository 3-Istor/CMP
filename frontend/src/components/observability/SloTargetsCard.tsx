"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getSloTargets, updateSloTargets } from "@/lib/api";
import { useCardData } from "@/lib/useCardData";
import type { SloSettings } from "@/types/observability";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { toast } from "sonner";
import { CardState, Section } from "./shared";

function valid(availability: number, latency: number): boolean {
  return (
    availability >= 90 && availability <= 99.99 && latency >= 50 && latency <= 99.9
  );
}

/** Settings > SLO: owners and admins set the targets, members read them. */
export function SloTargetsCard({ project, app }: { project: string; app: string }) {
  const t = useTranslations("obs.slo");
  const card = useCardData(`slo-targets-${project}-${app}`, () => getSloTargets(project, app));
  return (
    <Section
      title={t("settingsTitle")}
      description={card.data ? t("settingsDescription", { days: card.data.period_days }) : undefined}
    >
      <CardState card={card} signal="slo" height="h-28">
        {(data) => (
          <TargetsForm
            key={`${data.availability}-${data.latency}`}
            settings={data}
            onSaved={card.retry}
          />
        )}
      </CardState>
    </Section>
  );
}

function TargetsForm({
  settings,
  onSaved,
}: {
  settings: SloSettings;
  onSaved: () => void;
}) {
  const t = useTranslations("obs.slo");
  const ids = { availability: useId(), latency: useId(), hint: useId() };
  const [availability, setAvailability] = useState(String(settings.availability));
  const [latency, setLatency] = useState(String(settings.latency));
  const [saving, setSaving] = useState(false);
  const ok = valid(Number(availability), Number(latency));
  const save = async () => {
    if (!ok) return;
    setSaving(true);
    try {
      await updateSloTargets({
        project: settings.project,
        app: settings.app,
        availability: Number(availability),
        latency: Number(latency),
      });
      toast.success(t("saved"));
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };
  return (
    <form
      className="grid gap-4 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor={ids.availability}>{t("availabilityLabel")}</Label>
        <Input
          id={ids.availability}
          type="number"
          step="0.01"
          min={90}
          max={99.99}
          value={availability}
          readOnly={!settings.can_edit}
          aria-describedby={ids.hint}
          onChange={(e) => setAvailability(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={ids.latency}>
          {t("latencyLabel", { ms: settings.latency_threshold_ms })}
        </Label>
        <Input
          id={ids.latency}
          type="number"
          step="0.1"
          min={50}
          max={99.9}
          value={latency}
          readOnly={!settings.can_edit}
          aria-describedby={ids.hint}
          onChange={(e) => setLatency(e.target.value)}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
        <p
          id={ids.hint}
          className={ok ? "text-xs text-muted-foreground" : "text-xs text-destructive"}
          role={ok ? undefined : "alert"}
        >
          {settings.can_edit ? (ok ? "" : t("invalid")) : t("ownersOnly")}
        </p>
        {settings.can_edit && (
          <Button type="submit" size="sm" disabled={!ok || saving} className="ml-auto">
            {t("save")}
          </Button>
        )}
      </div>
    </form>
  );
}
