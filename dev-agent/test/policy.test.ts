import assert from "node:assert/strict";
import { test } from "node:test";
import { AccessController } from "../src/access.ts";
import { isPublicAddress } from "../src/network-policy.ts";
import { StagingProxyPolicy, stripTestPrefix } from "../src/proxy-policy.ts";
import { Store } from "../src/store.ts";
import type { InboundEvent, SessionRecord } from "../src/types.ts";

test("network policy permits public addresses and blocks local/private/metadata/tailscale ranges", () => {
  for (const ip of ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.100.100.100", "::1", "fc00::1", "fe80::1"]) {
    assert.equal(isPublicAddress(ip), false, ip);
  }
  assert.equal(isPublicAddress("1.1.1.1"), true);
  assert.equal(isPublicAddress("2606:4700:4700::1111"), true);
  assert.equal(isPublicAddress("not-an-ip"), false);
});

test("test prefix stripping preserves every non-command segment", () => {
  const segments = [
    { type: "text", data: { text: "/test hello" } },
    { type: "image", data: { url: "https://example.com/a.png" } },
    { type: "text", data: { text: " tail" } },
  ];
  assert.deepEqual(stripTestPrefix(segments), [
    { type: "text", data: { text: "hello" } },
    segments[1],
    segments[2],
  ]);
});

test("staging policy repeats owner/session checks and outbound deny precedence", () => {
  const store = new Store();
  const owner = "group:2001" as const;
  store.ensureOwner(owner);
  const session: SessionRecord = {
    owner,
    sessionRef: "opaque-stage",
    taskId: "task-stage",
    title: "stage",
    state: "active",
    branch: "agent/task-stage-stage",
    baseSha: "a".repeat(40),
    workspace: "/workspaces/stage",
    transcriptPath: "/state/stage.json",
    slotId: null,
    stagingReleaseId: "release-a",
    continuationOf: null,
    createdAt: 1,
    updatedAt: 1,
  };
  store.insertSession(session);
  store.saveRelease(owner, session.sessionRef, {
    id: "release-a",
    sha: "a".repeat(40),
    state: "healthy",
    validation: [],
  });
  store.setAccessRule("inbound", "group", "2001", "allow");
  const policy = new StagingProxyPolicy(store, new AccessController(store));
  const event = { owner, chat_type: "group", group_id: "2001", user_id: "1001", route_hint: "staging" } as unknown as InboundEvent;
  assert.equal(policy.acceptsEvent(owner, session.sessionRef, event), true);
  assert.equal(policy.acceptsEvent("group:2002", session.sessionRef, event), false);
  assert.equal(policy.acceptsEvent(owner, session.sessionRef, { ...event, route_hint: "dev" }), false);
  assert.equal(policy.acceptsEvent(owner, session.sessionRef, { ...event, route_hint: "none" }), false);
  assert.equal(policy.allowAction(owner, { action: "send_group_msg", params: { group_id: 9999 } }), true);
  store.setAccessRule("outbound", "group", "9999", "deny");
  assert.equal(policy.allowAction(owner, { action: "send_group_msg", params: { group_id: 9999 } }), false);
  store.close();
});
