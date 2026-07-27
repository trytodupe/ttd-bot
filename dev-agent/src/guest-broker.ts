import { execFile } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { chmod, chown, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { request } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, posix } from "node:path";
import { promisify } from "node:util";
import { VM } from "@earendil-works/gondolin";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "@earendil-works/pi-ai";
import type { Config } from "./config.ts";
import { isPublicAddress } from "./network-policy.ts";
import type { InboundEvent, SessionRecord, SlotRecord } from "./types.ts";
import { privateDirectory } from "./util.ts";

const execFileAsync = promisify(execFile);
const MAX_TOOL_OUTPUT = 50_000;
const MAX_EDIT_BYTES = 2 * 1024 * 1024;
const INGRESS_PROCESS = "(sandboxingress)";
const INGRESS_LIVE_CHECK = `
for stat in /proc/[0-9]*/stat; do
  test -r "$stat" || continue
  IFS=' ' read -r pid comm state rest < "$stat" || continue
  if test "$comm" = "${INGRESS_PROCESS}" && test "$state" != Z; then
    exit 0
  fi
done
exit 1
`;
function quote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function isolatedSessionCommand(workspace: string, command: string): string {
  const agentRoot = posix.join(workspace, ".dev-agent");
  const home = posix.join(agentRoot, "home");
  const cache = posix.join(agentRoot, "cache");
  const uvCache = posix.join(cache, "uv");
  const temporary = posix.join(agentRoot, "tmp");
  const config = posix.join(workspace, "config");
  const data = posix.join(workspace, "data");
  const directories = [home, uvCache, temporary, config, data].map(quote).join(" ");
  const environment = [
    ["HOME", home],
    ["XDG_CACHE_HOME", cache],
    ["UV_CACHE_DIR", uvCache],
    ["TMPDIR", temporary],
    ["XDG_CONFIG_HOME", config],
    ["XDG_DATA_HOME", data],
  ].map(([name, value]) => `${name}=${quote(value!)}`).join(" ");
  const pythonEnvironment = `
if test -f pyproject.toml && test -f uv.lock && \
   test "$(sha256sum pyproject.toml uv.lock | sha256sum | cut -d' ' -f1)" = "$(cat /opt/ttd-dev-agent/base-environment.sha256 2>/dev/null)"; then
  TTD_VENV=/opt/ttd-dev-agent/base-python/.venv
else
  TTD_VENV=${quote(posix.join(workspace, ".venv"))}
fi
export VIRTUAL_ENV="$TTD_VENV" UV_PROJECT_ENVIRONMENT="$TTD_VENV" PATH="$TTD_VENV/bin:$PATH"
`;
  // Keep setup in the foreground even when the requested command contains
  // `&`. Without a compound-command boundary, POSIX shell parsing backgrounds
  // the entire `cd && mkdir && export && command` chain, so follow-up commands
  // run from the slot user's home directory instead of the workspace.
  return `cd ${quote(workspace)} && mkdir -p ${directories} && export ${environment} && ${pythonEnvironment} {\n${command}\n}`;
}

export function workspaceArchiveExcludes(workspaceName: string): string[] {
  const roots = [
    `${workspaceName}/.venv`,
    `${workspaceName}/.dev-agent/cache`,
    `${workspaceName}/.dev-agent/home`,
    `${workspaceName}/.dev-agent/tmp`,
  ];
  return [
    ...roots.flatMap((path) => [path, `${path}/*`]),
    `${workspaceName}/.dev-agent/runtime.pid`,
    `${workspaceName}/.dev-agent/runtime.log`,
  ];
}

function output(stdout: string, stderr: string): string {
  const combined = [stdout, stderr].filter(Boolean).join("\n");
  return combined.length > MAX_TOOL_OUTPUT
    ? `${combined.slice(0, MAX_TOOL_OUTPUT)}\n[output truncated]`
    : combined;
}

function slotUser(slot: number, maxSlots = 3): string {
  if (!Number.isInteger(slot) || slot < 0 || slot >= maxSlots) throw new Error("invalid slot id");
  return `agent${slot}`;
}

export class GuestBroker {
  private vm: VM | undefined;
  private ingress: { close(): Promise<void>; host: string; port: number; url: string } | undefined;
  private ingressHealthCheck: Promise<void> | undefined;
  private readonly databasePasswords = new Map<number, string>();

  constructor(private readonly config: Config) {}

