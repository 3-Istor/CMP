/**
 * The user's edits of an import plan, kept in the shape the apply endpoint
 * takes (backend app/routers/imports.py PlanEdits): exclude and rename by
 * plan name, the rest by final app name, drop_links by plan names. Pure
 * functions, tested in tests/import-plan.test.mjs.
 */

import type {
  ImportCapacity,
  ImportPlan,
  ImportPlanEdits,
  ImportRun,
  ImportServiceView,
  PlanPort,
  PlanProbe,
} from "@/types";

const APP_NAME = /^[a-z]([-a-z0-9]*[a-z0-9])?$/;
const MAX_APP_NAME = 40;
const PROBE_PATH = /^\/[A-Za-z0-9._~/%-]{0,199}$/;
const MIN_MEMORY_MIB = 16;
const SUPPORTED_BACKING = ["mongodb", "redis", "neo4j", "elasticsearch"];

export type PlanEditAction =
  | { type: "rename"; app: string; name: string }
  | { type: "toggleExclude"; app: string }
  | { type: "setPublic"; app: string; value: boolean }
  | { type: "setMemory"; app: string; requests: number; limits: number }
  | { type: "setPort"; app: string; port: number }
  | { type: "setProbe"; app: string; probe: PlanProbe }
  | { type: "toggleLink"; from: string; to: string }
  | { type: "reset"; edits?: ImportPlanEdits };

export function emptyEdits(): ImportPlanEdits {
  return {
    exclude: [],
    rename: {},
    public: {},
    memory: {},
    port: {},
    probe: {},
    drop_links: [],
  };
}

