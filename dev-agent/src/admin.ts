import type { ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { Config } from "./config.ts";
import { Store } from "./store.ts";
import type { InboundEvent } from "./types.ts";

const THINKING = new Set<ThinkingLevel>(["off", "minimal", "low", "medium", "high", "xhigh", "max"]);

export class AdminService {
  constructor(private readonly store: Store, private readonly config: Config) {}

  handle(event: InboundEvent): string {
    if (!event.is_superuser) return "仅超级用户可以使用 /dev-admin。";
    const body = event.text.replace(/^\s*\/dev-admin(?:\s+|$)/i, "").trim();
    const tokens = body.split(/\s+/).filter(Boolean);
    const command = tokens.shift()?.toLowerCase() ?? "help";
    switch (command) {
      case "access":
        return this.access(tokens);
      case "model":
        return this.model(tokens);
      case "slots":
      case "health":
        return this.health();
      case "kill":
        return this.kill(tokens);
      case "help":
      default:
        return [
          "/dev-admin access <inbound|outbound> <allow|deny|remove> <user|group> <id>",
          "/dev-admin model [<provider> <model> <thinking>]",
          "/dev-admin slots",
          "/dev-admin kill <on|off|status>",
        ].join("\n");
    }
  }

  private access(tokens: string[]): string {
    const [direction, action, type, id] = tokens;
    if ((direction !== "inbound" && direction !== "outbound") ||
        (action !== "allow" && action !== "deny" && action !== "remove") ||
        (type !== "user" && type !== "group") || !id || !/^\d+$/.test(id)) {
      return "用法：/dev-admin access <inbound|outbound> <allow|deny|remove> <user|group> <数字ID>";
    }
    if (action === "remove") this.store.removeAccessRule(direction, type, id);
    else this.store.setAccessRule(direction, type, id, action);
    return `已更新 ${direction} ${type} 规则。`;
  }

  private model(tokens: string[]): string {
    if (tokens.length === 0) {
      const model = this.store.modelSettings(this.config.model);
      return `模型：${model.provider}/${model.model}\n思考级别：${model.thinkingLevel}`;
    }
    const [provider, model, thinking] = tokens;
    if (!provider || !model || !thinking || !THINKING.has(thinking as ThinkingLevel)) {
      return "用法：/dev-admin model <provider> <model> <off|minimal|low|medium|high|xhigh|max>";
    }
    this.store.setSetting("model.provider", provider);
    this.store.setSetting("model.id", model);
    this.store.setSetting("model.thinking", thinking);
    return `后续轮次将使用 ${provider}/${model}（${thinking}）。`;
  }

  private health(): string {
    const slots = this.store.slots();
    const counts = new Map<string, number>();
    for (const slot of slots) counts.set(slot.health, (counts.get(slot.health) ?? 0) + 1);
    return [
      `槽位总数：${slots.length}`,
      `空闲：${counts.get("idle") ?? 0}`,
      `启动中：${counts.get("starting") ?? 0}`,
      `健康：${counts.get("healthy") ?? 0}`,
      `异常：${counts.get("degraded") ?? 0}`,
    ].join("\n");
  }

  private kill(tokens: string[]): string {
    const action = tokens[0]?.toLowerCase() ?? "status";
    if (action === "on") this.store.setSetting("kill_switch", "on");
    else if (action === "off") this.store.setSetting("kill_switch", "off");
    else if (action !== "status") return "用法：/dev-admin kill <on|off|status>";
    return `全局终止开关：${this.store.getSetting("kill_switch") === "on" ? "开启" : "关闭"}`;
  }
}