  private vmOptions() {
    return {
      sandbox: { imagePath: this.config.gondolinImage, autoRestart: false },
      rootfs: { mode: "cow" as const, size: "40G" },
      memory: "8G",
      cpus: 5,
      dns: { mode: "synthetic" as const, syntheticHostMapping: "per-host" as const },
      allowWebSockets: true,
      httpHooks: {
        isRequestAllowed: (request: Request) => request.url.startsWith("http://") || request.url.startsWith("https://"),
        isIpAllowed: ({ ip }: { ip: string }) => isPublicAddress(ip),
      },
      tcp: { hosts: {} },
      sessionLabel: "ttd-dev-agent",
    };
  }

  async start(): Promise<void> {
    if (this.vm) return;
    this.vm = await VM.create(this.vmOptions());
    await this.bootstrap();
    await this.ensureIngressConnector(false);
    await this.startIngress();
    await this.ensureHealthy();
  }

  private requireVm(): VM {
    if (!this.vm) throw new Error("Gondolin VM is not running");
    return this.vm;
  }

  private async bootstrap(): Promise<void> {
    const vm = this.requireVm();
    await vm.fs.writeFile(
      "/opt/ttd-dev-agent/slot_proxy.py",
      await readFile(join(import.meta.dirname, "..", "guest", "slot_proxy.py")),
    );
    await vm.fs.writeFile(
      "/opt/ttd-dev-agent/staging_bot.py",
      await readFile(join(import.meta.dirname, "..", "guest", "staging_bot.py")),
    );
    await vm.fs.writeFile(
      "/opt/ttd-dev-agent/workspace_file.py",
      await readFile(join(import.meta.dirname, "..", "guest", "workspace_file.py")),
    );
    const trustedPrograms = await vm.exec(
      "cd /opt/ttd-dev-agent/base-python && " +
      "sha256sum pyproject.toml uv.lock | sha256sum | cut -d' ' -f1 >/opt/ttd-dev-agent/base-environment.sha256 && " +
      "chmod 0444 /opt/ttd-dev-agent/base-environment.sha256 && " +
      "chmod 0555 /opt/ttd-dev-agent/slot_proxy.py /opt/ttd-dev-agent/staging_bot.py " +
      "/opt/ttd-dev-agent/workspace_file.py",
    );
    if (!trustedPrograms.ok) throw new Error(`failed to refresh trusted guest programs: ${trustedPrograms.stderr}`);
    for (let slot = 0; slot < this.config.maxSlots; slot += 1) {
      const user = slotUser(slot, this.config.maxSlots);
      const check = await vm.exec(["/usr/bin/id", "-u", user]);
      if (!check.ok) {
        const created = await vm.exec(["/usr/sbin/adduser", "-D", "-h", `/home/${user}`, "-s", "/bin/sh", user]);
        if (!created.ok) throw new Error(`failed to create ${user}: ${created.stderr}`);
      }
    }
    await vm.exec("mkdir -p /workspaces /run/ttd-dev-agent && chmod 0711 /workspaces");
    await vm.exec(
      "mkdir -p /run/ttd-dev-agent/tools /run/ttd-dev-agent/transfers && " +
      "chown root:root /run/ttd-dev-agent/tools /run/ttd-dev-agent/transfers && " +
      "chmod 0711 /run/ttd-dev-agent/tools && chmod 0700 /run/ttd-dev-agent/transfers",
    );
    for (let slot = 0; slot < this.config.maxSlots; slot += 1) {
      const user = slotUser(slot, this.config.maxSlots);
      const directory = `/run/ttd-dev-agent/tools/${user}`;
      await vm.exec(["/bin/mkdir", "-p", directory]);
      await vm.exec(["/bin/chown", `root:${user}`, directory]);
      await vm.exec(["/bin/chmod", "0710", directory]);
    }
    await this.startPostgres();
    for (let slot = 0; slot < this.config.maxSlots; slot += 1) {
      const pidFile = `/run/ttd-dev-agent/slot-${slot}.pid`;
      await vm.exec(
        `pid=$(cat ${pidFile} 2>/dev/null || true); ` +
        `if test -n "$pid" && kill -0 "$pid" 2>/dev/null; then ` +
        `kill "$pid" 2>/dev/null || true; ` +
        `i=0; while kill -0 "$pid" 2>/dev/null && test "$i" -lt 50; do sleep 0.1; i=$((i + 1)); done; ` +
        `kill -KILL "$pid" 2>/dev/null || true; ` +
        `fi; ` +
        `rm -f ${pidFile}`,
      );
      const proxyPort = 9400 + slot;
      const runtimePort = 9500 + slot;
      const started = await vm.exec(
        `TTD_SLOT=${slot} TTD_PROXY_PORT=${proxyPort} TTD_RUNTIME_PORT=${runtimePort} ` +
        `setsid /usr/bin/python3 /opt/ttd-dev-agent/slot_proxy.py >/run/ttd-dev-agent/slot-${slot}.log 2>&1 & echo $! >${pidFile}`,
      );
      if (!started.ok) throw new Error(`failed to start slot proxy ${slot}: ${started.stderr}`);
    }
  }

