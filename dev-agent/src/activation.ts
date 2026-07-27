import type { GuestBroker } from "./guest-broker.ts";
import { Store } from "./store.ts";
import type { SessionRecord, SlotRecord } from "./types.ts";
import { opaqueId } from "./util.ts";

interface ValidationResult {
  command: string;
  ok: boolean;
  output: string;
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function selectTargetedTests(plugins: string[], availableTests: string[], changedTests: string[]): string[] {
  const pluginNames = plugins.map((plugin) => plugin.split("/").at(-1)?.replace(/^_+/, "")).filter(Boolean) as string[];
  const changed = new Set(changedTests);
  return [...new Set(availableTests.filter((path) => {
    if (!/^tests\/(?:.*\/)?test[^/]*\.py$/.test(path)) return false;
    if (changed.has(path)) return true;
    const filename = path.split("/").at(-1) ?? "";
    return pluginNames.some((name) =>
      filename === `test_${name}.py`
      || filename.startsWith(`test_${name}_`)
      || path.startsWith(`tests/${name}/`),
    );
  }))].sort();
}

export class ActivationManager {
  constructor(private readonly store: Store, private readonly guest: GuestBroker) {}

  private async changedPlugins(session: SessionRecord, slot: SlotRecord): Promise<string[]> {
    const result = await this.guest.runTrusted(
      session,
      slot,
      `git diff --name-only ${shellQuote(session.baseSha)} -- src/plugins && git ls-files --others --exclude-standard -- src/plugins`,
    );
    if (!result.ok) throw new Error(`failed to discover staged plugins: ${result.stderr}`);
    const plugins = new Set<string>();
    for (const line of result.stdout.split(/\r?\n/)) {
      const match = line.match(/^src\/plugins\/([^/]+)\//);
      if (match?.[1]) plugins.add(`src/plugins/${match[1]}`);
    }
    if (plugins.size === 0) throw new Error("no changed NoneBot plugins were found under src/plugins");
    return [...plugins].sort();
  }

  private async validate(session: SessionRecord, slot: SlotRecord, plugins: string[]): Promise<ValidationResult[]> {
    await this.guest.writeWorkspaceFile(
      session,
      slot,
      ".dev-agent/plugins.json",
      `${JSON.stringify(plugins, null, 2)}\n`,
    );
    const discovery = await this.guest.runTrusted(
      session,
      slot,
      `find tests -type f -name 'test*.py' -print; ` +
      `printf '\\n--changed--\\n'; ` +
      `git diff --name-only ${shellQuote(session.baseSha)} -- tests; ` +
      `git ls-files --others --exclude-standard -- tests`,
    );
    if (!discovery.ok) throw new Error(`failed to discover targeted tests: ${discovery.stderr}`);
    const [availableRaw = "", changedRaw = ""] = discovery.stdout.split(/\r?\n--changed--\r?\n/, 2);
    const targetedTests = selectTargetedTests(
      plugins,
      availableRaw.split(/\r?\n/).filter(Boolean),
      changedRaw.split(/\r?\n/).filter(Boolean),
    );
    if (targetedTests.length === 0) {
      throw new Error(`no targeted tests found for ${plugins.join(", ")}; add or update a test under tests/`);
    }
    const commands = [
      "python -m compileall -q src /opt/ttd-dev-agent/staging_bot.py",
      `HOST=127.0.0.1 PORT=1 LOCALSTORE_DATA_DIR=${shellQuote(`${session.workspace}/data`)} ` +
        "TTD_STAGING_PLUGIN_MANIFEST=.dev-agent/plugins.json python /opt/ttd-dev-agent/staging_bot.py validate",
      `python -m pytest -q ${targetedTests.map(shellQuote).join(" ")}`,
      "pre-commit run --all-files",
    ];
    const results: ValidationResult[] = [];
    for (const command of commands) {
      const result = await this.guest.runTrusted(session, slot, command);
      const combined = [result.stdout, result.stderr].filter(Boolean).join("\n").slice(-8000);
      results.push({ command, ok: result.ok, output: combined });
      if (!result.ok) throw new Error(`${command} failed:\n${combined}`);
    }
    return results;
  }

  private runtimePort(slot: SlotRecord): number {
    return 9500 + slot.id;
  }

  private async stopRuntime(session: SessionRecord, slot: SlotRecord): Promise<void> {
    const result = await this.guest.runTrusted(
      session,
      slot,
      `pid=$(cat .dev-agent/runtime.pid 2>/dev/null || true); ` +
      `if test -n "$pid" && test "$(readlink /proc/$pid/cwd 2>/dev/null)" = ${shellQuote(session.workspace)}; then ` +
      `kill "$pid" 2>/dev/null || true; fi; rm -f .dev-agent/runtime.pid`,
    );
    if (!result.ok) throw new Error(`failed to stop staging runtime: ${result.stderr}`);
  }

  private async migrate(session: SessionRecord, slot: SlotRecord): Promise<ValidationResult> {
    const command =
      `HOST=127.0.0.1 PORT=1 LOCALSTORE_DATA_DIR=${shellQuote(`${session.workspace}/data`)} ` +
      `TTD_STAGING_PLUGIN_MANIFEST=.dev-agent/plugins.json python /opt/ttd-dev-agent/staging_bot.py migrate`;
    const migration = await this.guest.runTrusted(session, slot, command);
    const output = [migration.stdout, migration.stderr].filter(Boolean).join("\n").slice(-8000);
    if (!migration.ok) throw new Error(`isolated migrations failed: ${output}`);
    return { command: "isolated migrations", ok: true, output };
  }

  private async startRuntime(session: SessionRecord, slot: SlotRecord): Promise<void> {
    const port = this.runtimePort(slot);
    await this.stopRuntime(session, slot);
    const start = await this.guest.runTrusted(
      session,
      slot,
      `HOST=127.0.0.1 PORT=${port} LOCALSTORE_DATA_DIR=${shellQuote(`${session.workspace}/data`)} ` +
      `TTD_STAGING_PLUGIN_MANIFEST=.dev-agent/plugins.json ` +
      `setsid python /opt/ttd-dev-agent/staging_bot.py >.dev-agent/runtime.log 2>&1 & echo $! >.dev-agent/runtime.pid`,
    );
    if (!start.ok) throw new Error(`failed to start staging runtime: ${start.stderr}`);
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const health = await this.guest.runTrusted(session, slot, `curl -fsS --max-time 1 http://127.0.0.1:${port}/health`);
      if (health.ok) return;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const log = await this.guest.runTrusted(session, slot, "tail -n 40 .dev-agent/runtime.log 2>/dev/null || true");
    await this.stopRuntime(session, slot);
    const detail = [log.stdout, log.stderr].filter(Boolean).join("\n").trim().slice(-2000);
    throw new Error(`staging runtime failed its health check${detail ? `:\n${detail}` : ""}`);
  }

  async restore(session: SessionRecord, slot: SlotRecord): Promise<void> {
    if (!session.stagingReleaseId) return;
    const release = this.store.stagingRelease(session.owner, session.sessionRef, session.stagingReleaseId);
    if (!release || release.state !== "healthy") throw new Error("staging release metadata is unavailable");
    await this.guest.ensurePythonEnvironment(session, slot);
    await this.guest.ensureSlotDatabase(slot);
    await this.migrate(session, slot);
    await this.startRuntime(session, slot);
    this.store.setSlotHealth(slot.id, "healthy");
  }

  async deactivate(session: SessionRecord, slot: SlotRecord): Promise<void> {
    await this.stopRuntime(session, slot);
    this.store.setSlotHealth(slot.id, "degraded");
  }

  async activate(session: SessionRecord, slot: SlotRecord): Promise<{ releaseId: string }> {
    const releaseId = opaqueId(6);
    let validations: ValidationResult[] = [];
    let sha = session.baseSha;
    try {
      const plugins = await this.changedPlugins(session, slot);
      await this.guest.ensurePythonEnvironment(session, slot);
      validations = await this.validate(session, slot, plugins);
      await this.stopRuntime(session, slot);
      await this.guest.ensureSlotDatabase(slot, true);
      validations.push(await this.migrate(session, slot));
      await this.startRuntime(session, slot);
      const head = await this.guest.runTrusted(session, slot, "git rev-parse HEAD");
      sha = head.stdout.trim() || session.baseSha;
      this.store.setSlotHealth(slot.id, "healthy");
      this.store.saveRelease(session.owner, session.sessionRef, {
        id: releaseId,
        sha,
        state: "healthy",
        validation: validations,
      });
      await this.guest.checkpointWorkspace(session, slot);
      return { releaseId };
    } catch (error) {
      await this.stopRuntime(session, slot).catch(() => undefined);
      this.store.setSlotHealth(slot.id, "degraded");
      this.store.saveRelease(session.owner, session.sessionRef, {
        id: releaseId,
        sha,
        state: "failed",
        validation: validations,
      });
      throw error;
    }
  }
}
