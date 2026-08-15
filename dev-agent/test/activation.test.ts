import assert from "node:assert/strict";
import { test } from "node:test";
import { ActivationManager, selectTargetedTests } from "../src/activation.ts";
import type { SessionRecord, SlotRecord } from "../src/types.ts";

test("publication selects changed tests and tests belonging to changed plugins", () => {
  const selected = selectTargetedTests(
    ["src/plugins/dev_agent_gateway", "src/plugins/_quickmatch_query"],
    [
      "tests/test_dev_agent_gateway.py",
      "tests/test_quickmatch_query.py",
      "tests/test_unrelated.py",
      "tests/nested/test_behavior.py",
      "tests/conftest.py",
    ],
    ["tests/nested/test_behavior.py", "tests/conftest.py"],
  );
  assert.deepEqual(selected, [
    "tests/nested/test_behavior.py",
    "tests/test_dev_agent_gateway.py",
    "tests/test_quickmatch_query.py",
  ]);
});

test("publication does not silently select unrelated plugin tests", () => {
  assert.deepEqual(
    selectTargetedTests(["src/plugins/new_plugin"], ["tests/test_unrelated.py"], []),
    [],
  );
});

test("publication redirects bytecode for the root-owned staging script", async () => {
  const commands: string[] = [];
  const head = "b".repeat(40);
  const guest = {
    ensurePythonEnvironment: async () => "/opt/ttd-dev-agent/base-python/.venv",
    ensureSlotDatabase: async () => undefined,
    writeWorkspaceFile: async () => undefined,
    checkpointWorkspace: async () => undefined,
    runTrusted: async (_session: SessionRecord, _slot: SlotRecord, command: string) => {
      commands.push(command);
      if (command.includes("-- src/plugins")) {
        return {
          ok: true,
          stdout: "src/plugins/example/__init__.py\n",
          stderr: "",
          exitCode: 0,
        };
      }
      if (command.startsWith("find tests")) {
        return {
          ok: true,
          stdout: "tests/test_example.py\n\n--changed--\ntests/test_example.py\n",
          stderr: "",
          exitCode: 0,
        };
      }
      if (command === "git rev-parse HEAD") {
        return { ok: true, stdout: `${head}\n`, stderr: "", exitCode: 0 };
      }
      return { ok: true, stdout: "", stderr: "", exitCode: 0 };
    },
  };
  const store = {
    setSlotHealth: () => undefined,
    saveRelease: () => undefined,
  };
  const session = {
    owner: "private:1001",
    sessionRef: "session",
    taskId: "task",
    title: "test",
    state: "active",
    branch: "agent/task-test",
    baseSha: "a".repeat(40),
    stagingReleaseId: null,
    workspace: "/workspaces/session",
    transcriptPath: "/transcripts/session.json",
    slotId: 0,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  } satisfies SessionRecord;
  const slot = { id: 0 } as SlotRecord;

  await new ActivationManager(store as never, guest as never).activate(session, slot);

  assert.ok(commands.includes(
    "PYTHONPYCACHEPREFIX=.dev-agent/pycache python -m compileall -q src /opt/ttd-dev-agent/staging_bot.py",
  ));
});

test("cold staging restore selects the baked environment and starts one runtime", async () => {
  const commands: string[] = [];
  const guest = {
    ensurePythonEnvironment: async () => "/opt/ttd-dev-agent/base-python/.venv",
    ensureSlotDatabase: async () => undefined,
    writeWorkspaceFile: async () => undefined,
    databaseUrl: () => "postgresql+asyncpg://agent0:secret@127.0.0.1:55432/agent0",
    runTrusted: async (_session: SessionRecord, _slot: SlotRecord, command: string) => {
      commands.push(command);
      return { ok: true, stdout: "", stderr: "", exitCode: 0 };
    },
    vmInstance: () => ({
      fs: {
        writeFile: async () => undefined,
        rename: async () => undefined,
      },
    }),
  };
  const store = {
    stagingRelease: () => ({ id: "release", sha: "a".repeat(40), state: "healthy", validation: [] }),
    setSlotHealth: () => undefined,
  };
  const session = {
    owner: "private:1001",
    sessionRef: "session",
    taskId: "task",
    title: "test",
    state: "active",
    branch: "agent/task-test",
    baseSha: "a".repeat(40),
    stagingReleaseId: "release",
    workspace: "/workspaces/session",
    transcriptPath: "/transcripts/session.json",
    slotId: 0,
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  } satisfies SessionRecord;
  const slot = { id: 0 } as SlotRecord;

  await new ActivationManager(store as never, guest as never).restore(session, slot);

  assert.ok(commands.some((command) => command.includes("setsid python /opt/ttd-dev-agent/staging_bot.py")));
  assert.ok(commands.every((command) => !command.includes("runtime-blue") && !command.includes("runtime-green")));
});