  private async startPostgres(): Promise<void> {
    const vm = this.requireVm();
    const data = "/var/lib/postgresql/17/data";
    const postgresUser = await vm.exec(["/usr/bin/id", "-u", "postgres"]);
    if (!postgresUser.ok) {
      const created = await vm.exec(
        "addgroup -S postgres && adduser -S -D -H -h /var/lib/postgresql -s /bin/sh -G postgres postgres",
      );
      if (!created.ok) throw new Error(`failed to create PostgreSQL service account: ${created.stderr}`);
    }
    const initialized = await vm.exec(["/usr/bin/test", "-f", `${data}/PG_VERSION`]);
    if (!initialized.ok) {
      const init = await vm.exec(
        `install -d -o postgres -g postgres -m 0700 ${quote(data)} /run/postgresql && ` +
        `su -s /bin/sh postgres -c ${quote(`initdb -D ${data} --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8`)}`,
      );
      if (!init.ok) throw new Error(`failed to initialize shared PostgreSQL: ${init.stderr}`);
      const configured = await vm.exec(
        `printf '%s\\n' "listen_addresses = '127.0.0.1'" "port = 55432" >>${quote(`${data}/postgresql.conf`)} && ` +
        `printf '%s\\n' 'host all all 127.0.0.1/32 scram-sha-256' >>${quote(`${data}/pg_hba.conf`)}`,
      );
      if (!configured.ok) throw new Error(`failed to configure shared PostgreSQL: ${configured.stderr}`);
    }
    const status = await vm.exec(
      ["/bin/su", "-s", "/bin/sh", "postgres", "-c", `pg_ctl -D ${quote(data)} status`],
    );
    if (!status.ok) {
      const started = await vm.exec(
        ["/bin/su", "-s", "/bin/sh", "postgres", "-c", `pg_ctl -D ${quote(data)} -l ${quote(`${data}/postgresql.log`)} start -w`],
      );
      if (!started.ok) throw new Error(`failed to start shared PostgreSQL: ${started.stderr}`);
    }
    for (let slot = 0; slot < this.config.maxSlots; slot += 1) {
      this.databasePasswords.set(slot, randomBytes(24).toString("hex"));
      await this.ensureDatabaseRole(slot);
    }
  }

  private databaseName(slot: number): string {
    slotUser(slot, this.config.maxSlots);
    return `agent${slot}`;
  }

  private async postgresSql(sql: string): Promise<void> {
    const result = await this.requireVm().exec(
      ["/bin/su", "-s", "/bin/sh", "postgres", "-c", `psql -v ON_ERROR_STOP=1 -p 55432 -d postgres -c ${quote(sql)}`],
    );
    if (!result.ok) throw new Error(`shared PostgreSQL command failed: ${result.stderr}`);
  }

  private async ensureDatabaseRole(slot: number): Promise<void> {
    const role = this.databaseName(slot);
    const password = this.databasePasswords.get(slot);
    if (!password) throw new Error("slot database password is unavailable");
    await this.postgresSql(
      `DO $$ BEGIN ` +
      `IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${role}') THEN ` +
      `ALTER ROLE ${role} WITH LOGIN PASSWORD '${password}'; ` +
      `ELSE CREATE ROLE ${role} LOGIN PASSWORD '${password}'; END IF; END $$;`,
    );
  }

  databaseUrl(slot: SlotRecord): string {
    const role = this.databaseName(slot.id);
    const password = this.databasePasswords.get(slot.id);
    if (!password) throw new Error("slot database is not initialized");
    return `postgresql+asyncpg://${role}:${password}@127.0.0.1:55432/${role}`;
  }

