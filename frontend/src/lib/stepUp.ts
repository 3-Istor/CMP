import { signIn } from "next-auth/react";

/**
 * Step-up before a destructive action (CNP-124, backend app/services/step_up.py).
 *
 * The API answers 401 with `detail.code === "step_up_required"` when the
 * token's login is not a recent passkey one. We log in again with the
 * `acr_values` and `max_age` it gives; Keycloak asks only for the passkey and
 * sends the user back to the same page, where they confirm the action again.
 */

export interface StepUpHint {
  acrValues: string;
  maxAge: number;
}

const STORAGE_KEY = "cnp-step-up";
export const STEP_UP_FAILED_EVENT = "cnp:step-up-failed";
// A second challenge this soon after a step-up means Keycloak could not
// deliver the level (no passkey, flow not deployed): redirecting again would
// loop.
const LOOP_WINDOW_MS = 2 * 60 * 1000;

interface StepUpRecord {
  at: number;
  notified: boolean;
}

export function parseStepUpHint(status: number, body: string): StepUpHint | null {
  if (status !== 401) return null;
  try {
    const detail = JSON.parse(body)?.detail;
    if (
      detail?.code === "step_up_required" &&
      typeof detail.acr_values === "string" &&
      typeof detail.max_age === "number"
    ) {
      return { acrValues: detail.acr_values, maxAge: detail.max_age };
    }
  } catch {
    // not JSON: an ordinary 401
  }
  return null;
}

function readRecord(): StepUpRecord | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StepUpRecord) : null;
  } catch {
    return null;
  }
}

function writeRecord(record: StepUpRecord | null) {
  try {
    if (record) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(record));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked: the loop guard is lost, the step-up still works
  }
}

/**
 * Send the user to Keycloak for the step-up. Returns false, without
 * redirecting, when a step-up was just done and did not satisfy the API.
 */
export async function startStepUp(hint: StepUpHint): Promise<boolean> {
  const previous = readRecord();
  if (previous && Date.now() - previous.at < LOOP_WINDOW_MS && previous.notified) {
    writeRecord(null);
    window.dispatchEvent(new Event(STEP_UP_FAILED_EVENT));
    return false;
  }
  writeRecord({ at: Date.now(), notified: false });
  await signIn(
    "keycloak",
    { callbackUrl: window.location.href },
    { acr_values: hint.acrValues, max_age: String(hint.maxAge) },
  );
  return true;
}

/** True once, on the first page load after coming back from a step-up. */
export function takeStepUpReturn(): boolean {
  const record = readRecord();
  if (!record || record.notified) return false;
  if (Date.now() - record.at > LOOP_WINDOW_MS) {
    writeRecord(null);
    return false;
  }
  writeRecord({ ...record, notified: true });
  return true;
}
