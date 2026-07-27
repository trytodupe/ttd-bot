import type { Config } from "./config.ts";
import { AccessController } from "./access.ts";
import { AdminService } from "./admin.ts";
import { SessionService } from "./session-service.ts";
import { Store } from "./store.ts";
import type { InboundEvent, Route, RuntimeManager } from "./types.ts";
import { assertOwnerKey } from "./util.ts";

export interface RouteResult {
  route: Route;
  accepted?: boolean;
  immediate?: string;
}

function hasStagingPayload(event: InboundEvent): boolean {
  let commandSeen = false;
  for (const segment of event.segments) {
    if (segment.type === "reply") continue;
    if (!commandSeen) {
      commandSeen = true;
      if (segment.type !== "text") return false;
      const text = String(segment.data.text ?? "").replace(/^\s*\/test(?:\s+|$)/i, "");
      if (text.trim()) return true;
      continue;
    }
    if (segment.type !== "text" || String(segment.data.text ?? "").trim()) return true;
  }
  return false;
}

export class Controller {
  private readonly access: AccessController;
  private readonly admin: AdminService;
  private readonly sessions: SessionService;
  private readonly ownerQueues = new Map<string, Promise<void>>();

  constructor(private readonly store: Store, runtime: RuntimeManager, private readonly config: Config) {
    this.access = new AccessController(store);
    this.admin = new AdminService(store, config);
    this.sessions = new SessionService(store, runtime, config);
    this.runtime = runtime;
  }

  private readonly runtime: RuntimeManager;

  async waitForIdle(): Promise<void> {
    await Promise.all([...this.ownerQueues.values()]);
  }

  private dispatchDev(event: InboundEvent): void {
    const previous = this.ownerQueues.get(event.owner) ?? Promise.resolve();
    const next = previous
      .catch(() => undefined)
      .then(() => this.sessions.handle(event))
      .catch((error: unknown) => {
        const contact = this.store.latestContact(event.owner);
        if (!contact) return;
        const message = error instanceof Error ? error.message : String(error);
        this.store.enqueue({
          owner: event.owner,
          botId: contact.botId,
          chatType: contact.chatType,
          destinationId: contact.destinationId,
          message: `开发任务失败：${message.slice(0, 500)}`,
          origin: "system",
        });
      })
      .finally(() => {
        if (this.ownerQueues.get(event.owner) === next) this.ownerQueues.delete(event.owner);
      });
    this.ownerQueues.set(event.owner, next);
  }

  private dispatchStage(session: NonNullable<ReturnType<Store["activeSession"]>>, event: InboundEvent): void {
    const previous = this.ownerQueues.get(event.owner) ?? Promise.resolve();
    const next = previous
      .catch(() => undefined)
      .then(() => this.runtime.stage(session, event))
      .catch((error: unknown) => {
        const contact = this.store.latestContact(event.owner);
        if (!contact) return;
        const message = error instanceof Error ? error.message : String(error);
        this.store.enqueue({
          owner: event.owner,
          sessionRef: session.sessionRef,
          botId: contact.botId,
          chatType: contact.chatType,
          destinationId: contact.destinationId,
          message: `暂存运行时恢复失败：${message.slice(0, 500)}`,
          origin: "system",
        });
      })
      .finally(() => {
        if (this.ownerQueues.get(event.owner) === next) this.ownerQueues.delete(event.owner);
      });
    this.ownerQueues.set(event.owner, next);
  }

  private validate(event: InboundEvent): void {
    assertOwnerKey(event.owner);
    const expected = event.chat_type === "group" ? `group:${event.group_id}` : `private:${event.user_id}`;
    if (event.owner !== expected) throw new Error("owner does not match event destination");
    if (!event.event_id.startsWith(`${event.owner}:`)) throw new Error("event id is not owner scoped");
    const imageCount = [...event.segments, ...(event.quote?.segments ?? [])]
      .filter((segment) => segment.type === "image").length;
    if (imageCount > 4) throw new Error("too many images");
  }

  async route(event: InboundEvent): Promise<RouteResult> {
    this.validate(event);
    if (event.route_hint === "admin") {
      this.store.ensureOwner(event.owner, this.config.ownerUidBase);
      const message = this.admin.handle(event);
      this.store.enqueue({
        owner: event.owner,
        botId: event.bot_id,
        chatType: event.chat_type,
        destinationId: event.chat_type === "group" ? String(event.group_id) : event.user_id,
        message,
        origin: "admin",
      });
      if (event.is_superuser && /^\s*\/dev-admin\s+(?:access|kill)(?:\s|$)/i.test(event.text)) {
        await this.runtime.syncPolicies();
      }
      return { route: "admin", accepted: event.is_superuser };
    }

    if (event.chat_type === "private" && event.route_hint === "none") {
      return { route: "none" };
    }

    const killed = this.store.getSetting("kill_switch") === "on";
    const commandRoute = event.route_hint === "dev" ? "dev" : event.route_hint === "staging" ? "staging" : undefined;
    if (killed) {
      return commandRoute
        ? { route: commandRoute, accepted: false, immediate: "开发环境已由管理员停用。" }
        : { route: "none" };
    }
    if (!this.access.inbound(event)) {
      return commandRoute
        ? { route: commandRoute, accepted: false, immediate: "当前聊天未获开发环境访问权限。" }
        : { route: "none" };
    }
    this.store.ensureOwner(event.owner, this.config.ownerUidBase);
    const fresh = this.store.recordInbound(event);
    if (!fresh) return { route: commandRoute ?? "none", accepted: true };

    if (event.route_hint === "dev") {
      this.dispatchDev(event);
      return { route: "dev", accepted: true };
    }

    const active = this.store.activeSession(event.owner);
    if (event.route_hint === "staging") {
      if (!hasStagingPayload(event)) {
        return { route: "staging", accepted: false, immediate: "用法：/test <消息>" };
      }
      if (!active?.stagingReleaseId) {
        return {
          route: "staging",
          accepted: false,
          immediate: active
            ? "当前聊天的暂存运行时不可用，请使用 /dev 查看并修复发布失败。"
            : "当前聊天没有可测试的暂存版本。",
        };
      }
      this.dispatchStage(active, event);
      return { route: "staging", accepted: true };
    }
    return { route: "none" };
  }
}
