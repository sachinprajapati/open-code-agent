import { EventEmitter } from "node:events";
import type { Writable } from "node:stream";
import { ProtocolError, RpcError } from "./errors";
import type { Json, JsonObject, RpcId } from "./protocolTypes";

type Envelope = { jsonrpc: "2.0"; id?: RpcId | null; method?: string; params?: JsonObject; result?: Json; error?: { code: number; message: string } };
type RequestHandler = (params: JsonObject, id: RpcId) => Promise<Json | undefined>;
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

export class JsonRpcClient extends EventEmitter {
  private readonly pending = new Map<RpcId, { resolve: (value: Json) => void; reject: (reason: Error) => void; timer: NodeJS.Timeout }>();
  private readonly requestHandlers = new Map<string, RequestHandler>();
  private readonly buffer: string[] = [];
  private nextId = 1;
  private closed = false;
  private writing: Promise<void> = Promise.resolve();
  constructor(private readonly input: Writable, private readonly timeoutMs = 30_000, private readonly onLog: (message: string) => void = () => undefined) { super(); }
  registerRequestHandler(method: string, handler: RequestHandler): void { this.requestHandlers.set(method, handler); }
  request(method: string, params: JsonObject = {}, timeoutMs = this.timeoutMs): Promise<Json> {
    if (this.closed) return Promise.reject(new ProtocolError("RPC connection is closed."));
    if (this.pending.size >= 32) return Promise.reject(new ProtocolError("Too many pending RPC requests."));
    const id = `client:${this.nextId++}`;
    return new Promise<Json>((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new ProtocolError(`RPC request timed out: ${method}`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      void this.write({ jsonrpc: "2.0", id, method, params }).catch(reject);
    });
  }
  notify(method: string, params: JsonObject = {}): Promise<void> { return this.write({ jsonrpc: "2.0", method, params }); }
  accept(chunk: Buffer | string): void {
    if (this.closed) return;
    this.buffer.push(chunk.toString());
    const joined = this.buffer.join(""); const lines = joined.split("\n"); this.buffer.length = 0; this.buffer.push(lines.pop() ?? "");
    for (const raw of lines) { if (raw.trim()) void this.receive(raw.endsWith("\r") ? raw.slice(0, -1) : raw); }
  }
  disconnect(reason = "RPC connection closed."): void {
    if (this.closed) return; this.closed = true;
    for (const [id, pending] of this.pending) { clearTimeout(pending.timer); pending.reject(new ProtocolError(reason)); this.pending.delete(id); }
  }
  private async receive(raw: string): Promise<void> {
    let decoded: unknown; try { decoded = JSON.parse(raw); } catch { this.onLog("Ignored malformed JSON-RPC line from server."); return; }
    if (!this.validEnvelope(decoded)) { this.onLog("Ignored invalid JSON-RPC envelope from server."); return; }
    const message = decoded as Envelope;
    if (message.method !== undefined) {
      if (message.id !== undefined && message.id !== null) await this.handleRequest(message);
      else this.emit("notification", message.method, message.params ?? {});
      return;
    }
    if (message.id === undefined || message.id === null) { this.onLog("Ignored response without id."); return; }
    const id = message.id; const pending = this.pending.get(id); if (!pending) { this.onLog(`Ignored response for unknown id ${String(id)}.`); return; }
    this.pending.delete(id); clearTimeout(pending.timer);
    if (message.error) pending.reject(new RpcError(message.error.code, message.error.message)); else pending.resolve(message.result as Json);
  }
  private async handleRequest(message: Envelope): Promise<void> {
    const handler = this.requestHandlers.get(message.method!);
    if (!handler) { await this.write({ jsonrpc: "2.0", id: message.id!, error: { code: -32601, message: "Method not found." } }); return; }
    try { const result = await handler(message.params ?? {}, message.id!); await this.write({ jsonrpc: "2.0", id: message.id!, result: result ?? null }); }
    catch { await this.write({ jsonrpc: "2.0", id: message.id!, error: { code: -32603, message: "Client request failed." } }); }
  }
  private validEnvelope(value: unknown): boolean {
    if (!object(value) || value.jsonrpc !== "2.0") return false;
    const hasMethod = typeof value.method === "string" && value.method.trim() !== "";
    const hasResult = Object.hasOwn(value, "result"); const hasError = object(value.error) && typeof value.error.code === "number" && typeof value.error.message === "string";
    if (hasMethod) return !(hasResult || hasError) && (value.id === undefined || typeof value.id === "string" || (Number.isInteger(value.id) && (value.id as number) >= 0));
    return value.id !== undefined && (hasResult !== hasError);
  }
  private write(message: Envelope): Promise<void> {
    const line = `${JSON.stringify(message)}\n`;
    this.writing = this.writing.then(() => new Promise<void>((resolve, reject) => this.input.write(line, "utf8", (error) => error ? reject(error) : resolve())));
    return this.writing;
  }
}
