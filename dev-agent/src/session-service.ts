import { chown, mkdir, readFile } from "node:fs/promises";
import { lookup } from "node:dns/promises";
import { request as httpsRequest } from "node:https";
import { isIP } from "node:net";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { Readable } from "node:stream";
import type { AgentMessage } from "@earendil-works/pi-agent-core";
import type {
  InboundEvent,
  OutboxMessage,
  OwnerChatKey,
  RuntimeManager,
  SessionRecord,
  SlotRecord,
} from "./types.ts";
import type { Config } from "./config.ts";
import { Store } from "./store.ts";
import { isPublicAddress } from "./network-policy.ts";
import { opaqueId, ownerNamespace, privateDirectory, taskId } from "./util.ts";

const TERMINAL_STATES = new Set(["merged", "closed"]);
const PAGE_SIZE = 8;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const DEV_HELP = `开发环境用法：
/dev <需求>：创建或继续当前聊天的开发会话
/dev new <需求>：保存当前会话并开始新任务
/dev sessions [页码]、/dev resume <编号>：查看或恢复此聊天自己的会话
/dev status：查看 Agent 工作状态、Token 用量、暂存版本和槽位
/dev stop、/dev compact、/dev abandon confirm：保存释放、压缩上下文或放弃会话

测试与发布：
1. Agent 完成代码后发送 /dev publish
2. 检查通过后会激活暂存插件并创建草稿 PR
3. 只在发起开发的当前私聊或群聊中使用 /test <消息>；/test 前缀不会传给插件

私聊、群聊和不同群的会话与暂存版本彼此隔离。普通消息和 QQ 回复不会发送给 Agent；补充要求请显式使用 /dev。`;

interface TokenUsageTotals {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  reasoning: number;
  totalTokens: number;
}

function finiteToken(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;
}

export function summarizeTokenUsage(messages: AgentMessage[]): TokenUsageTotals {
  const totals: TokenUsageTotals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, reasoning: 0, totalTokens: 0 };
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    const usage = (message as AgentMessage & { usage?: Record<string, unknown> }).usage;
    if (!usage) continue;
    totals.input += finiteToken(usage.input);
    totals.output += finiteToken(usage.output);
    totals.cacheRead += finiteToken(usage.cacheRead);
    totals.cacheWrite += finiteToken(usage.cacheWrite);
    totals.reasoning += finiteToken(usage.reasoning);
    totals.totalTokens += finiteToken(usage.totalTokens);
  }
  return totals;
}

function destination(event: InboundEvent): Pick<OutboxMessage, "botId" | "chatType" | "destinationId"> {
  return {
    botId: event.bot_id,
    chatType: event.chat_type,
    destinationId: event.chat_type === "group" ? String(event.group_id) : event.user_id,
  };
}

function commandBody(text: string): string {
  return text.replace(/^\s*\/dev(?:\s+|$)/i, "").trim();
}

function firstLine(value: string): string {
  return value.split(/\r?\n/, 1)[0]?.trim().slice(0, 80) || "New task";
}

