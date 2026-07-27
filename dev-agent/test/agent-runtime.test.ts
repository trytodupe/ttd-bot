import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Agent, AgentEvent, AgentMessage } from "@earendil-works/pi-agent-core";
import { AgentRuntimeManager, PiSession } from "../src/agent-runtime.ts";
import { loadConfig } from "../src/config.ts";
import { Store } from "../src/store.ts";
import type { InboundEvent, SessionRecord, SlotRecord } from "../src/types.ts";

class FakeAgent {
  listener: ((event: AgentEvent, signal: AbortSignal) => Promise<void> | void) | undefined;
  state = { isStreaming: false, messages: [] as AgentMessage[] };
  getApiKey: Agent["getApiKey"];

  subscribe(listener: (event: AgentEvent, signal: AbortSignal) => Promise<void> | void): () => void {
    this.listener = listener;
    return () => { this.listener = undefined; };
  }

  async emit(event: AgentEvent): Promise<void> {
    await this.listener?.(event, new AbortController().signal);
  }

  async prompt(): Promise<void> {}
  steer(): void {}
  abort(): void {}
  clearAllQueues(): void {}
  async waitForIdle(): Promise<void> {}
}

function assistant(text: string, stopReason: "stop" | "error" | "aborted" = "stop"): AgentMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    stopReason,
    timestamp: Date.now(),
  } as AgentMessage;
}

function contact(owner: "private:1001"): InboundEvent {
  return {
    event_id: `${owner}:contact`,
    owner,
    chat_type: "private",
    user_id: "1001",
    group_id: null,
    message_id: "contact",
    bot_id: "bot",
    is_superuser: false,
    route_hint: "dev",
    text: "/dev test",
    segments: [{ type: "text", data: { text: "/dev test" } }],
    quote: null,
    attachment_rejections: [],
    timestamp: 1,
  };
}

test("successful final text is queued only after transcript and workspace checkpoints", async () => {
  const root = await mkdtemp(join(tmpdir(), "ttd-agent-runtime-"));
  const store = new Store();
  const owner = "private:1001" as const;
  store.ensureOwner(owner, process.getuid?.() ?? 0);
  store.recordInbound(contact(owner));
  const session: SessionRecord = {
    owner,
    sessionRef: "runtime-ref",
    taskId: "runtime-task",
    title: "runtime",
    state: "active",
    branch: "agent/runtime-task-runtime",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/runtime-ref",
    transcriptPath: join(root, "transcript.json"),
    slotId: 0,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  const slot: SlotRecord = { id: 0, owner, sessionRef: session.sessionRef, health: "healthy" };
  store.insertSession(session);
  const fakeAgent = new FakeAgent();
  let workspaceCheckpointed = false;
  const guest = {
    checkpointWorkspace: async () => { workspaceCheckpointed = true; },
  };
  const originalEnqueue = store.enqueue.bind(store);
  store.enqueue = ((message) => {
    assert.equal(workspaceCheckpointed, true);
    return originalEnqueue(message);
  }) as Store["enqueue"];
  const runtime = new PiSession(
    session,
    slot,
    store,
    loadConfig({ TTD_DEV_STATE_ROOT: root, TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e" }),
    guest as never,
    [],
    fakeAgent as unknown as Agent,
  );

  await fakeAgent.emit({ type: "agent_start" });
  const final = assistant("Implemented the change and all checks pass.");
  fakeAgent.state.messages.push(final);
  await fakeAgent.emit({ type: "agent_end", messages: [final] });

  assert.equal(JSON.parse(await readFile(session.transcriptPath, "utf8")).length, 1);
  const outbox = store.pollOutbox("bot", 10);
  assert.equal(outbox.length, 1);
  assert.equal(outbox[0]?.message, "Implemented the change and all checks pass.");
  void runtime;
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("error, abort, and already-notified final text are not sent as success summaries", async () => {
  const root = await mkdtemp(join(tmpdir(), "ttd-agent-runtime-"));
  const store = new Store();
  const owner = "private:1001" as const;
  store.ensureOwner(owner, process.getuid?.() ?? 0);
  store.recordInbound(contact(owner));
  const session: SessionRecord = {
    owner,
    sessionRef: "runtime-ref",
    taskId: "runtime-task",
    title: "runtime",
    state: "active",
    branch: "agent/runtime-task-runtime",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/runtime-ref",
    transcriptPath: join(root, "transcript.json"),
    slotId: 0,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  const slot = { id: 0 } as SlotRecord;
  store.insertSession(session);
  const fakeAgent = new FakeAgent();
  const runtime = new PiSession(
    session,
    slot,
    store,
    loadConfig({ TTD_DEV_STATE_ROOT: root, TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e" }),
    { checkpointWorkspace: async () => undefined } as never,
    [],
    fakeAgent as unknown as Agent,
  );

  await fakeAgent.emit({ type: "agent_start" });
  await fakeAgent.emit({ type: "agent_end", messages: [assistant("failed", "error")] });
  await fakeAgent.emit({ type: "agent_start" });
  await fakeAgent.emit({ type: "agent_end", messages: [assistant("stopped", "aborted")] });
  await fakeAgent.emit({ type: "agent_start" });
  (runtime as unknown as { notify(kind: string, text: string): void }).notify("test", "same summary");
  await fakeAgent.emit({ type: "agent_end", messages: [assistant("same summary")] });

  const outbox = store.pollOutbox("bot", 10);
  assert.deepEqual(outbox.map((item) => item.message), ["same summary"]);
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("failed session open cleans up without checkpointing an incomplete workspace", async () => {
  const session = {
    owner: "private:1001",
    sessionRef: "session",
    taskId: "task",
    title: "test",
    state: "active",
    branch: "agent/task-test",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/session",
    transcriptPath: "/transcripts/session.json",
    slotId: 0,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  } satisfies SessionRecord;
  const slot = { id: 0 } as SlotRecord;
  const suspended: boolean[] = [];
  const guest = {
    prepareWorkspace: async () => { throw new Error("incomplete checkout"); },
    suspend: async (_session: SessionRecord, checkpoint: boolean) => { suspended.push(checkpoint); },
  };
  const manager = new AgentRuntimeManager(
    {} as never,
    loadConfig({ TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e" }),
    guest as never,
    {} as never,
    {} as never,
  );

  await assert.rejects(manager.open(session, slot), /incomplete checkout/);
  assert.deepEqual(suspended, [false]);
});
