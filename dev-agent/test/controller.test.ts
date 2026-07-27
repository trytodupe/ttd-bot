import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { loadConfig } from "../src/config.ts";
import { Controller } from "../src/controller.ts";
import { Store } from "../src/store.ts";
import type { AgentMessage } from "@earendil-works/pi-agent-core";
import type { InboundEvent, OwnerChatKey, RuntimeManager, RuntimeSession, SessionRecord, SlotRecord } from "../src/types.ts";

class FakeSession implements RuntimeSession {
  transcript: AgentMessage[] = [];
  prompts: string[] = [];
  busy = false;
  isBusy(): boolean { return this.busy; }
  async waitForIdle(): Promise<void> {}
  async prompt(text: string): Promise<void> { this.prompts.push(text); this.transcript.push({ role: "user", content: text, timestamp: Date.now() }); }
  steer(text: string): void { this.prompts.push(text); }
  stop(): void {}
  async compact(): Promise<void> {}
  async checkpoint(): Promise<void> {}
  messages(): AgentMessage[] { return this.transcript; }
}

class FakeRuntime implements RuntimeManager {
  sessions = new Map<string, FakeSession>();
  staged: string[] = [];
  activationError: Error | undefined;
  publicationError: Error | undefined;
  deactivated: string[] = [];
  private key(owner: OwnerChatKey, ref: string) { return `${owner}:${ref}`; }
  async open(session: SessionRecord, _slot: SlotRecord): Promise<RuntimeSession> {
    const runtime = new FakeSession();
    this.sessions.set(this.key(session.owner, session.sessionRef), runtime);
    return runtime;
  }
  get(owner: OwnerChatKey, ref: string): RuntimeSession | undefined { return this.sessions.get(this.key(owner, ref)); }
  async suspend(owner: OwnerChatKey, ref: string): Promise<void> { this.sessions.delete(this.key(owner, ref)); }
  async activate(): Promise<{ releaseId: string }> {
    if (this.activationError) throw this.activationError;
    return { releaseId: "release" };
  }
  async deactivate(session: SessionRecord): Promise<void> { this.deactivated.push(session.sessionRef); }
  async stage(session: SessionRecord): Promise<void> { this.staged.push(session.sessionRef); }
  async syncPolicies(): Promise<void> {}
  async publish(): Promise<{ url: string; number: number; state: "open" }> {
    if (this.publicationError) throw this.publicationError;
    return { url: "https://example.test/pr/1", number: 1, state: "open" };
  }
  async shutdown(): Promise<void> {}
}

function event(owner: OwnerChatKey, text: string, route: InboundEvent["route_hint"], id: number): InboundEvent {
  const [kind, external] = owner.split(":", 2);
  return {
    event_id: `${owner}:${id}`,
    owner,
    chat_type: kind as "private" | "group",
    user_id: kind === "private" ? external! : "3001",
    group_id: kind === "group" ? external! : null,
    message_id: String(id),
    bot_id: "bot",
    is_superuser: false,
    route_hint: route,
    text,
    segments: [{ type: "text", data: { text } }],
    quote: null,
    attachment_rejections: [],
    timestamp: 1,
  };
}

test("plain DMs are untouched while commands fail closed under ACL", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  const controller = new Controller(store, runtime, config);
  const owner = "private:1001" as const;
  assert.deepEqual(await controller.route(event(owner, "hello", "none", 1)), { route: "none" });
  assert.equal((await controller.route(event(owner, "/dev build", "dev", 2))).route, "dev");
  assert.match((await controller.route(event(owner, "/dev build", "dev", 3))).immediate ?? "", /未获/);
  assert.equal(store.listSessions(owner, 1).length, 0);
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("ordinary group traffic is untouched and test restoration is asynchronous", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  const controller = new Controller(store, runtime, config);
  const owner = "group:2001" as const;
  store.setAccessRule("inbound", "group", "2001", "allow");

  await controller.route(event(owner, "/dev build", "dev", 20));
  await controller.waitForIdle();
  const session = store.activeSession(owner);
  assert.ok(session);
  store.updateSession(owner, session.sessionRef, { stagingReleaseId: "release" });

  assert.deepEqual(await controller.route(event(owner, "ordinary", "none", 21)), { route: "none" });
  assert.equal(runtime.staged.length, 0);
  assert.deepEqual(await controller.route(event(owner, "/test hello", "staging", 22)), {
    route: "staging",
    accepted: true,
  });
  await controller.waitForIdle();
  assert.deepEqual(runtime.staged, [session.sessionRef]);
  assert.deepEqual(await controller.route(event(owner, "/test", "staging", 23)), {
    route: "staging",
    accepted: false,
    immediate: "用法：/test <消息>",
  });
  await controller.waitForIdle();
  assert.deepEqual(runtime.staged, [session.sessionRef]);

  store.close();
  await rm(root, { recursive: true, force: true });
});