function imageMime(data: Buffer): string {
  if (data.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "image/png";
  if (data[0] === 0xff && data[1] === 0xd8) return "image/jpeg";
  if (data.subarray(0, 6).toString("ascii").startsWith("GIF8")) return "image/gif";
  if (data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP") return "image/webp";
  throw new Error("base64 attachment is not a supported image");
}

export class SessionService {
  constructor(
    private readonly store: Store,
    private readonly runtime: RuntimeManager,
    private readonly config: Config,
  ) {}

  private notify(event: InboundEvent, message: string, sessionRef?: string, origin: OutboxMessage["origin"] = "system"): void {
    this.store.enqueue({
      owner: event.owner,
      ...(sessionRef ? { sessionRef } : {}),
      ...destination(event),
      message,
      origin,
    });
  }

  async handle(event: InboundEvent): Promise<void> {
    const body = commandBody(event.text);
    const [command = "", ...rest] = body.split(/\s+/);
    const argument = rest.join(" ").trim();

    switch (command.toLowerCase()) {
      case "new":
        await this.newSession(event, argument);
        return;
      case "sessions":
        this.list(event, argument);
        return;
      case "resume":
        await this.resume(event, argument);
        return;
      case "status":
        await this.status(event);
        return;
      case "stop":
        await this.stop(event);
        return;
      case "compact":
        await this.compact(event);
        return;
      case "publish":
        await this.publish(event);
        return;
      case "abandon":
        await this.abandon(event, argument);
        return;
      case "help":
      case "":
        this.notify(event, DEV_HELP)
        return;
      default:
        await this.prompt(event, body);
    }
  }

  private baseSha(): string {
    const result = spawnSync(
      "git",
      ["-c", `safe.directory=${this.config.repository}`, "-C", this.config.repository, "rev-parse", "main"],
      { encoding: "utf8" },
    );
    if (result.status !== 0 || !result.stdout.trim()) {
      throw new Error(`failed to resolve local main: ${result.stderr || result.error?.message || "unknown error"}`);
    }
    return result.stdout.trim();
  }

  private async ownerPaths(owner: OwnerChatKey, sessionRef: string): Promise<{ workspace: string; transcript: string }> {
    const ownerRoot = join(this.config.stateRoot, "owners", ownerNamespace(owner));
    const sessionRoot = join(ownerRoot, "sessions", sessionRef);
    await privateDirectory(ownerRoot);
    await mkdir(sessionRoot, { recursive: true, mode: 0o700 });
    const uid = this.store.ownerUnixUid(owner);
    try {
      await chown(ownerRoot, uid, uid);
      await chown(sessionRoot, uid, uid);
    } catch (error) {
      if (process.getuid?.() === 0) throw error;
    }
    return {
      workspace: `/workspaces/${ownerNamespace(owner)}/${sessionRef}`,
      transcript: join(sessionRoot, "pi-transcript.json"),
    };
  }

  private async persistedMessages(session: SessionRecord): Promise<AgentMessage[]> {
    try {
      const parsed: unknown = JSON.parse(await readFile(session.transcriptPath, "utf8"));
      return Array.isArray(parsed) ? parsed as AgentMessage[] : [];
    } catch {
      return [];
    }
  }

  private async createRecord(owner: OwnerChatKey, prompt: string, continuationOf: string | null): Promise<SessionRecord> {
    const sessionRef = opaqueId();
    const id = taskId();
    const paths = await this.ownerPaths(owner, sessionRef);
    const now = Date.now();
    const record: SessionRecord = {
      owner,
      sessionRef,
      taskId: id,
      title: firstLine(prompt),
      state: "suspended",
      branch: `agent/${id}-plugin-change`,
      baseSha: this.baseSha(),
      workspace: paths.workspace,
      transcriptPath: paths.transcript,
      slotId: null,
      stagingReleaseId: null,
      continuationOf,
      createdAt: now,
      updatedAt: now,
    };
    this.store.insertSession(record);
    return record;
  }

  private async checkpointActive(owner: OwnerChatKey): Promise<SlotRecord | undefined> {
    const active = this.store.activeSession(owner);
    const slot = this.store.slotForOwner(owner);
    if (!active || !slot) return slot;
    await this.runtime.suspend(owner, active.sessionRef);
    this.store.updateSession(owner, active.sessionRef, {
      state: active.state === "open_pr" ? "open_pr" : "suspended",
      slotId: null,
    });
    return slot;
  }

  private async attach(event: InboundEvent, session: SessionRecord, seed?: SessionRecord): Promise<SessionRecord | undefined> {
    const slot = this.store.allocateSlot(event.owner, session.sessionRef);
    if (!slot) {
      this.notify(event, `${this.config.maxSlots} 个开发槽位都在使用中，请稍后再恢复。`)
      return undefined;
    }
    const attached = this.store.getSession(event.owner, session.sessionRef);
    if (!attached) throw new Error("session disappeared while allocating a slot");
    try {
      await this.runtime.open(attached, slot, seed);
      this.store.setSlotHealth(slot.id, "healthy");
      return this.store.getSession(event.owner, session.sessionRef);
    } catch (error) {
      this.store.setSlotHealth(slot.id, "degraded");
      this.store.releaseSlot(event.owner, session.sessionRef);
      await this.runtime.syncPolicies();
      throw error;
    }
  }

  private async newSession(event: InboundEvent, prompt: string): Promise<void> {
    if (!prompt) {
      this.notify(event, "用法：/dev new <需求>");
      return;
    }
    if (!this.store.hasAvailableSlot(event.owner)) {
      this.notify(event, `${this.config.maxSlots} 个开发槽位都在使用中，请先等待其他聊天释放槽位。`)
      return;
    }
    await this.checkpointActive(event.owner);
    const session = await this.createRecord(event.owner, prompt, null);
    const attached = await this.attach(event, session);
    if (!attached) {
      this.store.deleteUnstartedSession(event.owner, session.sessionRef);
      return;
    }
    this.notify(
      event,
      `已创建开发会话 ${session.sessionRef}，开始处理。完成代码后发送 /dev publish；暂存激活后只在当前聊天使用 /test <消息>。`,
      session.sessionRef,
    );
    await this.runPrompt(event, attached, prompt);
  }

  private async prompt(event: InboundEvent, prompt: string): Promise<void> {
    let active = this.store.activeSession(event.owner);
    if (!active) {
      if (!this.store.hasAvailableSlot(event.owner)) {
        this.notify(event, `${this.config.maxSlots} 个开发槽位都在使用中，请稍后重试。`)
        return;
      }
      const created = await this.createRecord(event.owner, prompt, null);
      active = await this.attach(event, created);
      if (!active) {
        this.store.deleteUnstartedSession(event.owner, created.sessionRef);
        return;
      }
      this.notify(
        event,
        `已创建开发会话 ${created.sessionRef}，开始处理。完成代码后发送 /dev publish；暂存激活后只在当前聊天使用 /test <消息>。`,
        created.sessionRef,
      );
    } else {
      await this.materialize(active);
      this.notify(event, "已收到补充说明。", active.sessionRef);
    }
    await this.runPrompt(event, active, prompt);
  }

  private async materialize(session: SessionRecord): Promise<void> {
    if (this.runtime.get(session.owner, session.sessionRef)) return;
    const slot = this.store.slotForOwner(session.owner);
    if (!slot || slot.sessionRef !== session.sessionRef) throw new Error("active session has no matching slot");
    try {
      await this.runtime.open(session, slot);
      this.store.setSlotHealth(slot.id, "healthy");
    } catch (error) {
      this.store.setSlotHealth(slot.id, "degraded");
      throw error;
    }
  }

  private promptText(event: InboundEvent, prompt: string): string {
    const parts = [prompt];
    if (event.quote?.text) parts.push(`\n[Quoted context]\n${event.quote.text}`);
    const rejections = [...event.attachment_rejections, ...(event.quote?.attachment_rejections ?? [])];
    if (rejections.length) parts.push(`\n[Attachment notes]\n${rejections.join("; ")}`);
    return parts.join("\n");
  }

  private async promptImages(event: InboundEvent): Promise<Array<{ type: "image"; data: string; mimeType: string }>> {
    const images = [...event.segments, ...(event.quote?.segments ?? [])]
      .filter((segment) => segment.type === "image")
      .slice(0, 4);
    const result: Array<{ type: "image"; data: string; mimeType: string }> = [];
    for (const segment of images) {
      const source = segment.data.url ?? segment.data.file;
      if (typeof source !== "string") continue;
      if (source.startsWith("base64://")) {
        const data = source.slice("base64://".length);
        const decoded = Buffer.from(data, "base64");
        if (decoded.length > MAX_IMAGE_BYTES) throw new Error("image exceeds the 10 MiB limit");
        result.push({ type: "image", data, mimeType: imageMime(decoded) });
        continue;
      }
      if (!source.startsWith("https://")) continue;
      const response = await this.fetchPublicImage(source);
      if (!response.ok || !response.body) throw new Error(`image download failed (${response.status})`);
      const contentType = response.headers.get("content-type")?.split(";", 1)[0] ?? "";
      if (!contentType.startsWith("image/")) throw new Error("attachment URL did not return an image");
      const declared = Number(response.headers.get("content-length") ?? "0");
      if (declared > MAX_IMAGE_BYTES) throw new Error("image exceeds the 10 MiB limit");
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of response.body) {
        const buffer = Buffer.from(chunk);
        size += buffer.length;
        if (size > MAX_IMAGE_BYTES) throw new Error("image exceeds the 10 MiB limit");
        chunks.push(buffer);
      }
      result.push({
        type: "image",
        data: Buffer.concat(chunks).toString("base64"),
        mimeType: contentType,
      });
    }
    return result;
  }

  private async fetchPublicImage(source: string): Promise<Response> {
    let current = new URL(source);
    for (let redirect = 0; redirect <= 3; redirect += 1) {
      if (current.protocol !== "https:") throw new Error("image URL must use HTTPS");
      const addresses = isIP(current.hostname)
        ? [{ address: current.hostname }]
        : await lookup(current.hostname, { all: true });
      if (addresses.length === 0 || addresses.some(({ address }) => !isPublicAddress(address))) {
        throw new Error("image URL resolves to a non-public address");
      }
      const response = await this.fetchPinnedHttps(current, addresses[0]!.address);
      if (response.status < 300 || response.status >= 400) return response;
      const location = response.headers.get("location");
      if (!location) return response;
      current = new URL(location, current);
    }
    throw new Error("image URL redirected too many times");
  }

  private fetchPinnedHttps(url: URL, address: string): Promise<Response> {
    return new Promise((resolve, reject) => {
      const request = httpsRequest(
        url,
        {
          signal: AbortSignal.timeout(15_000),
          servername: url.hostname,
          lookup: (_hostname, _options, callback) => {
            callback(null, address, isIP(address) as 4 | 6);
          },
        },
        (response) => {
          const headers = new Headers();
          for (const [name, value] of Object.entries(response.headers)) {
            if (Array.isArray(value)) {
              for (const item of value) headers.append(name, item);
            } else if (value !== undefined) {
              headers.set(name, String(value));
            }
          }
          resolve(new Response(Readable.toWeb(response) as ReadableStream, {
            status: response.statusCode ?? 500,
            headers,
          }));
        },
      );
      request.once("error", reject);
      request.end();
    });
  }

  private async runPrompt(event: InboundEvent, session: SessionRecord, prompt: string): Promise<void> {
    const runtime = this.runtime.get(event.owner, session.sessionRef);
    if (!runtime) {
      this.notify(event, "会话运行时未就绪。", session.sessionRef);
      return;
    }
    try {
      const images = await this.promptImages(event);
      const text = this.promptText(event, prompt);
      if (runtime.isBusy()) runtime.steer(text, images);
      else void runtime.prompt(text, images).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        this.notify(event, `开发任务失败：${message.slice(0, 500)}`, session.sessionRef);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.notify(event, `开发任务失败：${message.slice(0, 500)}`, session.sessionRef);
    }
  }

  private list(event: InboundEvent, rawPage: string): void {
    const page = Math.max(1, Number.parseInt(rawPage || "1", 10) || 1);
    const sessions = this.store.listSessions(event.owner, page, PAGE_SIZE);
    if (sessions.length === 0) {
      this.notify(event, "当前聊天还没有开发会话。")
      return;
    }
    const lines = sessions.map((session) => {
      const active = session.slotId === null ? "" : " *";
      return `${session.sessionRef} [${session.state}]${active} ${session.title}`;
    });
    this.notify(event, `开发会话（第 ${page} 页）：\n${lines.join("\n")}`);
  }

  private async resume(event: InboundEvent, sessionRef: string, announce = true): Promise<void> {
    if (!sessionRef) {
      this.notify(event, "用法：/dev resume <会话编号>");
      return;
    }
    const selected = this.store.getSession(event.owner, sessionRef);
    if (!selected || selected.state === "abandoned") {
      this.notify(event, "此聊天中没有该会话。")
      return;
    }
    const current = this.store.activeSession(event.owner);
    if (current?.sessionRef === selected.sessionRef) {
      if (announce) this.notify(event, `会话 ${selected.sessionRef} 已经处于活动状态。`, selected.sessionRef);
      return;
    }
    if (!this.store.hasAvailableSlot(event.owner)) {
      this.notify(event, `${this.config.maxSlots} 个开发槽位都在使用中，暂时无法恢复。`)
      return;
    }
    await this.checkpointActive(event.owner);

    let target = selected;
    let seed: SessionRecord | undefined;
    if (TERMINAL_STATES.has(selected.state)) {
      seed = selected;
      target = await this.createRecord(event.owner, `Continue: ${selected.title}`, selected.sessionRef);
    }
    const attached = await this.attach(event, target, seed);
    if (attached && announce) {
      this.notify(
        event,
        seed
          ? `原 PR 已结束，已从最终状态创建续作会话 ${target.sessionRef}。`
          : `已恢复会话 ${target.sessionRef}。`,
        target.sessionRef,
      );
    }
  }

  private async status(event: InboundEvent): Promise<void> {
    const session = this.store.activeSession(event.owner);
    if (!session) {
      this.notify(event, "当前聊天没有活动开发会话。")
      return;
    }
    const slot = this.store.slotForOwner(event.owner);
    const runtime = this.runtime.get(event.owner, session.sessionRef);
    const agentState = !runtime ? "运行时未就绪" : runtime.isBusy() ? "工作中（新的 /dev 输入会按顺序处理）" : "等待指令";
    const usage = summarizeTokenUsage(runtime?.messages() ?? await this.persistedMessages(session));
    this.notify(
      event,
      `会话 ${session.sessionRef}\n` +
      `会话状态：${session.state}\n` +
      `Agent：${agentState}\n` +
      `Token 累计：输入 ${usage.input} / 输出 ${usage.output} / 缓存读取 ${usage.cacheRead} / 缓存写入 ${usage.cacheWrite} / 推理 ${usage.reasoning} / 总计 ${usage.totalTokens}\n` +
      `槽位：${slot?.id ?? "-"} (${slot?.health ?? "idle"})\n` +
      `分支：${session.branch}\n` +
      `暂存版本：${session.stagingReleaseId ?? "无（完成后发送 /dev publish）"}\n` +
      `测试范围：仅当前聊天；暂存激活后使用 /test <消息>。`,
      session.sessionRef,
    );
  }

  private async stop(event: InboundEvent): Promise<void> {
    const session = this.store.activeSession(event.owner);
    if (!session) {
      this.notify(event, "当前聊天没有活动开发会话。")
      return;
    }
    const runtime = this.runtime.get(event.owner, session.sessionRef);
    runtime?.stop();
    await this.runtime.suspend(event.owner, session.sessionRef);
    this.store.updateSession(event.owner, session.sessionRef, {
      state: session.state === "open_pr" ? "open_pr" : "suspended",
      slotId: null,
    });
    this.store.releaseSlot(event.owner, session.sessionRef);
    await this.runtime.syncPolicies();
    this.notify(event, `会话 ${session.sessionRef} 已检查点保存并释放槽位。`, session.sessionRef);
  }

  private async compact(event: InboundEvent): Promise<void> {
    const session = this.store.activeSession(event.owner);
    if (session) await this.materialize(session);
    const runtime = session ? this.runtime.get(event.owner, session.sessionRef) : undefined;
    if (!session || !runtime) {
      this.notify(event, "当前聊天没有活动开发会话。")
      return;
    }
    await runtime.compact();
    await runtime.checkpoint();
    this.notify(event, "上下文已压缩并保存。", session.sessionRef);
  }

  private async publish(event: InboundEvent): Promise<void> {
    const session = this.store.activeSession(event.owner);
    const slot = this.store.slotForOwner(event.owner);
    if (!session || !slot) {
      this.notify(event, "当前聊天没有活动开发会话。")
      return;
    }
    await this.materialize(session);
    const activeRuntime = this.runtime.get(event.owner, session.sessionRef);
    if (activeRuntime) {
      await activeRuntime.waitForIdle();
      await activeRuntime.checkpoint();
    }
    this.notify(event, "正在运行完整检查并激活暂存版本……", session.sessionRef);
    try {
      const release = await this.runtime.activate(session, slot);
      this.store.updateSession(event.owner, session.sessionRef, { stagingReleaseId: release.releaseId });
      await this.runtime.syncPolicies();
      this.notify(event, `暂存版本 ${release.releaseId} 已激活，正在创建草稿 PR……`, session.sessionRef);
      const pr = await this.runtime.publish(this.store.getSession(event.owner, session.sessionRef) ?? session);
      this.store.updateSession(event.owner, session.sessionRef, { state: "open_pr" });
      this.notify(event, `草稿 PR 已就绪：${pr.url}`, session.sessionRef);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.runtime.deactivate(session).catch(() => undefined);
      this.store.updateSession(event.owner, session.sessionRef, { stagingReleaseId: null });
      await this.runtime.syncPolicies().catch(() => undefined);
      this.notify(event, `发布失败，暂存当前不可用：${message.slice(0, 500)}`, session.sessionRef);
      if (activeRuntime) {
        void activeRuntime.prompt(
          `[Publication failed]\n${message.slice(0, 4000)}\nDiagnose and fix the failure. Revert only changes you determine are responsible; do not discard unrelated unfinished work.`,
        ).catch(() => undefined);
      }
    }
  }

  private async abandon(event: InboundEvent, confirmation: string): Promise<void> {
    if (confirmation.toLowerCase() !== "confirm") {
      this.notify(event, "此操作会永久冻结当前会话。确认请发送：/dev abandon confirm")
      return;
    }
    const session = this.store.activeSession(event.owner);
    if (!session) {
      this.notify(event, "当前聊天没有活动开发会话。")
      return;
    }
    this.runtime.get(event.owner, session.sessionRef)?.stop();
    await this.runtime.suspend(event.owner, session.sessionRef);
    this.store.updateSession(event.owner, session.sessionRef, { state: "abandoned", slotId: null });
    this.store.releaseSlot(event.owner, session.sessionRef);
    await this.runtime.syncPolicies();
    this.notify(event, `会话 ${session.sessionRef} 已放弃并冻结。`);
  }
}
