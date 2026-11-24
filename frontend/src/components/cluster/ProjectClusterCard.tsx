"use client";

import { Panel } from "@/components/app-overview/shared";
import { StatTile } from "@/components/data/StatTile";
import { CLOUD_META } from "@/components/finops/format";
import { useObsFormat } from "@/components/observability/shared";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { CloudStateBadge } from "@/components/ui/cloud-state-badge";
import { useTranslations } from "next-intl";
import { memoryLabel, useProjectFootprint } from "./Footprint";
import { MovePopover } from "./MovePopover";
import { useClusterStates } from "./useClusterStates";

const SUB: Record<string, string> = { onprem: "K3s · hub", aws: "EKS", gcp: "GKE" };

/** Where the project runs: what it reserves next to what it uses now, with the two-click move. */
export function ProjectClusterCard() {
  const t = useTranslations("projectHome.where");
  const tState = useTranslations("placement.state");
  const { number, percent } = useObsFormat();
  const { projectName, targetCloud, can, move, setMove } = useProjectContext();
  const { states, memory } = useClusterStates();
  const rows = useProjectFootprint(projectName, targetCloud);
  const loading = rows === null;
  if (!targetCloud) return null;
  const state = states?.[targetCloud] ?? "unknown";
  const measuredRows = rows ?? [];
  const cpu = measuredRows.reduce((s, r) => s + r.cpu, 0);
  const mem = measuredRows.reduce((s, r) => s + r.memory_bytes, 0);
  const measured = measuredRows.filter((r) => r.usage);
  const usedCpu = measured.reduce((s, r) => s + (r.usage?.cpu_cores ?? 0), 0);
  const usedMem = measured.reduce((s, r) => s + (r.usage?.mem_bytes ?? 0), 0);
  const stateLabel = tState.has(state) ? tState(state) : state;
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {t("title")}
          <CloudStateBadge state={state}>
            {t("state", { cloud: CLOUD_META[targetCloud].label, state: stateLabel })}
          </CloudStateBadge>
        </span>
      }
      description={SUB[targetCloud]}
      action={
        !move && can("project.move") ? (
          <MovePopover project={projectName} cloud={targetCloud} states={states} memory={memory} onStarted={setMove} />
        ) : undefined
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <StatTile
          label={t("reserved")}
          loading={loading}
          value={measuredRows.length ? `${number(cpu, 2)} CPU · ${memoryLabel(mem)}` : null}
          hint={measuredRows.length ? t("reservedHint") : t("none")}
          className="[&>p:nth-child(2)]:text-base"
        />
        <StatTile
          label={t("using")}
          loading={loading}
          value={measured.length ? `${number(usedCpu, 2)} CPU · ${memoryLabel(usedMem)}` : null}
          hint={
            measured.length && cpu && mem
              ? t("usingHint", { cpu: percent(usedCpu / cpu, 0), mem: percent(usedMem / mem, 0) })
              : t("usingNone")
          }
          className="[&>p:nth-child(2)]:text-base"
        />
      </div>
    </Panel>
  );
}
