import type { AgentMessage, ThinkingLevel } from "@earendil-works/pi-agent-core";

export type OwnerChatKey = `private:${string}` | `group:${string}`;
export type ChatType = "private" | "group";
export type Route = "none" | "dev" | "admin" | "staging";
export type SessionState =
  | "active"
  | "suspended"
  | "open_pr"
  | "merged"
  | "closed"
  | "abandoned";

export interface NormalizedSegment {
  type: string;
  data: Record<string, string | number | boolean | null>;
}

export interface NormalizedQuote {
  message_id: string;
  sender_id: string;
  text: string;
  segments: NormalizedSegment[];
  attachment_rejections: string[];
}

export interface InboundEvent {
  event_id: string;
  owner: OwnerChatKey;
  chat_type: ChatType;
  user_id: string;
  group_id: string | null;
  message_id: string;
  bot_id: string;
  is_superuser: boolean;
  route_hint: "dev" | "admin" | "staging" | "none";
  text: string;
  segments: NormalizedSegment[];
  quote: NormalizedQuote | null;
  attachment_rejections: string[];
  timestamp: number;
}

export interface SessionRecord {
  owner: OwnerChatKey;
  sessionRef: string;
  taskId: string;
  title: string;
  state: SessionState;
  branch: string;
  baseSha: string;
  workspace: string;
  transcriptPath: string;
  slotId: number | null;
  stagingReleaseId: string | null;
  continuationOf: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface SlotRecord {
  id: number;
  owner: OwnerChatKey | null;
  sessionRef: string | null;
  health: "idle" | "starting" | "healthy" | "degraded";
}

export interface ModelSettings {
  provider: string;
  model: string;
  thinkingLevel: ThinkingLevel;
}

export interface RuntimeSession {
  isBusy(): boolean;
  waitForIdle(): Promise<void>;
  prompt(text: string, images?: Array<{ type: "image"; data: string; mimeType: string }>): Promise<void>;
  steer(text: string, images?: Array<{ type: "image"; data: string; mimeType: string }>): void;
  stop(): void;
  compact(): Promise<void>;
  checkpoint(): Promise<void>;
  messages(): AgentMessage[];
}

export interface RuntimeManager {
  open(session: SessionRecord, slot: SlotRecord, seedSession?: SessionRecord): Promise<RuntimeSession>;
  get(owner: OwnerChatKey, sessionRef: string): RuntimeSession | undefined;
  suspend(owner: OwnerChatKey, sessionRef: string): Promise<void>;
  activate(session: SessionRecord, slot: SlotRecord): Promise<{ releaseId: string }>;
  deactivate(session: SessionRecord): Promise<void>;
  stage(session: SessionRecord, event: InboundEvent): Promise<void>;
  syncPolicies(): Promise<void>;
  publish(session: SessionRecord): Promise<{ url: string; number: number; state: "open" }>;
  shutdown(): Promise<void>;
}

export interface OutboxMessage {
  owner: OwnerChatKey;
  sessionRef?: string;
  botId: string;
  chatType: ChatType;
  destinationId: string;
  message: string | NormalizedSegment[];
  origin: "agent" | "staging" | "admin" | "system";
}
