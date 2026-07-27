import { chown, readFile, rename, writeFile } from "node:fs/promises";
import { Agent, type AgentMessage } from "@earendil-works/pi-agent-core";
import { getModel } from "@earendil-works/pi-ai/compat";
import type { Model } from "@earendil-works/pi-ai";
import type { ActivationManager } from "./activation.ts";
import type { Config } from "./config.ts";
import type { GitHubPublisher } from "./github.ts";
import type { GuestBroker } from "./guest-broker.ts";
import { Store } from "./store.ts";
import type {
  InboundEvent,
  OwnerChatKey,
  RuntimeManager,
  RuntimeSession,
  SessionRecord,
  SlotRecord,
} from "./types.ts";

const dynamicGetModel = getModel as unknown as (provider: string, model: string) => Model<any> | undefined;

function runtimeKey(owner: OwnerChatKey, sessionRef: string): string {
  return `${owner}\0${sessionRef}`;
}

function assistantText(message: AgentMessage | undefined): string {
  if (!message || message.role !== "assistant") return "";
  return message.content
    .flatMap((item) => item.type === "text" ? [item.text] : [])
    .join("\n")
    .trim();
}

function systemPrompt(session: SessionRecord): string {
  return `You are the isolated coding agent for a NoneBot/ttd-bot development task.

All code, searches, commands, dependency installs, migrations, and tests must use the provided guest tools. Your workspace is ${session.workspace}. Do not ask for or expose provider, GitHub, production, or host credentials. Do not attempt commits, pushes, merges, tags, releases, deployments, Docker access, production service control, or production hot reloads; the trusted host broker alone publishes a draft PR.

Develop only ttd-bot/NoneBot plugins and the directly required tests or configuration. Inspect repository conventions before editing. Keep staging plugin scope minimal. Before requesting publication, run tests for changed plugins, changed or new tests, and the pre-commit gate. Unrelated existing plugin failures are outside the publication gate. Maintain .dev-agent/pr.json as JSON with a concise non-identifying "summary" and a "behavior" string array. Never put QQ identifiers, owner identifiers, raw chat history, secrets, or user names in branches, commits, PR metadata, source comments, fixtures, or logs.

Use notify only for a clarification question, a test milestone, an activation milestone, or a failure. Do not stream progress or send generic conversational output. Continue autonomously until the requested implementation and validation are complete. End each successful run with one concise final summary covering the outcome, validation performed, and any remaining action. Do not include identifiers, secrets, raw logs, or transcript excerpts in that summary.`;
}

export class PiSession implements RuntimeSession {
  private readonly agent: Agent;
  private readonly notificationsThisRun = new Set<string>();

  constructor(
    private readonly session: SessionRecord,
    private readonly slot: SlotRecord,
    private readonly store: Store,
    private readonly config: Config,
    private readonly guest: GuestBroker,
    messages: AgentMessage[],
    injectedAgent?: Agent,
  ) {
    if (injectedAgent) {
      this.agent = injectedAgent;
    } else {
      const settings = store.modelSettings(config.model);
      const model = dynamicGetModel(settings.provider, settings.model);
      if (!model) throw new Error(`unknown model ${settings.provider}/${settings.model}`);
      this.agent = new Agent({
        initialState: {
          systemPrompt: systemPrompt(session),
          model,
          thinkingLevel: settings.thinkingLevel,
          messages,
          tools: guest.tools(session, slot, (kind, text) => this.notify(kind, text)),
        },
        steeringMode: "one-at-a-time",
        followUpMode: "one-at-a-time",
        sessionId: session.sessionRef,
        getApiKey: (provider) => provider === store.modelSettings(config.model).provider ? config.modelApiKey : undefined,
        prepareNextTurn: () => {
          const next = store.modelSettings(config.model);
          const nextModel = dynamicGetModel(next.provider, next.model);
          return nextModel ? { model: nextModel, thinkingLevel: next.thinkingLevel } : undefined;
        },
        toolExecution: "parallel",
      });
    }
    this.agent.subscribe(async (event) => {
      if (event.type === "agent_start") this.notificationsThisRun.clear();
      if (event.type === "message_end" && event.message.role === "assistant" && event.message.stopReason === "error") {
        this.notify("failure", event.message.errorMessage ?? "model request failed");
      }
      if (event.type === "agent_end") {
        await this.checkpoint();
        await this.guest.checkpointWorkspace(this.session, this.slot);
        const final = [...event.messages].reverse().find((message) => message.role === "assistant");
        if (final?.role === "assistant" && final.stopReason !== "error" && final.stopReason !== "aborted") {
          const text = assistantText(final).slice(0, 1000);
          if (text && !this.notificationsThisRun.has(text)) this.enqueue(text);
        }
      }
    });
  }

