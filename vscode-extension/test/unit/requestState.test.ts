import { describe, expect, it } from "vitest";
import { beginCancellation, beginRequest, finishRequest, setProgress } from "../../src/sessions/requestState";
import { initialState } from "../../src/sessions/state";

describe("request state", () => {
  it("transitions from processing through cancellation back to idle", () => {
    const state = initialState("plan");
    beginRequest(state);
    expect(state).toMatchObject({ requestState: "processing", starting: true, progress: "Connecting to agent..." });
    const firstActivity = state.activityVersion;
    setProgress(state, "Running tool: search_code");
    expect(state.activityVersion).toBe(firstActivity + 1);
    beginCancellation(state);
    expect(state).toMatchObject({ requestState: "cancelling", cancelling: true });
    finishRequest(state);
    expect(state).toMatchObject({ requestState: "idle", starting: false, cancelling: false, runId: undefined, progress: undefined });
  });

  it("allows cancellation while the request is still starting", () => {
    const state = initialState("plan");
    beginRequest(state, "Inspecting workspace...");
    beginCancellation(state);
    expect(state).toMatchObject({ requestState: "cancelling", starting: true });
    expect(state.runId).toBeUndefined();
    finishRequest(state);
    expect(state.requestState).toBe("idle");
  });

  it("restores idle state after an error or timeout", () => {
    const state = initialState("ask");
    beginRequest(state, "Inspecting workspace...");
    state.runId = "run-1";
    finishRequest(state);
    expect(state.requestState).toBe("idle");
    expect(state.progress).toBeUndefined();
  });
});
