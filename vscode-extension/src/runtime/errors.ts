export class RpcError extends Error {
  constructor(readonly code: number, message: string) { super(message); this.name = "RpcError"; }
}
export class ProtocolError extends Error { constructor(message: string) { super(message); this.name = "ProtocolError"; } }