export function finalName(edits: ImportPlanEdits, planName: string): string {
  return edits.rename[planName] || planName;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

function moveKey<T>(
  record: Record<string, T>,
  from: string,
  to: string,
): Record<string, T> {
  if (!(from in record) || from === to) return record;
  const next = without(record, from);
  next[to] = record[from];
  return next;
}

/** What a service runs with once the edits apply, by plan name. */
export interface EffectiveService {
  planName: string;
  name: string;
  ports: PlanPort[];
  probe: PlanProbe | null;
  public: boolean;
  requests: number;
  limits: number;
  excluded: boolean;
}

export function effectiveService(
  plan: ImportPlan,
  edits: ImportPlanEdits,
  planName: string,
): EffectiveService | null {
  const service = plan.services.find((s) => s.name === planName);
  if (!service) return null;
  const name = finalName(edits, planName);
  const ports = service.ports.map((p) => ({ ...p }));
  let probe = service.probes ? { ...service.probes } : null;
  const port = edits.port[name];
  if (port !== undefined && ports.length > 0) {
    const old = ports[0].port;
    ports[0] = { ...ports[0], port };
    if (probe && probe.port === old) probe = { ...probe, port };
  }
  if (edits.probe[name]) probe = { ...edits.probe[name] };
  const memory = edits.memory[name];
  return {
    planName,
    name,
    ports,
    probe,
    public: edits.public[name] ?? service.public,
    requests: memory?.requests_mem_mib ?? service.resources.requests_mem_mib,
    limits: memory?.limits_mem_mib ?? service.resources.limits_mem_mib,
    excluded: edits.exclude.includes(planName),
  };
}

export function planEditsReducer(
  state: ImportPlanEdits,
  action: PlanEditAction,
  plan: ImportPlan,
): ImportPlanEdits {
  switch (action.type) {
    case "reset":
      return action.edits ?? emptyEdits();
    case "rename": {
      const before = finalName(state, action.app);
      const name = action.name.trim();
      const after = name || action.app;
      const rename =
        after === action.app
          ? without(state.rename, action.app)
          : { ...state.rename, [action.app]: after };
      return {
        ...state,
        rename,
        public: moveKey(state.public, before, after),
        memory: moveKey(state.memory, before, after),
        port: moveKey(state.port, before, after),
        probe: moveKey(state.probe, before, after),
      };
    }
    case "toggleExclude":
      return {
        ...state,
        exclude: state.exclude.includes(action.app)
          ? state.exclude.filter((a) => a !== action.app)
          : [...state.exclude, action.app],
      };
    case "setPublic": {
      const service = plan.services.find((s) => s.name === action.app);
      const name = finalName(state, action.app);
      return {
        ...state,
        public:
          service && service.public === action.value
            ? without(state.public, name)
            : { ...state.public, [name]: action.value },
      };
    }
    case "setMemory": {
      const service = plan.services.find((s) => s.name === action.app);
      const name = finalName(state, action.app);
      const unchanged =
        service &&
        service.resources.requests_mem_mib === action.requests &&
        service.resources.limits_mem_mib === action.limits;
      return {
        ...state,
        memory: unchanged
          ? without(state.memory, name)
          : {
              ...state.memory,
              [name]: {
                requests_mem_mib: action.requests,
                limits_mem_mib: action.limits,
              },
            },
      };
    }
    case "setPort": {
      const service = plan.services.find((s) => s.name === action.app);
      const name = finalName(state, action.app);
      const before = effectiveService(plan, state, action.app);
      const original = service?.ports[0]?.port;
      const port =
        original === action.port
          ? without(state.port, name)
          : { ...state.port, [name]: action.port };
      // An edited probe on the old port follows the port, as the plan's does.
      const edited = state.probe[name];
      const probe =
        edited && before && edited.port === before.ports[0]?.port
          ? { ...state.probe, [name]: { ...edited, port: action.port } }
          : state.probe;
      return { ...state, port, probe };
    }
    case "setProbe": {
      const name = finalName(state, action.app);
      const probe: PlanProbe = {
        kind: action.probe.kind,
        path: action.probe.kind === "http" ? action.probe.path : null,
        port: action.probe.port,
      };
      return { ...state, probe: { ...state.probe, [name]: probe } };
    }
    case "toggleLink": {
      const dropped = state.drop_links.some(
        (l) => l.from === action.from && l.to === action.to,
      );
      return {
        ...state,
        drop_links: dropped
          ? state.drop_links.filter(
              (l) => !(l.from === action.from && l.to === action.to),
            )
          : [...state.drop_links, { from: action.from, to: action.to }],
      };
    }
  }
}

export type PlanProblem =
  | { code: "unsupported"; app: string }
  | { code: "name"; app: string }
  | { code: "duplicate"; app: string }
  | { code: "memory"; app: string }
  | { code: "probePath"; app: string }
  | { code: "nothing" };

/** What the apply endpoint would refuse, found before the step-up. */
export function planProblems(
  plan: ImportPlan,
  edits: ImportPlanEdits,
  taken: string[] = [],
): PlanProblem[] {
  const problems: PlanProblem[] = [];
  const names: string[] = [];
  for (const backing of plan.backing) {
    if (edits.exclude.includes(backing.name)) continue;
    if (!backing.supported || !SUPPORTED_BACKING.includes(backing.kind)) {
      problems.push({ code: "unsupported", app: backing.name });
      continue;
    }
    names.push(finalName(edits, backing.name));
  }
  for (const service of plan.services) {
    const effective = effectiveService(plan, edits, service.name);
    if (!effective || effective.excluded) continue;
    names.push(effective.name);
    if (
      effective.requests < MIN_MEMORY_MIB ||
      effective.requests > effective.limits
    ) {
      problems.push({ code: "memory", app: effective.name });
    }
    if (
      effective.probe?.kind === "http" &&
      !PROBE_PATH.test(effective.probe.path ?? "")
    ) {
      problems.push({ code: "probePath", app: effective.name });
    }
  }
  for (const name of new Set(names)) {
    if (name.length > MAX_APP_NAME || !APP_NAME.test(name)) {
      problems.push({ code: "name", app: name });
    }
    if (names.filter((n) => n === name).length > 1 || taken.includes(name)) {
      problems.push({ code: "duplicate", app: name });
    }
  }
  if (names.length === 0) problems.push({ code: "nothing" });
  return problems;
}

export interface CapacityGauge {
  requestedMiB: number;
  otherMiB: number;
  maxMiB: number;
  over: boolean;
  /** Apps requesting more than one namespace may hold. */
  tooBig: string[];
}

/** Memory requests of the plan as it will deploy, against the project cap. */
export function capacityGauge(
  plan: ImportPlan,
  edits: ImportPlanEdits,
  capacity: ImportCapacity,
): CapacityGauge {
  let requested = 0;
  const tooBig: string[] = [];
  for (const backing of plan.backing) {
    if (edits.exclude.includes(backing.name) || !backing.supported) continue;
    const mib =
      capacity.backing_requests_mib[backing.kind] ??
      backing.resources.requests_mem_mib;
    requested += mib;
    if (mib > capacity.namespace_max_mib) tooBig.push(finalName(edits, backing.name));
  }
  for (const service of plan.services) {
    const effective = effectiveService(plan, edits, service.name);
    if (!effective || effective.excluded) continue;
    requested += effective.requests;
    if (effective.requests > capacity.namespace_max_mib) tooBig.push(effective.name);
  }
  return {
    requestedMiB: requested,
    otherMiB: capacity.other_imports_mib,
    maxMiB: capacity.project_max_mib,
    over: requested + capacity.other_imports_mib > capacity.project_max_mib,
    tooBig,
  };
}

export type StageState = "pending" | "active" | "done" | "failed" | "skipped";
export const STAGES = ["build", "scan", "publish", "deploy", "smoke"] as const;
export type Stage = (typeof STAGES)[number];

const BUILD_FAILURES: Record<string, Stage> = {
  claim: "build",
  fetch: "build",
  analyze: "build",
  dispatch: "build",
  unknown: "build",
  build: "build",
  scan: "scan",
  publish: "publish",
  deploy: "deploy",
};

/**
 * Where one app of a build run stands. The workflow reports the stage only
 * when it fails; images in the report mean build, scan and publish passed;
 * Argo CD states and the smoke test come from the deploy loop.
 */
export function serviceStages(
  run: Pick<ImportRun, "kind" | "status" | "report">,
  service: Pick<ImportServiceView, "app" | "kind" | "status" | "public">,
): Record<Stage, StageState> {
  const stages: Record<Stage, StageState> = {
    build: "pending",
    scan: "pending",
    publish: "pending",
    deploy: "pending",
    smoke: service.public ? "pending" : "skipped",
  };
  const report = run.report ?? {};
  const built = service.kind === "backing" || Boolean(report.images?.[service.app]);
  if (service.kind === "backing") {
    stages.build = stages.scan = stages.publish = "skipped";
  }
  if (run.kind !== "build") return stages;

  if (!built) {
    if (run.status === "failed") {
      const failed = BUILD_FAILURES[report.stage ?? "unknown"] ?? "build";
      for (const stage of ["build", "scan", "publish"] as const) {
        if (stage === failed) {
          stages[stage] = "failed";
          break;
        }
        stages[stage] = "done";
      }
    } else if (run.status === "claimed") {
      stages.build = "active";
    }
    return stages;
  }

  if (service.kind === "service") {
    stages.build = stages.scan = stages.publish = "done";
  }
  const state = report.states?.[service.app];
  if (state === undefined) {
    if (run.status === "failed" && report.stage === "deploy") {
      stages.deploy = "failed";
    } else if (run.status === "running") {
      stages.deploy = "active";
    }
    return stages;
  }
  if (state === "synced" || state === "unreachable") {
    stages.deploy = "done";
    if (service.public) stages.smoke = state === "synced" ? "done" : "failed";
  } else {
    stages.deploy = "failed";
  }
  return stages;
}