  private refreshModel(): void {
    const settings = this.store.modelSettings(this.config.model);
    const model = dynamicGetModel(settings.provider, settings.model);
    if (!model) throw new Error(`unknown model ${settings.provider}/${settings.model}`);
    this.agent.state.model = model;
    this.agent.state.thinkingLevel = settings.thinkingLevel;
    this.agent.getApiKey = (provider) => provider === settings.provider ? this.config.modelApiKey : undefined;
  }

  private notify(_kind: string, text: string): void {
    const message = text.slice(0, 1000);
    this.notificationsThisRun.add(message);
    this.enqueue(message);
  }

  private enqueue(message: string): void {
    const contact = this.store.latestContact(this.session.owner);
    if (!contact) return;
    this.store.enqueue({
      owner: this.session.owner,
      sessionRef: this.session.sessionRef,
      botId: contact.botId,
      chatType: contact.chatType,
      destinationId: contact.destinationId,
      message,
      origin: "agent",
    });
  }

  async prompt(text: string, images: Array<{ type: "image"; data: string; mimeType: string }> = []): Promise<void> {
    if (this.agent.state.isStreaming) {
      this.steer(text, images);
      return;
    }
    this.refreshModel();
    await this.agent.prompt(text, images);
  }

  isBusy(): boolean {
    return this.agent.state.isStreaming;
  }

  steer(text: string, images: Array<{ type: "image"; data: string; mimeType: string }> = []): void {
    const content = images.length ? [{ type: "text" as const, text }, ...images] : text;
    this.agent.steer({ role: "user", content, timestamp: Date.now() });
  }

  stop(): void {
    this.agent.abort();
    this.agent.clearAllQueues();
  }

  async waitForIdle(): Promise<void> {
    await this.agent.waitForIdle();
  }

  async compact(): Promise<void> {
    await this.agent.waitForIdle();
    if (this.agent.state.messages.length < 12) return;
    const before = this.agent.state.messages.length;
    await this.agent.prompt(
      "Create a concise internal continuation summary of the task state, decisions, changed files, test results, and remaining work. Do not use tools and do not include personal identifiers or raw chat text.",
    );
    const summary = assistantText(this.agent.state.messages.at(-1));
    if (!summary) throw new Error("model did not produce a compaction summary");
    const recent = this.agent.state.messages.slice(Math.max(0, before - 4), before);
    this.agent.state.messages = [
      {
        role: "user",
        content: `[Compacted continuation context]\n${summary}`,
        timestamp: Date.now(),
      },
      ...recent,
    ];
  }

  async checkpoint(): Promise<void> {
    const temp = `${this.session.transcriptPath}.new`;
    await writeFile(temp, `${JSON.stringify(this.agent.state.messages)}\n`, { mode: 0o600 });
    const uid = this.store.ownerUnixUid(this.session.owner);
    await chown(temp, uid, uid);
    await rename(temp, this.session.transcriptPath);
  }

  messages(): AgentMessage[] {
    return this.agent.state.messages;
  }
}

export class AgentRuntimeManager implements RuntimeManager {
  private readonly sessions = new Map<string, PiSession>();

  constructor(
    private readonly store: Store,
    private readonly config: Config,
    private readonly guest: GuestBroker,
    private readonly activation: ActivationManager,
    private readonly publisher: GitHubPublisher,
  ) {}

  private async transcript(session: SessionRecord): Promise<AgentMessage[]> {
    try {
      const parsed = JSON.parse(await readFile(session.transcriptPath, "utf8"));
      return Array.isArray(parsed) ? parsed as AgentMessage[] : [];
    } catch {
      return [];
    }
  }

  async open(session: SessionRecord, slot: SlotRecord, seed?: SessionRecord): Promise<RuntimeSession> {
    const key = runtimeKey(session.owner, session.sessionRef);
    const existing = this.sessions.get(key);
    if (existing) return existing;
    try {
      console.log(`recover/open slot ${slot.id}: preparing workspace`);
      await this.guest.prepareWorkspace(session, slot, seed);
      console.log(`recover/open slot ${slot.id}: syncing policy`);
      await this.syncSlotPolicy(slot);
      console.log(`recover/open slot ${slot.id}: restoring staging`);
      await this.activation.restore(session, slot);
      const messages = seed ? await this.transcript(seed) : await this.transcript(session);
      const runtime = new PiSession(session, slot, this.store, this.config, this.guest, messages);
      this.sessions.set(key, runtime);
      console.log(`recover/open slot ${slot.id}: ready`);
      return runtime;
    } catch (error) {
      // Opening may fail before a usable checkout exists. Cleaning up without
      // checkpointing prevents a partial directory from replacing the last
      // known-good owner-scoped backup.
      await this.guest.suspend(session, false).catch(() => undefined);
      throw error;
    }
  }

  get(owner: OwnerChatKey, sessionRef: string): RuntimeSession | undefined {
    return this.sessions.get(runtimeKey(owner, sessionRef));
  }

