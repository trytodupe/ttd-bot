import type { InboundEvent, NormalizedSegment, OwnerChatKey } from "./types.ts";
import { AccessController } from "./access.ts";
import { Store } from "./store.ts";

export interface OneBotAction {
  action: string;
  params?: Record<string, unknown>;
  echo?: unknown;
}

export function stripTestPrefix(segments: NormalizedSegment[]): NormalizedSegment[] {
  let pending = true;
  return segments.map((segment) => {
    if (!pending || segment.type !== "text") return segment;
    const text = String(segment.data.text ?? "");
    const stripped = text.replace(/^\s*\/test(?:\s+|$)/i, "");
    if (stripped !== text) pending = false;
    return { ...segment, data: { ...segment.data, text: stripped } };
  });
}

export class StagingProxyPolicy {
  constructor(private readonly store: Store, private readonly access: AccessController) {}

  acceptsEvent(owner: OwnerChatKey, sessionRef: string, event: InboundEvent): boolean {
    if (event.owner !== owner || !this.access.inbound(event)) return false;
    if (event.route_hint !== "staging") return false;
    const session = this.store.getSession(owner, sessionRef);
    if (!session?.stagingReleaseId) return false;
    return this.store.stagingRelease(owner, sessionRef, session.stagingReleaseId)?.state === "healthy";
  }

  allowAction(owner: OwnerChatKey, action: OneBotAction): boolean {
    const params = action.params ?? {};
    if ("group_id" in params) {
      const groupId = String(params.group_id);
      return this.access.outbound("group", groupId);
    }
    if ("user_id" in params) {
      const userId = String(params.user_id);
      return this.access.outbound("user", userId);
    }
    return true;
  }
}
