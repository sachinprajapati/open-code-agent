import type { AppServerProcess } from "../runtime/appServerProcess";
import type { ApprovalRequest, Json } from "../runtime/protocolTypes";
export class ApprovalController {
  private pending = new Map<string, ApprovalRequest>();
  constructor(private readonly runtime: AppServerProcess, private readonly publish: (approval: ApprovalRequest) => void, private readonly status: (message: string) => void) {}
  request(approval: ApprovalRequest): void { if (!approval.approval_id || this.pending.has(approval.approval_id)) return; this.pending.set(approval.approval_id, approval); this.publish(approval); }
  decide(id: string, fingerprint: string, approved: boolean): boolean {
    const approval = this.pending.get(id); if (!approval || approval.fingerprint !== fingerprint) { this.status("The approval request is no longer active."); return false; }
    this.pending.delete(id); const response: Json = { approval_id: id, fingerprint, decision: approved ? "approved_once" : "denied" }; this.runtime.respondApproval(id, response); return true;
  }
  failClosed(): void { for (const approval of this.pending.values()) this.runtime.respondApproval(approval.approval_id, { approval_id: approval.approval_id, fingerprint: approval.fingerprint, decision: "denied" }); this.pending.clear(); }
}
