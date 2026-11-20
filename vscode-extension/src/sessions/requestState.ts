import type { SessionState } from "./state";

/** Small, explicit lifecycle helpers make every terminal path restore an idle UI. */
export function beginRequest(state: SessionState, progress = "Connecting to agent..."): void {
  state.starting = true;
  state.cancelling = false;
  state.requestState = "processing";
  setProgress(state, progress);
}

export function beginCancellation(state: SessionState): void {
  state.cancelling = true;
  state.requestState = "cancelling";
  setProgress(state, "Stopping agent...");
}

export function setProgress(state: SessionState, progress: string): void {
  state.progress = progress;
  state.activityVersion += 1;
}

export function finishRequest(state: SessionState): void {
  state.runId = undefined;
  state.starting = false;
  state.cancelling = false;
  state.requestState = "idle";
  state.progress = undefined;
}
