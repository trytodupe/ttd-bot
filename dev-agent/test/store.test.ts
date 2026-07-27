import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { AccessController } from "../src/access.ts";
import { Store } from "../src/store.ts";
import type { OwnerChatKey, SessionRecord } from "../src/types.ts";

function session(owner: OwnerChatKey, ref: string, index: number): SessionRecord {
  return {
    owner,
    sessionRef: ref,
    taskId: `task${index}`,
    title: `task ${index}`,
    state: "suspended",
    branch: `agent/task${index}-task`,
    baseSha: "a".repeat(40),
    workspace: `/workspaces/o${index}/${ref}`,
    transcriptPath: `/state/o${index}/${ref}.json`,
    slotId: null,
    stagingReleaseId: null,
    continuationOf: null,
    createdAt: index,
    updatedAt: index,
  };
}

test("session reads always require the owner composite key", () => {
  const store = new Store();
  const alice = "private:1001" as const;
  const bob = "private:1002" as const;
  store.ensureOwner(alice);
  store.ensureOwner(bob);
  store.insertSession(session(alice, "opaque-ref-a", 1));
  store.insertSession(session(bob, "opaque-ref-b", 2));

  assert.equal(store.getSession(alice, "opaque-ref-a")?.owner, alice);
  assert.equal(store.getSession(bob, "opaque-ref-a"), undefined);
  assert.deepEqual(store.listSessions(bob, 1).map((item) => item.sessionRef), ["opaque-ref-b"]);

  store.close();
});

test("three slots reject a fourth owner and reuse a chat slot when switching", () => {
  const store = new Store();
  for (let index = 0; index < 4; index += 1) {
    const owner = `group:${2000 + index}` as OwnerChatKey;
    store.ensureOwner(owner);
    store.insertSession(session(owner, `opaque-${index}`, index + 10));
    const allocated = store.allocateSlot(owner, `opaque-${index}`);
    if (index < 3) assert.equal(allocated?.id, index);
    else assert.equal(allocated, undefined);
  }

  const first = "group:2000" as const;
  store.insertSession(session(first, "opaque-switch", 30));
  assert.equal(store.allocateSlot(first, "opaque-switch")?.id, 0);
  assert.equal(store.activeSession(first)?.sessionRef, "opaque-switch");
  assert.equal(store.getSession(first, "opaque-0")?.slotId, null);
  store.close();
});

test("reducing configured slots suspends excess sessions without deleting history", () => {
  const store = new Store();
  const insertLegacySlot = store.db.prepare(
    "INSERT INTO slots(slot_id, health, updated_at) VALUES(?, 'idle', ?)",
  );
  insertLegacySlot.run(3, Date.now());
  insertLegacySlot.run(4, Date.now());
  for (let index = 0; index < 5; index += 1) {
    const owner = `group:${3000 + index}` as OwnerChatKey;
    store.ensureOwner(owner);
    const record = session(owner, `opaque-migrate-${index}`, index + 40);
    store.insertSession(record);
    store.allocateSlot(owner, record.sessionRef);
  }

  store.configureSlots(3);

  assert.deepEqual(store.slots().map((slot) => slot.id), [0, 1, 2]);
  for (let index = 3; index < 5; index += 1) {
    const owner = `group:${3000 + index}` as OwnerChatKey;
    const preserved = store.getSession(owner, `opaque-migrate-${index}`);
    assert.equal(preserved?.slotId, null);
    assert.equal(preserved?.state, "suspended");
  }
  store.close();
});

test("legacy blue-green columns are removed without losing slot state", async () => {
  const root = await mkdtemp(`${tmpdir()}/ttd-store-`);
  const path = join(root, "controller.sqlite3");
  const legacy = new Store(path, 3);
  legacy.db.exec("ALTER TABLE slots ADD COLUMN active_color TEXT NOT NULL DEFAULT 'blue' CHECK(active_color IN ('blue','green'))");
  legacy.db.exec("ALTER TABLE staging_releases ADD COLUMN color TEXT NOT NULL DEFAULT 'blue' CHECK(color IN ('blue','green'))");
  legacy.close();

  const migrated = new Store(path, 3);
  const columns = (table: string) =>
    (migrated.db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).map((row) => row.name);
  assert.equal(columns("slots").includes("active_color"), false);
  assert.equal(columns("staging_releases").includes("color"), false);
  assert.equal(migrated.slots().length, 3);
  migrated.close();
  await rm(root, { recursive: true, force: true });
});

test("inbound is default-deny and blacklists override allowlists", () => {
  const store = new Store();
  const access = new AccessController(store);
  const privateEvent = {
    user_id: "1001",
    chat_type: "private",
    group_id: null,
  } as Parameters<AccessController["inbound"]>[0];
  assert.equal(access.inbound(privateEvent), false);
  store.setAccessRule("inbound", "user", "1001", "allow");
  assert.equal(access.inbound(privateEvent), true);
  store.setAccessRule("inbound", "user", "1001", "deny");
  assert.equal(access.inbound(privateEvent), false);

  const groupEvent = { user_id: "1001", chat_type: "group", group_id: "2001" } as Parameters<AccessController["inbound"]>[0];
  store.setAccessRule("inbound", "group", "2001", "allow");
  assert.equal(access.inbound(groupEvent), false, "user blacklist overrides group allowlist");

  assert.equal(access.outbound("group", "9999"), true, "empty outbound allowlist is unrestricted");
  store.setAccessRule("outbound", "group", "2001", "allow");
  assert.equal(access.outbound("group", "9999"), false);
  store.setAccessRule("outbound", "group", "9999", "deny");
  assert.equal(access.outbound("group", "9999"), false);
  store.close();
});

test("outbox acknowledgements are bot scoped", () => {
  const store = new Store();
  const owner = "private:1001" as const;
  store.ensureOwner(owner);
  store.insertSession(session(owner, "opaque-outbox", 50));
  const id = store.enqueue({
    owner,
    sessionRef: "opaque-outbox",
    botId: "bot-a",
    chatType: "private",
    destinationId: "1001",
    message: "question",
    origin: "agent",
  });
  assert.equal(store.pollOutbox("bot-b", 20).length, 0);
  assert.equal(store.pollOutbox("bot-a", 20)[0]?.id, id);
  assert.throws(() => store.ackOutbox(id, "bot-b", "message-1"), /lease not found/);
  store.ackOutbox(id, "bot-a", "message-1");
  store.close();
});
