import type { Mode, Model, Profile } from "../runtime/protocolTypes";
import type { RuntimeState } from "../runtime/appServerProcess";
export interface TranscriptItem { kind: "user" | "assistant" | "activity" | "error"; text: string; }
export interface CheckpointFile { path: string; before: { content: string; hash: string } | null; after: { content: string; hash: string } | null; }
export interface Checkpoint { checkpoint_id: string; created_at: string; files: CheckpointFile[]; }
export type RequestState = "idle" | "processing" | "cancelling";
export interface SessionState { runtime: RuntimeState; sessionId?: string; taskId?: string; runId?: string; workspaceName?: string; profile?: string; model?: string; mode: Mode; profiles: Profile[]; models: Model[]; transcript: TranscriptItem[]; checkpoints: Checkpoint[]; starting: boolean; cancelling: boolean; requestState: RequestState; progress?: string; activityVersion: number; }
export const initialState = (mode: Mode): SessionState => ({ runtime: "stopped", mode, profiles: [], models: [], transcript: [], checkpoints: [], starting: false, cancelling: false, requestState: "idle", activityVersion: 0 });
