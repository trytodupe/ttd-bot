import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { test } from "node:test";
import { isolatedSessionCommand, workspaceArchiveExcludes } from "../src/guest-broker.ts";

const execFileAsync = promisify(execFile);

test("guest commands use owner-scoped home, cache, config, data, and temp directories", async () => {
  const parent = await mkdtemp(join(tmpdir(), "ttd-session-env-"));
  const workspace = join(parent, "workspace with ' quote");
  try {
    await execFileAsync("mkdir", ["-p", workspace]);
    const result = await execFileAsync(
      "/bin/sh",
      ["-c", isolatedSessionCommand(workspace, "env")],
      { encoding: "utf8" },
    );
    const environment = Object.fromEntries(result.stdout.trim().split("\n").map((line) => {
      const equals = line.indexOf("=");
      return [line.slice(0, equals), line.slice(equals + 1)];
    }));
    assert.equal(environment.HOME, join(workspace, ".dev-agent/home"));
    assert.equal(environment.XDG_CACHE_HOME, join(workspace, ".dev-agent/cache"));
    assert.equal(environment.UV_CACHE_DIR, join(workspace, ".dev-agent/cache/uv"));
    assert.equal(environment.TMPDIR, join(workspace, ".dev-agent/tmp"));
    assert.equal(environment.XDG_CONFIG_HOME, join(workspace, "config"));
    assert.equal(environment.XDG_DATA_HOME, join(workspace, "data"));
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("background guest commands keep setup and pid files inside the workspace", async () => {
  const parent = await mkdtemp(join(tmpdir(), "ttd-session-background-"));
  const workspace = join(parent, "workspace");
  try {
    await execFileAsync("mkdir", ["-p", workspace]);
    await execFileAsync(
      "/bin/sh",
      ["-c", isolatedSessionCommand(workspace, "sleep 0.01 & echo $! >.dev-agent/runtime.pid; wait")],
      { encoding: "utf8", cwd: parent },
    );
    assert.match(await readFile(join(workspace, ".dev-agent/runtime.pid"), "utf8"), /^\d+\n$/);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("workspace archives omit rebuildable and separately-backed-up runtime state", () => {
  assert.deepEqual(workspaceArchiveExcludes("workspace"), [
    "workspace/.venv",
    "workspace/.venv/*",
    "workspace/.dev-agent/cache",
    "workspace/.dev-agent/cache/*",
    "workspace/.dev-agent/home",
    "workspace/.dev-agent/home/*",
    "workspace/.dev-agent/tmp",
    "workspace/.dev-agent/tmp/*",
    "workspace/.dev-agent/runtime.pid",
    "workspace/.dev-agent/runtime.log",
  ]);
});