  async ensureSlotDatabase(slot: SlotRecord, reset = false): Promise<void> {
    const name = this.databaseName(slot.id);
    const exists = await this.requireVm().exec(
      ["/bin/su", "-s", "/bin/sh", "postgres", "-c", `psql -At -p 55432 -d postgres -c ${quote(`SELECT 1 FROM pg_database WHERE datname = '${name}'`)}`],
    );
    if (reset && exists.stdout.trim() === "1") {
      await this.postgresSql(`REVOKE CONNECT ON DATABASE ${name} FROM PUBLIC`);
      await this.postgresSql(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${name}' AND pid <> pg_backend_pid()`);
      await this.postgresSql(`DROP DATABASE ${name}`);
    }
    if (reset || exists.stdout.trim() !== "1") {
      await this.postgresSql(`CREATE DATABASE ${name} OWNER ${name}`);
      await this.postgresSql(`REVOKE CONNECT ON DATABASE ${name} FROM PUBLIC`);
      await this.postgresSql(`GRANT CONNECT ON DATABASE ${name} TO ${name}`);
    }
  }

  async ensurePythonEnvironment(session: SessionRecord, slot: SlotRecord): Promise<string> {
    const probe = await this.runTrusted(
      session,
      slot,
      `test -f pyproject.toml && test -f uv.lock && ` +
      `test "$(sha256sum pyproject.toml uv.lock | sha256sum | cut -d' ' -f1)" = ` +
      `"$(cat /opt/ttd-dev-agent/base-environment.sha256 2>/dev/null)"`,
    );
    if (probe.ok) return "/opt/ttd-dev-agent/base-python/.venv";
    const synced = await this.runTrusted(session, slot, "uv sync --frozen");
    if (!synced.ok) throw new Error(`uv sync --frozen failed:\n${[synced.stdout, synced.stderr].filter(Boolean).join("\n").slice(-8000)}`);
    return `${session.workspace}/.venv`;
  }

  private secretMatches(candidate: string, expected: string): boolean {
    const left = Buffer.from(candidate);
    const right = Buffer.from(expected);
    return left.length === right.length && timingSafeEqual(left, right);
  }

  private async startIngress(): Promise<void> {
    const vm = this.requireVm();
    if (this.config.slotIngressSecrets.length !== this.config.maxSlots) {
      throw new Error(`exactly ${this.config.maxSlots} TTD_DEV_SLOT_INGRESS_SECRETS are required`);
    }
    vm.setIngressRoutes(Array.from({ length: this.config.maxSlots }, (_, slot) => ({
      prefix: `/slot/${slot}`,
      port: 9400 + slot,
      stripPrefix: true,
    })));
    this.ingress = await vm.enableIngress({
      listenHost: this.config.ingressHost,
      listenPort: this.config.ingressPort,
      allowWebSockets: true,
      hooks: {
        isAllowed: (info) => {
          const match = info.route.prefix.match(/^\/slot\/(\d+)$/);
          const slot = match?.[1] ? Number(match[1]) : -1;
          const header = info.headers.authorization;
          const authorization = Array.isArray(header) ? header[0] : header;
          const candidate = authorization?.replace(/^Bearer\s+/i, "") ?? "";
          const expected = this.config.slotIngressSecrets[slot];
          return Boolean(expected && this.secretMatches(candidate, expected));
        },
      },
    });
  }

  private async ensureIngressConnector(restart: boolean): Promise<void> {
    const vm = this.requireVm();
    const running = await vm.exec(["/bin/sh", "-c", INGRESS_LIVE_CHECK]);
    if (running.ok && !restart) return;
    const command = `
if test ${restart ? "1" : "0"} -eq 1; then
  for stat in /proc/[0-9]*/stat; do
    test -r "$stat" || continue
    IFS=' ' read -r pid comm state rest < "$stat" || continue
    if test "$comm" = "${INGRESS_PROCESS}" && test "$state" != Z; then
      kill "$pid" 2>/dev/null || true
    fi
  done
fi
mkdir -p /run/ttd-dev-agent
setsid /usr/bin/sandboxingress >>/run/ttd-dev-agent/sandboxingress.log 2>&1 &
pid=$!
printf '%s\n' "$pid" >/run/ttd-dev-agent/sandboxingress.pid
i=0
while test "$i" -lt 50; do
  if test -r "/proc/$pid/stat"; then
    IFS=' ' read -r current_pid comm state rest < "/proc/$pid/stat" || true
    if test "$comm" = "${INGRESS_PROCESS}" && test "$state" != Z; then
      exit 0
    fi
  fi
  i=$((i + 1))
  sleep 0.1
done
exit 1
`;
    const started = await vm.exec(["/bin/sh", "-c", command]);
    if (!started.ok) {
      const log = await vm.exec(["/bin/sh", "-c", "tail -n 20 /run/ttd-dev-agent/sandboxingress.log 2>/dev/null || true"]);
      throw new Error(`failed to start Gondolin ingress connector${log.stdout.trim() ? `: ${log.stdout.trim().slice(0, 500)}` : ""}`);
    }
  }

  private async probeIngress(): Promise<number> {
    const ingress = this.ingress;
    const secret = this.config.slotIngressSecrets[0];
    if (!ingress || !secret) throw new Error("ingress is not configured");
    return await new Promise<number>((resolve, reject) => {
      let settled = false;
      const finish = (status: number) => {
        if (settled) return;
        settled = true;
        resolve(status);
      };
      const req = request({
        host: ingress.host,
        port: ingress.port,
        path: "/slot/0/",
        headers: {
          Authorization: `Bearer ${secret}`,
          Connection: "Upgrade",
          Upgrade: "websocket",
          "Sec-WebSocket-Version": "13",
          "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
          "X-TTD-Health-Probe": "1",
        },
      });
      req.once("upgrade", (response, socket) => {
        socket.destroy();
        finish(response.statusCode ?? 0);
      });
      req.once("response", (response) => {
        response.resume();
        finish(response.statusCode ?? 0);
      });
      req.once("error", reject);
      req.setTimeout(3_000, () => req.destroy(new Error("ingress health check timed out")));
      req.end();
    });
  }

  private async waitForIngress(attempts = 10): Promise<number> {
    let status = 0;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        status = await this.probeIngress();
      } catch {
        status = 0;
      }
      if (status === 101) return status;
      if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, 200));
    }
    return status;
  }

  async ensureHealthy(): Promise<void> {
    if (this.ingressHealthCheck) return await this.ingressHealthCheck;
    this.ingressHealthCheck = (async () => {
      await this.ensureIngressConnector(false);
      let status = await this.waitForIngress();
      if (status === 101) return;
      await this.ensureIngressConnector(true);
      status = await this.waitForIngress();
      if (status !== 101) throw new Error(`Gondolin ingress health check failed (${status || "unreachable"})`);
    })().finally(() => {
      this.ingressHealthCheck = undefined;
    });
    return await this.ingressHealthCheck;
  }

  async close(): Promise<void> {
    const vm = this.vm;
    if (!vm) return;
    this.vm = undefined;
    this.ingressHealthCheck = undefined;
    await this.ingress?.close();
    this.ingress = undefined;
    await vm.close();
  }

  private safeRelativePath(session: SessionRecord, relative: string): string {
    if (relative.includes("\0")) throw new Error("invalid path");
    const resolved = posix.resolve(session.workspace, relative || ".");
    if (resolved !== session.workspace && !resolved.startsWith(`${session.workspace}/`)) {
      throw new Error("path escapes the session workspace");
    }
    return posix.relative(session.workspace, resolved) || ".";
  }

  async run(session: SessionRecord, slot: SlotRecord, command: string, signal?: AbortSignal): Promise<{ ok: boolean; stdout: string; stderr: string; exitCode: number }> {
    const result = await this.runTrusted(session, slot, command, signal);
    return { ok: result.ok, stdout: result.stdout, stderr: result.stderr, exitCode: result.exitCode };
  }

  async runTrusted(session: SessionRecord, slot: SlotRecord, command: string, signal?: AbortSignal) {
    const vm = this.requireVm();
    const databaseUrl = this.databaseUrl(slot);
    return await vm.exec(
      [
        "/bin/su",
        "-s",
        "/bin/sh",
        slotUser(slot.id, this.config.maxSlots),
        "-c",
        isolatedSessionCommand(
          session.workspace,
          `export SQLALCHEMY_DATABASE_URL=${quote(databaseUrl)} TORTOISE_ORM_DB_URL=${quote(databaseUrl)}\n${command}`,
        ),
      ],
      signal ? { signal } : undefined,
    );
  }

  private async restoreWorkspaceArchive(session: SessionRecord): Promise<boolean> {
    const vm = this.requireVm();
    const ownerBackup = join(dirname(session.transcriptPath), "workspace.tar.gz");
    const legacyBackup = join(this.config.stateRoot, "backups", `${session.taskId}.tar.gz`);
    const guestArchive = `/run/ttd-dev-agent/transfers/restore-${randomBytes(16).toString("hex")}.tar.gz`;
    try {
      // A failed first open can leave an incomplete destination behind. Never
      // merge a checkpoint into that directory: stale files can make a bad
      // archive look usable and also prevent the fallback clone.
      await vm.exec(["/bin/rm", "-rf", session.workspace]);
      let archive: Buffer;
      try {
        archive = await readFile(ownerBackup);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        archive = await readFile(legacyBackup);
      }
      await vm.fs.writeFile(guestArchive, archive);
      await vm.exec(["/bin/chmod", "0600", guestArchive]);
      let restored;
      try {
        await vm.exec(["/bin/mkdir", "-p", dirname(session.workspace)]);
        restored = await vm.exec(["/bin/tar", "-xzf", guestArchive, "-C", dirname(session.workspace)]);
      } finally {
        await vm.fs.deleteFile(guestArchive, { force: true });
      }
      if (!restored.ok) throw new Error(`workspace recovery failed: ${restored.stderr}`);
      const exists = await vm.exec(["/usr/bin/test", "-d", `${session.workspace}/.git`]);
      if (exists.ok) return true;
      await vm.exec(["/bin/rm", "-rf", session.workspace]);
      return false;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
      throw error;
    }
  }

  async prepareWorkspace(session: SessionRecord, slot: SlotRecord, seed?: SessionRecord): Promise<void> {
    const vm = this.requireVm();
    await vm.exec(["/bin/mkdir", "-p", dirname(session.workspace)]);
    await vm.exec(["/bin/chmod", "0711", dirname(session.workspace)]);
    let exists = await vm.exec(["/usr/bin/test", "-d", `${session.workspace}/.git`]);
    let recovered = false;
    if (!exists.ok) {
      recovered = await this.restoreWorkspaceArchive(session);
      if (recovered) exists = await vm.exec(["/usr/bin/test", "-d", `${session.workspace}/.git`]);
    }
    if (!exists.ok) {
      // git refuses to clone into a non-empty destination. This is safe only
      // after both the live workspace and the owner-scoped backup failed the
      // .git validity check above.
      await vm.exec(["/bin/rm", "-rf", session.workspace]);
      if (seed) {
        const seedExists = await vm.exec(["/usr/bin/test", "-d", `${seed.workspace}/.git`]);
        if (!seedExists.ok && !await this.restoreWorkspaceArchive(seed)) {
          throw new Error("continuation source workspace is unavailable");
        }
        const cloned = await vm.exec(["/usr/bin/git", "clone", seed.workspace, session.workspace]);
        if (!cloned.ok) throw new Error(`failed to clone continuation workspace: ${cloned.stderr}`);
        const branch = await vm.exec(["/usr/bin/git", "-C", session.workspace, "checkout", "-b", session.branch, seed.branch]);
        if (!branch.ok) throw new Error(`failed to create continuation branch: ${branch.stderr}`);
      } else {
        const temp = await mkdtemp(join(tmpdir(), "ttd-agent-bundle-"));
        const bundle = join(temp, "main.bundle");
        const guestBundle = `/run/ttd-dev-agent/transfers/main-${randomBytes(16).toString("hex")}.bundle`;
        try {
          await execFileAsync("git", ["-c", `safe.directory=${this.config.repository}`, "-C", this.config.repository, "bundle", "create", bundle, "main"]);
          await vm.fs.writeFile(guestBundle, await readFile(bundle));
          await vm.exec(["/bin/chmod", "0600", guestBundle]);
          const cloned = await vm.exec(["/usr/bin/git", "clone", guestBundle, session.workspace]);
          if (!cloned.ok) throw new Error(`failed to clone local main: ${cloned.stderr}`);
          const branch = await vm.exec(["/usr/bin/git", "-C", session.workspace, "checkout", "-b", session.branch, session.baseSha]);
          if (!branch.ok) throw new Error(`failed to create agent branch: ${branch.stderr}`);
        } finally {
          await rm(temp, { recursive: true, force: true });
          await vm.fs.deleteFile(guestBundle, { force: true });
        }
      }
      await vm.exec(["/bin/mkdir", "-p", `${session.workspace}/.dev-agent`, `${session.workspace}/data`, `${session.workspace}/config`]);
      await vm.fs.writeFile(`${session.workspace}/.dev-agent/plugins.json`, "[]\n");
    }
    await vm.exec([
      "/bin/chown",
      "-R",
      `${slotUser(slot.id, this.config.maxSlots)}:${slotUser(slot.id, this.config.maxSlots)}`,
      session.workspace,
    ]);
    await vm.exec(["/bin/chmod", "0700", session.workspace]);
    await vm.fs.writeFile(`/run/ttd-dev-agent/slot-${slot.id}.json`, `${JSON.stringify({ owner: session.owner, session_ref: session.sessionRef, inbound: { enabled: false, user_deny: [] }, outbound: {} })}\n`);
    await vm.exec(["/bin/chmod", "0600", `/run/ttd-dev-agent/slot-${slot.id}.json`]);
    await this.ensureSlotDatabase(slot, true);
    await this.ensurePythonEnvironment(session, slot);
  }

  async readWorkspaceFile(
    session: SessionRecord,
    slot: SlotRecord,
    relative: string,
    signal?: AbortSignal,
  ): Promise<string> {
    const path = this.safeRelativePath(session, relative);
    const result = await this.runTrusted(
      session,
      slot,
      `/usr/bin/python3 /opt/ttd-dev-agent/workspace_file.py read ${quote(path)}`,
      signal,
    );
    if (!result.ok) throw new Error(result.stderr.trim() || "failed to read workspace file");
    return result.stdout.slice(0, MAX_TOOL_OUTPUT);
  }

  async writeWorkspaceFile(
    session: SessionRecord,
    slot: SlotRecord,
    relative: string,
    content: string,
    signal?: AbortSignal,
  ): Promise<void> {
    const path = this.safeRelativePath(session, relative);
    if (Buffer.byteLength(content) > MAX_EDIT_BYTES) throw new Error("file content is too large");
    const user = slotUser(slot.id, this.config.maxSlots);
    const payload = `/run/ttd-dev-agent/tools/${user}/write-${randomBytes(16).toString("hex")}.json`;
    await this.requireVm().fs.writeFile(payload, JSON.stringify({ content }));
    await this.requireVm().exec(["/bin/chown", `${user}:${user}`, payload]);
    await this.requireVm().exec(["/bin/chmod", "0400", payload]);
    try {
      const result = await this.runTrusted(
        session,
        slot,
        `/usr/bin/python3 /opt/ttd-dev-agent/workspace_file.py write ${quote(path)} ${quote(payload)}`,
        signal,
      );
      if (!result.ok) throw new Error(result.stderr.trim() || "failed to write workspace file");
    } finally {
      await this.requireVm().fs.deleteFile(payload, { force: true });
    }
  }

  async suspend(session: SessionRecord, checkpoint = true): Promise<void> {
    const vm = this.requireVm();
    if (session.slotId !== null) {
      const user = slotUser(session.slotId, this.config.maxSlots);
      const pidPath = `${session.workspace}/.dev-agent/runtime.pid`;
      await vm.exec(
        ["/bin/su", "-s", "/bin/sh", user, "-c", `pid=$(cat ${quote(pidPath)} 2>/dev/null || true); ` +
        `if test -n "$pid" && test "$(readlink /proc/$pid/cwd 2>/dev/null)" = ${quote(session.workspace)}; then kill "$pid" 2>/dev/null || true; fi; rm -f ${quote(pidPath)}`],
      );
      if (checkpoint) await this.checkpointWorkspace(session, { id: session.slotId } as SlotRecord);
    }
    await vm.exec(["/bin/chown", "-R", "root:root", session.workspace]);
    await vm.exec(["/bin/chmod", "0700", session.workspace]);
  }

  async checkpointWorkspace(session: SessionRecord, slot: SlotRecord): Promise<void> {
    const vm = this.requireVm();
    const archive = `/run/ttd-dev-agent/transfers/checkpoint-${randomBytes(16).toString("hex")}.tar.gz`;
    const workspaceName = posix.basename(session.workspace);
    const result = await vm.exec([
      "/bin/tar",
      "-czf",
      archive,
      ...workspaceArchiveExcludes(workspaceName).map((path) => `--exclude=${path}`),
      "-C",
      dirname(session.workspace),
      workspaceName,
    ]);
    if (!result.ok) throw new Error(`workspace backup failed: ${result.stderr}`);
    try {
      await vm.exec(["/bin/chmod", "0600", archive]);
      const backup = join(dirname(session.transcriptPath), "workspace.tar.gz");
      await privateDirectory(dirname(backup));
      await writeFile(backup, await vm.fs.readFile(archive), { mode: 0o600 });
      await chmod(backup, 0o600);
      try {
        const uid = (await stat(dirname(session.transcriptPath))).uid;
        await chown(backup, uid, uid);
      } catch (error) {
        if (process.getuid?.() === 0) throw error;
      }
    } finally {
      await vm.fs.deleteFile(archive, { force: true });
    }

  }

  tools(session: SessionRecord, slot: SlotRecord, notify: (kind: string, text: string) => void): AgentTool<any>[] {
    const readTool: AgentTool<any> = {
      name: "read_file",
      label: "Read file",
      description: "Read a UTF-8 file from the isolated workspace.",
      parameters: Type.Object({ path: Type.String() }),
      execute: async (_id, params, signal) => {
        const args = params as Record<string, unknown>;
        const text = await this.readWorkspaceFile(session, slot, String(args.path), signal);
        return { content: [{ type: "text", text }], details: {} };
      },
    };
    const editTool: AgentTool<any> = {
      name: "edit_file",
      label: "Edit file",
      description: "Replace one exact text occurrence in a workspace file.",
      parameters: Type.Object({ path: Type.String(), oldText: Type.String(), newText: Type.String() }),
      executionMode: "sequential",
      execute: async (_id, params, signal) => {
        const args = params as Record<string, unknown>;
        const path = this.safeRelativePath(session, String(args.path));
        const oldText = String(args.oldText);
        const newText = String(args.newText);
        if (Buffer.byteLength(oldText) > MAX_EDIT_BYTES || Buffer.byteLength(newText) > MAX_EDIT_BYTES) {
          throw new Error("edit text is too large");
        }
        const user = slotUser(slot.id, this.config.maxSlots);
        const payload = `/run/ttd-dev-agent/tools/${user}/edit-${randomBytes(16).toString("hex")}.json`;
        await this.requireVm().fs.writeFile(payload, JSON.stringify({ oldText, newText }));
        await this.requireVm().exec(["/bin/chown", `${user}:${user}`, payload]);
        await this.requireVm().exec(["/bin/chmod", "0400", payload]);
        try {
          const result = await this.runTrusted(
            session,
            slot,
            `/usr/bin/python3 /opt/ttd-dev-agent/workspace_file.py edit ${quote(path)} ${quote(payload)}`,
            signal,
          );
          if (!result.ok) throw new Error(result.stderr.trim() || "failed to edit workspace file");
        } finally {
          await this.requireVm().fs.deleteFile(payload, { force: true });
        }
        return { content: [{ type: "text", text: "updated" }], details: {} };
      },
    };
    const searchTool: AgentTool<any> = {
      name: "search",
      label: "Search workspace",
      description: "Search workspace files with ripgrep.",
      parameters: Type.Object({ query: Type.String(), path: Type.Optional(Type.String()) }),
      execute: async (_id, params, signal) => {
        const args = params as Record<string, unknown>;
        const path = this.safeRelativePath(session, String(args.path ?? "."));
        const result = await this.run(session, slot, `rg --line-number --hidden --glob '!.git/**' -- ${quote(String(args.query))} ${quote(path)}`, signal);
        return { content: [{ type: "text", text: output(result.stdout, result.stderr) || "no matches" }], details: { exitCode: result.exitCode } };
      },
    };
    const shellTool: AgentTool<any> = {
      name: "shell",
      label: "Run shell",
      description: "Run a shell command in the isolated VM, which has no host mounts, publication credentials, or production control path.",
      parameters: Type.Object({ command: Type.String() }),
      executionMode: "sequential",
      execute: async (_id, params, signal) => {
        const args = params as Record<string, unknown>;
        const result = await this.run(session, slot, String(args.command), signal);
        return { content: [{ type: "text", text: output(result.stdout, result.stderr) || `(exit ${result.exitCode})` }], details: { exitCode: result.exitCode } };
      },
    };
    const notifyTool: AgentTool<any> = {
      name: "notify",
      label: "Notify chat",
      description: "Send only a clarification, test milestone, activation milestone, or failure to the owning chat.",
      parameters: Type.Object({
        kind: Type.Union([Type.Literal("clarification"), Type.Literal("test"), Type.Literal("activation"), Type.Literal("failure")]),
        text: Type.String({ maxLength: 1000 }),
      }),
      execute: async (_id, params) => {
        const args = params as Record<string, unknown>;
        notify(String(args.kind), String(args.text));
        return { content: [{ type: "text", text: "notification queued" }], details: {} };
      },
    };
    return [readTool, editTool, searchTool, shellTool, notifyTool];
  }

  async deliverStaging(session: SessionRecord, slot: SlotRecord, event: InboundEvent): Promise<void> {
    // SnowLuma sends the same OneBot event to the stable per-slot proxy. This
    // host-side assertion prevents a stale controller route from targeting a
    // different slot; the proxy independently repeats the owner check.
    if (session.owner !== event.owner || slot.owner !== event.owner || slot.sessionRef !== session.sessionRef) {
      throw new Error("staging route ownership mismatch");
    }
  }

  async updateSlotPolicy(slot: SlotRecord, policy: { inbound: { enabled: boolean; user_deny: string[] }; outbound: Record<string, string[]> }): Promise<void> {
    const path = `/run/ttd-dev-agent/slot-${slot.id}.json`;
    if (!slot.owner || !slot.sessionRef) {
      await this.requireVm().fs.writeFile(path, `${JSON.stringify({
        owner: null,
        session_ref: null,
        heartbeat: Date.now() / 1000,
        inbound: { enabled: false, user_deny: [] },
        outbound: policy.outbound,
      })}\n`);
      await this.requireVm().exec(["/bin/chmod", "0600", path]);
      return;
    }
    await this.requireVm().fs.writeFile(path, `${JSON.stringify({
      owner: slot.owner,
      session_ref: slot.sessionRef,
      heartbeat: Date.now() / 1000,
      inbound: policy.inbound,
      outbound: policy.outbound,
    })}\n`);
    await this.requireVm().exec(["/bin/chmod", "0600", path]);
  }

  vmInstance(): VM {
    return this.requireVm();
  }
}