  async suspend(owner: OwnerChatKey, sessionRef: string): Promise<void> {
    const key = runtimeKey(owner, sessionRef);
    const runtime = this.sessions.get(key);
    if (runtime) {
      runtime.stop();
      await runtime.waitForIdle();
      await runtime.checkpoint();
      this.sessions.delete(key);
    }
    const session = this.store.getSession(owner, sessionRef);
    if (session) await this.guest.suspend(session);
  }

  async activate(session: SessionRecord, slot: SlotRecord): Promise<{ releaseId: string }> {
    return await this.activation.activate(session, slot);
  }

  async deactivate(session: SessionRecord): Promise<void> {
    const slot = this.store.slotForOwner(session.owner);
    if (!slot || slot.sessionRef !== session.sessionRef) return;
    await this.activation.deactivate(session, slot);
  }

  async stage(session: SessionRecord, event: InboundEvent): Promise<void> {
    const slot = this.store.slotForOwner(session.owner);
    if (!slot || slot.sessionRef !== session.sessionRef) throw new Error("staging session has no matching slot");
    if (!this.get(session.owner, session.sessionRef)) {
      await this.open(session, slot);
      this.store.setSlotHealth(slot.id, "healthy");
    }
    await this.guest.deliverStaging(session, slot, event);
  }

  async publish(session: SessionRecord): Promise<{ url: string; number: number; state: "open" }> {
    const slot = this.store.slotForOwner(session.owner);
    if (!slot || slot.sessionRef !== session.sessionRef) throw new Error("publication session has no matching slot");
    return await this.publisher.publish(session, slot);
  }

  async reconcilePullRequests(): Promise<void> {
    const terminal = await this.publisher.reconcile();
    for (const update of terminal) {
      const session = this.store.getSession(update.owner, update.sessionRef);
      if (!session || session.state !== "open_pr") continue;
      await this.suspend(update.owner, update.sessionRef);
      this.store.updateSession(update.owner, update.sessionRef, { state: update.state, slotId: null });
      this.store.releaseSlot(update.owner, update.sessionRef);
      await this.syncPolicies();
    }
  }

  private async syncSlotPolicy(slot: SlotRecord): Promise<void> {
    if (!slot.owner) {
      await this.guest.updateSlotPolicy(slot, {
        inbound: { enabled: false, user_deny: [] },
        outbound: {
          user_allow: this.store.accessRules("outbound", "user", "allow"),
          user_deny: this.store.accessRules("outbound", "user", "deny"),
          group_allow: this.store.accessRules("outbound", "group", "allow"),
          group_deny: this.store.accessRules("outbound", "group", "deny"),
        },
      });
      return;
    }
    const [kind, id] = slot.owner.split(":", 2) as ["private" | "group", string];
    const ownerDecision = this.store.accessDecision("inbound", kind === "private" ? "user" : "group", id);
    const session = slot.sessionRef ? this.store.getSession(slot.owner, slot.sessionRef) : undefined;
    const release = session?.stagingReleaseId
      ? this.store.stagingRelease(slot.owner, slot.sessionRef!, session.stagingReleaseId)
      : undefined;
    const inboundEnabled = this.store.getSetting("kill_switch") !== "on"
      && ownerDecision === "allow"
      && release?.state === "healthy";
    await this.guest.updateSlotPolicy(slot, {
      inbound: {
        enabled: inboundEnabled,
        user_deny: this.store.accessRules("inbound", "user", "deny"),
      },
      outbound: {
        user_allow: this.store.accessRules("outbound", "user", "allow"),
        user_deny: this.store.accessRules("outbound", "user", "deny"),
        group_allow: this.store.accessRules("outbound", "group", "allow"),
        group_deny: this.store.accessRules("outbound", "group", "deny"),
      },
    });
  }

  async syncPolicies(): Promise<void> {
    const failures: unknown[] = [];
    for (const slot of this.store.slots()) {
      try {
        await this.syncSlotPolicy(slot);
      } catch (error) {
        failures.push(error);
      }
    }
    if (failures.length > 0) throw new Error(`failed to sync ${failures.length} slot policies`);
  }

  async shutdown(): Promise<void> {
    const active: Array<{ runtime: PiSession; session: SessionRecord; slot: SlotRecord }> = [];
    for (const slot of this.store.slots()) {
      if (!slot.owner || !slot.sessionRef) continue;
      const runtime = this.sessions.get(runtimeKey(slot.owner, slot.sessionRef));
      const session = this.store.getSession(slot.owner, slot.sessionRef);
      if (runtime && session) active.push({ runtime, session, slot });
    }
    for (const { runtime } of active) runtime.stop();
    for (const { runtime } of active) await runtime.waitForIdle();
    for (const { runtime, session, slot } of active) {
      await runtime.checkpoint();
      await this.guest.checkpointWorkspace(session, slot);
    }
    this.sessions.clear();
    await this.guest.close();
  }
}
