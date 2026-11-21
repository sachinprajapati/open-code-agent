export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };
export type RpcId = string | number;
export interface Profile { profile_id: string; display_name: string; base_url: string; default_model: string; authentication_configured: boolean; is_default: boolean; hasApiKey?: boolean; }
export interface Model { id: string; display_name: string; }
export type Mode = "plan" | "ask" | "auto-edit";
export interface AgentEvent { session_id: string; run_id: string; sequence: number; type: string; payload: JsonObject; timestamp: string; }
export interface ApprovalRequest { approval_id: string; session_id: string; run_id: string; proposal_type: "patch" | "command"; fingerprint: string; proposal: JsonObject; classification?: JsonObject; }
export interface InitializeResult { server_name: string; server_version: string; selected_protocol_version: string; capabilities: Record<string, boolean>; }
