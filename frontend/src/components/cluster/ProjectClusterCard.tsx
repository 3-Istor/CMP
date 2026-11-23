"use client";

import { useProjectContext } from "@/components/projects/ProjectContext";
import type { DeploymentStatus } from "@/types";
import { AppChip, appKind } from "./AppChip";
import { ClusterCard } from "./ClusterCard";
import { FootprintLine, FootprintTotal, useProjectFootprint } from "./Footprint";
import { MovePopover } from "./MovePopover";
import { ProjectRow } from "./ProjectRow";
import { useClusterStates } from "./useClusterStates";

const HEALTH: Partial<Record<DeploymentStatus, string>> = { running: "up", degraded: "degraded", failed: "down" };

/** The project's cluster as one card, with the two-click move. */
export function ProjectClusterCard() {
  const { projectName, targetCloud, apps, can, move, setMove } = useProjectContext();
  const { states, memory } = useClusterStates();
  const rows = useProjectFootprint(projectName, targetCloud);
  if (!targetCloud) return null;
  const state = states?.[targetCloud] ?? "unknown";
  return (
    <ClusterCard cloud={targetCloud} state={state} memoryPct={memory[targetCloud]} className="mb-6 max-w-xl">
      <ProjectRow
        name={projectName}
        moving={!!move}
        total={<FootprintTotal rows={rows} up={state === "up"} />}
        apps={apps
          .filter((a) => a.status !== "deleted")
          .map((a) => (
            <AppChip key={a.id} name={a.name} kind={appKind(a.name, a.template_id, a.template_category)} state={HEALTH[a.status] ?? "unknown"}
              footprint={rows.find((r) => r.app === a.name) && <FootprintLine row={rows.find((r) => r.app === a.name)!} />}
            />
          ))}
        action={
          !move && can("project.move") ? (
            <MovePopover project={projectName} cloud={targetCloud} states={states} memory={memory} onStarted={setMove} />
          ) : undefined
        }
      />
    </ClusterCard>
  );
}
