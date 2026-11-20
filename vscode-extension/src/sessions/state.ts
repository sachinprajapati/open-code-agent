import type { Mode, Model, Profile } from "../runtime/protocolTypes";
import type { RuntimeState } from "../runtime/appServerProcess";
export interface TranscriptItem { kind: "user" | "assistant" | "activity" | "error"; text: string; }
export type RequestState = "idle" | "processing" | "cancelling";
export interface SessionState { runtime: RuntimeState; sessionId?: string; runId?: string; workspaceName?: string; profile?: string; model?: string; mode: Mode; profiles: Profile[]; models: Model[]; transcript: TranscriptItem[]; starting: boolean; cancelling: boolean; requestState: RequestState; progress?: string; activityVersion: number; }
export const initialState = (mode: Mode): SessionState => ({ runtime: "stopped", mode, profiles: [], models: [], transcript: [], starting: false, cancelling: false, requestState: "idle", activityVersion: 0 });
