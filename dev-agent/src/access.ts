import type { InboundEvent } from "./types.ts";
import { Store } from "./store.ts";

export class AccessController {
  constructor(private readonly store: Store) {}

  inbound(event: InboundEvent): boolean {
    const user = this.store.accessDecision("inbound", "user", event.user_id);
    if (user === "deny") return false;
    if (event.chat_type === "private") return user === "allow";
    if (!event.group_id) return false;
    const group = this.store.accessDecision("inbound", "group", event.group_id);
    if (group === "deny") return false;
    return group === "allow";
  }

  outbound(type: "user" | "group", id: string): boolean {
    const decision = this.store.accessDecision("outbound", type, id);
    if (decision === "deny") return false;
    if (decision === "allow") return true;
    return this.store.countAccessAllows("outbound", type) === 0;
  }
}