test("three independent owners run and a fourth receives durable slot rejection", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  const controller = new Controller(store, runtime, config);
  for (let index = 0; index < 4; index += 1) {
    const owner = `group:${2000 + index}` as OwnerChatKey;
    store.setAccessRule("inbound", "group", String(2000 + index), "allow");
    const result = await controller.route(event(owner, `/dev task ${index}`, "dev", 100 + index));
    assert.equal(result.route, "dev");
    await controller.waitForIdle();
  }
  assert.equal(store.slots().filter((slot) => slot.owner).length, 3);
  assert.equal(store.listSessions("group:2003", 1).length, 0);
  const outbox = store.pollOutbox("bot", 30);
  assert.ok(outbox.some((item) => String(item.message).includes("3 个开发槽位")));
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("the first dev input lazily materializes a persisted active session", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c",
  });
  const store = new Store(":memory:", config.maxSlots);
  const runtime = new FakeRuntime();
  const owner = "private:1001" as const;
  store.ensureOwner(owner);
  store.setAccessRule("inbound", "user", "1001", "allow");
  const persisted: SessionRecord = {
    owner,
    sessionRef: "persisted-ref",
    taskId: "persisted-task",
    title: "persisted",
    state: "active",
    branch: "agent/persisted-task-plugin-change",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/persisted",
    transcriptPath: `${root}/persisted.json`,
    slotId: null,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  store.insertSession(persisted);
  store.allocateSlot(owner, persisted.sessionRef);
  const controller = new Controller(store, runtime, config);

  await controller.route(event(owner, "/dev continue", "dev", 450));
  await controller.waitForIdle();

  assert.deepEqual((runtime.get(owner, persisted.sessionRef) as FakeSession).prompts, ["continue"]);
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("status reports persisted state without cold materializing the runtime", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c",
  });
  const store = new Store(":memory:", config.maxSlots);
  const runtime = new FakeRuntime();
  const owner = "private:1001" as const;
  store.ensureOwner(owner);
  store.setAccessRule("inbound", "user", "1001", "allow");
  const persisted: SessionRecord = {
    owner,
    sessionRef: "persisted-ref",
    taskId: "persisted-task",
    title: "persisted",
    state: "open_pr",
    branch: "agent/persisted-task-plugin-change",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/persisted",
    transcriptPath: `${root}/persisted.json`,
    slotId: null,
    stagingReleaseId: "release",
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  store.insertSession(persisted);
  store.allocateSlot(owner, persisted.sessionRef);
  await writeFile(persisted.transcriptPath, JSON.stringify([{
    role: "assistant",
    content: [{ type: "text", text: "persisted" }],
    stopReason: "stop",
    timestamp: 1,
    usage: {
      input: 10,
      output: 20,
      cacheRead: 30,
      cacheWrite: 40,
      reasoning: 5,
      totalTokens: 100,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
  }]));
  const controller = new Controller(store, runtime, config);

  await controller.route(event(owner, "/dev status", "dev", 475));
  await controller.waitForIdle();

  assert.equal(runtime.get(owner, persisted.sessionRef), undefined);
  const messages = store.pollOutbox("bot", 20).map((item) => String(item.message));
  assert.ok(messages.some((message) => message.includes("Agent：运行时未就绪")));
  assert.ok(messages.some((message) =>
    message.includes("输入 10 / 输出 20 / 缓存读取 30 / 缓存写入 40 / 推理 5 / 总计 100"),
  ));
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("help explains publication and chat-scoped testing while status reports work and token usage", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  const controller = new Controller(store, runtime, config);
  const owner = "private:1001" as const;
  store.setAccessRule("inbound", "user", "1001", "allow");

  await controller.route(event(owner, "/dev help", "dev", 500));
  await controller.waitForIdle();
  await controller.route(event(owner, "/dev build a plugin", "dev", 501));
  await controller.waitForIdle();
  const session = store.activeSession(owner);
  assert.ok(session);
  const activeRuntime = runtime.get(owner, session.sessionRef) as FakeSession;
  activeRuntime.busy = true;
  activeRuntime.transcript.push({
    role: "assistant",
    content: [{ type: "text", text: "working" }],
    stopReason: "toolUse",
    timestamp: 1,
    usage: { input: 10, output: 20, cacheRead: 30, cacheWrite: 40, reasoning: 5, totalTokens: 100, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  } as AgentMessage);
  await controller.route(event(owner, "/dev status", "dev", 502));
  await controller.waitForIdle();

  const messages = store.pollOutbox("bot", 50).map((item) => String(item.message));
  const help = messages.find((message) => message.includes("开发环境用法")) ?? "";
  assert.match(help, /\/dev publish/);
  assert.match(help, /\/test <消息>/);
  assert.match(help, /当前私聊或群聊/);
  assert.match(help, /QQ 回复不会发送给 Agent/);
  const status = messages.find((message) => message.includes("Token 累计")) ?? "";
  assert.match(status, /Agent：工作中/);
  assert.match(status, /输入 10 \/ 输出 20 \/ 缓存读取 30 \/ 缓存写入 40 \/ 推理 5 \/ 总计 100/);
  assert.match(status, /测试范围：仅当前聊天/);
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("publication failure makes staging unavailable and returns the error to Pi", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  runtime.publicationError = new Error("GitHub publication failed");
  const controller = new Controller(store, runtime, config);
  const owner = "private:1001" as const;
  store.setAccessRule("inbound", "user", "1001", "allow");

  await controller.route(event(owner, "/dev build a plugin", "dev", 550));
  await controller.waitForIdle();
  const session = store.activeSession(owner);
  assert.ok(session);
  const pi = runtime.get(owner, session.sessionRef) as FakeSession;
  await controller.route(event(owner, "/dev publish", "dev", 551));
  await controller.waitForIdle();

  assert.equal(store.getSession(owner, session.sessionRef)?.stagingReleaseId, null);
  assert.deepEqual(runtime.deactivated, [session.sessionRef]);
  assert.ok(pi.prompts.some((prompt) => prompt.includes("[Publication failed]") && prompt.includes("GitHub publication failed")));
  const messages = store.pollOutbox("bot", 50).map((item) => String(item.message));
  assert.ok(messages.some((message) => message.includes("发布失败，暂存当前不可用")));
  store.close();
  await rm(root, { recursive: true, force: true });
});

test("resume hides foreign refs and forks an immutable completed PR session", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-controller-`);
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_SLOT_INGRESS_SECRETS: "a,b,c,d,e",
  });
  const store = new Store();
  const runtime = new FakeRuntime();
  const controller = new Controller(store, runtime, config);
  const owner = "group:7001" as const;
  const foreign = "group:7002" as const;
  store.ensureOwner(owner);
  store.ensureOwner(foreign);
  store.setAccessRule("inbound", "group", "7001", "allow");
  store.setAccessRule("inbound", "group", "7002", "allow");
  const completed: SessionRecord = {
    owner,
    sessionRef: "completed-ref",
    taskId: "completed-task",
    title: "completed",
    state: "merged",
    branch: "agent/completed-task-completed",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/completed",
    transcriptPath: `${root}/completed.json`,
    slotId: null,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  store.insertSession(completed);

  await controller.route(event(foreign, "/dev resume completed-ref", "dev", 600));
  await controller.waitForIdle();
  assert.equal(store.listSessions(foreign, 1).length, 0);

  await controller.route(event(owner, "/dev resume completed-ref", "dev", 601));
  await controller.waitForIdle();
  const sessions = store.listSessions(owner, 1);
  const continuation = sessions.find((item) => item.continuationOf === completed.sessionRef);
  assert.ok(continuation);
  assert.equal(continuation.state, "active");
  assert.notEqual(continuation.branch, completed.branch);
  assert.equal(store.getSession(owner, completed.sessionRef)?.state, "merged");
  store.close();
  await rm(root, { recursive: true, force: true });
});
