import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer, request } from "node:http";
import type { Socket } from "node:net";
import { networkInterfaces, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { loadConfig } from "../../src/config.ts";
import { GuestBroker } from "../../src/guest-broker.ts";
import { isPublicAddress } from "../../src/network-policy.ts";
import type { SessionRecord, SlotRecord } from "../../src/types.ts";

function handshake(host: string, port: number, path: string, token?: string): Promise<number> {
  return new Promise((resolveStatus, reject) => {
    const req = request({
      host,
      port,
      path,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
      },
    });
    req.once("upgrade", (response, socket) => {
      socket.destroy();
      resolveStatus(response.statusCode ?? 0);
    });
    req.once("response", (response) => {
      response.resume();
      resolveStatus(response.statusCode ?? 0);
    });
    req.once("error", reject);
    req.setTimeout(5_000, () => req.destroy(new Error("handshake timed out")));
    req.end();
  });
}

function openWebSocket(host: string, port: number, path: string, token: string): Promise<Socket> {
  return new Promise((resolveSocket, reject) => {
    const req = request({
      host,
      port,
      path,
      headers: {
        Authorization: `Bearer ${token}`,
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
      },
    });
    req.once("upgrade", (response, socket) => {
      if (response.statusCode !== 101) {
        socket.destroy();
        reject(new Error(`unexpected upgrade status ${response.statusCode}`));
        return;
      }
      resolveSocket(socket);
    });
    req.once("response", (response) => reject(new Error(`unexpected response ${response.statusCode}`)));
    req.once("error", reject);
    req.setTimeout(5_000, () => req.destroy(new Error("handshake timed out")));
    req.end();
  });
}

function websocketText(value: string): Buffer {
  const payload = Buffer.from(value);
  if (payload.length >= 65_536) throw new Error("integration frame is too large");
  const mask = randomBytes(4);
  const headerLength = payload.length < 126 ? 6 : 8;
  const frame = Buffer.alloc(headerLength + payload.length);
  frame[0] = 0x81;
  if (payload.length < 126) {
    frame[1] = 0x80 | payload.length;
    mask.copy(frame, 2);
  } else {
    frame[1] = 0x80 | 126;
    frame.writeUInt16BE(payload.length, 2);
    mask.copy(frame, 4);
  }
  for (let index = 0; index < payload.length; index += 1) {
    frame[headerLength + index] = payload[index]! ^ mask[index % 4]!;
  }
  return frame;
}

test("fresh VM exposes configured ingress, base Python, and isolated slot databases", { timeout: 360_000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), "ttd-gondolin-integration-"));
  const privateIp = Object.values(networkInterfaces())
    .flat()
    .find((address) =>
      address?.family === "IPv4"
      && !address.internal
      && !isPublicAddress(address.address),
    )?.address;
  assert.ok(privateIp, "host has no private IPv4 address for the egress-denial probe");
  let privateHits = 0;
  const privateServer = createServer((_request, response) => {
    privateHits += 1;
    response.end("reachable");
  });
  await new Promise<void>((resolveListen, reject) => {
    privateServer.once("error", reject);
    privateServer.listen(0, privateIp, () => {
      privateServer.off("error", reject);
      resolveListen();
    });
  });
  const privateAddress = privateServer.address();
  assert.ok(privateAddress && typeof privateAddress === "object");
  const responsivePrivateUrl = `http://${privateIp}:${privateAddress.port}`;
  const secrets = ["slot0", "slot1", "slot2"];
  const config = loadConfig({
    TTD_DEV_STATE_ROOT: root,
    TTD_DEV_REPOSITORY: resolve(".."),
    TTD_DEV_GONDOLIN_IMAGE: process.env.TTD_DEV_GONDOLIN_IMAGE ?? "ttd-dev-agent:latest",
    TTD_DEV_INGRESS_HOST: "127.0.0.1",
    TTD_DEV_INGRESS_PORT: "0",
    TTD_DEV_SLOT_INGRESS_SECRETS: secrets.join(","),
  });
  let broker = new GuestBroker(config);
  try {
    await broker.start();
    let ingress = (broker as unknown as { ingress: { host: string; port: number } }).ingress;
    const networkProbe = await broker.vmInstance().exec(["/bin/sh", "-c", `
set -eu
python3 - <<'PY'
import asyncio
import signal
import socket

from aiohttp import ClientSession

assert socket.getaddrinfo("example.com", 443)
signal.alarm(30)

async def websocket_probe():
    async with ClientSession() as session:
        async with session.ws_connect("wss://ws.postman-echo.com/raw", timeout=10) as websocket:
            await websocket.send_str("ttd-dev-agent-probe")
            message = await asyncio.wait_for(websocket.receive(), timeout=10)
            assert message.data == "ttd-dev-agent-probe"

asyncio.run(websocket_probe())

try:
    raw = socket.create_connection(("github.com", 22), timeout=3)
    raw.settimeout(3)
    raw.sendall(b"SSH-2.0-ttd-dev-agent-probe\\r\\n")
    received = raw.recv(1)
    raw.close()
except OSError:
    pass
else:
    assert received == b"", "unmapped SSH/raw TCP returned data"

udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.settimeout(3)
try:
    packet = bytearray(48)
    packet[0] = 0x1b
    target = socket.getaddrinfo("time.cloudflare.com", 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    udp.sendto(packet, target)
    udp.recv(1)
except OSError:
    pass
else:
    raise AssertionError("public NTP/non-DNS UDP returned a response")
finally:
    udp.close()
signal.alarm(0)
PY
curl -fsS --retry 3 --retry-all-errors --retry-delay 1 --max-time 10 http://example.com >/dev/null
curl -fsS --retry 3 --retry-all-errors --retry-delay 1 --max-time 10 https://example.com >/dev/null
for target in \
  ${responsivePrivateUrl} \
  http://127.0.0.1:8901 \
  http://10.0.0.1 \
  http://192.168.0.1 \
  http://169.254.169.254 \
  http://172.17.0.1 \
  http://100.64.0.1 \
  'http://[::1]:8901' \
  'http://[fe80::1]'; do
  if curl -fsS --connect-timeout 1 --max-time 2 "$target" >/dev/null 2>&1; then
    echo "blocked destination was reachable: $target" >&2
    exit 1
  fi
done
`]);
    assert.equal(networkProbe.ok, true, networkProbe.stderr);
    assert.equal(privateHits, 0, "guest reached the responsive private host endpoint");
    const filterProbe = await broker.vmInstance().exec(["/bin/sh", "-c", `
TTD_SLOT=0 TTD_PROXY_PORT=9400 TTD_RUNTIME_PORT=9500 python3 - <<'PY'
import importlib.util
import time

spec = importlib.util.spec_from_file_location("slot_proxy_probe", "/opt/ttd-dev-agent/slot_proxy.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

private = {"owner": "private:1001", "heartbeat": time.time(), "inbound": {"enabled": True, "user_deny": []}}
module.config = lambda: private
reply = {"message_type": "private", "user_id": 1001, "message": [{"type": "reply", "data": {"id": "1"}}, {"type": "text", "data": {"text": "follow up"}}]}
assert not module.allow_event(reply)
explicit = {"message_type": "private", "user_id": 1001, "message": [{"type": "text", "data": {"text": "/test hello"}}], "raw_message": "/test hello"}
assert module.allow_event(explicit)
assert explicit["message"][0]["data"]["text"] == "hello"
split = {"message_type": "private", "user_id": 1001, "message": [{"type": "text", "data": {"text": "/te"}}, {"type": "image", "data": {"url": "https://example.com/a.png"}}, {"type": "text", "data": {"text": "st hello"}}]}
assert not module.allow_event(split)

group = {"owner": "group:2001", "heartbeat": time.time(), "inbound": {"enabled": True, "user_deny": []}}
module.config = lambda: group
ordinary = {"message_type": "group", "group_id": 2001, "user_id": 1001, "message": [{"type": "text", "data": {"text": "hello"}}]}
assert not module.allow_event(ordinary)
explicit_group = {"message_type": "group", "group_id": 2001, "user_id": 1001, "message": [{"type": "text", "data": {"text": "/test hello"}}], "raw_message": "/test hello"}
assert module.allow_event(explicit_group)
assert explicit_group["message"][0]["data"]["text"] == "hello"
PY
`]);
    assert.equal(filterProbe.ok, true, filterProbe.stderr);
    assert.equal(await handshake(ingress.host, ingress.port, "/slot/0/"), 403);
    for (let slot = 0; slot < config.maxSlots; slot += 1) {
      assert.equal(await handshake(ingress.host, ingress.port, `/slot/${slot}/`, secrets[slot]), 101);
    }
    const workspace = "/workspaces/integration/session";
    const prepared = await broker.vmInstance().exec([
      "/bin/sh",
      "-c",
      `mkdir -p ${workspace} && cp /opt/ttd-dev-agent/base-python/pyproject.toml ${workspace}/pyproject.toml && ` +
      `cp /opt/ttd-dev-agent/base-python/uv.lock ${workspace}/uv.lock && ` +
      `chown -R agent0:agent0 /workspaces/integration && chmod 0711 /workspaces/integration && chmod 0700 ${workspace}`,
    ]);
    assert.equal(prepared.ok, true, prepared.stderr);
    const transcriptPath = join(root, "owners", "integration", "sessions", "session", "pi-transcript.json");
    await mkdir(join(root, "owners", "integration", "sessions", "session"), { recursive: true });
    const session = {
      owner: "private:1001",
      sessionRef: "integration-session",
      title: "integration",
      state: "active",
      branch: "agent/integration-task-plugin-change",
      baseSha: "a".repeat(40),
      workspace,
      transcriptPath,
      taskId: "integration-task",
      slotId: 0,
      stagingReleaseId: null,
      continuationOf: null,
      createdAt: 1,
      updatedAt: 1,
    } satisfies SessionRecord;
    const slot = { id: 0 } as SlotRecord;
    const uvCache = `${workspace}/.dev-agent/cache/uv`;
    const cacheProbe = await broker.runTrusted(
      session,
      slot,
      `test "$UV_CACHE_DIR" = '${uvCache}' && mkdir -p "$UV_CACHE_DIR" && test -w "$UV_CACHE_DIR" && uv cache dir`,
    );
    assert.equal(cacheProbe.ok, true, cacheProbe.stderr);
    assert.equal(cacheProbe.stdout.trim(), uvCache);
    const basePython = await broker.runTrusted(
      session,
      slot,
      "test \"$VIRTUAL_ENV\" = /opt/ttd-dev-agent/base-python/.venv && python -c 'import nonebot, pytest'",
    );
    assert.equal(basePython.ok, true, basePython.stderr);
    const payloadIsolation = await broker.vmInstance().exec([
      "/bin/sh",
      "-c",
      "printf secret >/run/ttd-dev-agent/tools/agent0/probe && " +
        "chown agent0:agent0 /run/ttd-dev-agent/tools/agent0/probe && " +
        "chmod 0400 /run/ttd-dev-agent/tools/agent0/probe && " +
        "! su -s /bin/sh agent1 -c 'cat /run/ttd-dev-agent/tools/agent0/probe' && " +
        "rm /run/ttd-dev-agent/tools/agent0/probe",
    ]);
    assert.equal(payloadIsolation.ok, true, payloadIsolation.stderr);
    const workspaceFiles = broker.tools(session, slot, () => undefined);
    const readTool = workspaceFiles.find((tool) => tool.name === "read_file");
    const editTool = workspaceFiles.find((tool) => tool.name === "edit_file");
    assert.ok(readTool);
    assert.ok(editTool);
    const normalFile = await broker.runTrusted(session, slot, "printf 'before\\n' >normal.txt");
    assert.equal(normalFile.ok, true, normalFile.stderr);
    await editTool.execute("edit", { path: "normal.txt", oldText: "before", newText: "after" });
    const edited = await readTool.execute("read", { path: "normal.txt" });
    assert.equal(edited.content[0]?.type, "text");
    assert.match(edited.content[0]?.type === "text" ? edited.content[0].text : "", /^after/);
    const symlink = await broker.runTrusted(
      session,
      slot,
      "ln -s /opt/ttd-dev-agent/base-environment.sha256 escaped.json",
    );
    assert.equal(symlink.ok, true, symlink.stderr);
    await assert.rejects(
      readTool.execute("read-escape", { path: "escaped.json" }),
      /workspace file operation failed/,
    );
    await assert.rejects(
      editTool.execute("edit-escape", { path: "escaped.json", oldText: "owner", newText: "changed" }),
      /workspace file operation failed/,
    );
    const trustedHashBefore = await broker.vmInstance().fs.readFile(
      "/opt/ttd-dev-agent/base-environment.sha256",
      { encoding: "utf8" },
    );
    await broker.writeWorkspaceFile(session, slot, "escaped.json", "workspace-only\n");
    assert.equal(
      await broker.vmInstance().fs.readFile("/opt/ttd-dev-agent/base-environment.sha256", { encoding: "utf8" }),
      trustedHashBefore,
    );
    const replacedLink = await readTool.execute("read-replaced", { path: "escaped.json" });
    assert.equal(
      replacedLink.content[0]?.type === "text" ? replacedLink.content[0].text : "",
      "workspace-only\n",
    );
    await broker.ensureSlotDatabase(slot, true);
    await broker.ensureSlotDatabase({ id: 1 } as SlotRecord, true);
    const database = await broker.runTrusted(session, slot, `python - <<'PY'
import asyncio
import os
import asyncpg

url = os.environ["SQLALCHEMY_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
asyncio.run(asyncpg.connect(url))
PY`);
    assert.equal(database.ok, true, database.stderr);
    const ownUrl = broker.databaseUrl(slot).replace("/agent0", "/agent1");
    const crossDatabase = await broker.runTrusted(session, slot, `python - <<'PY'
import asyncio
import asyncpg

asyncio.run(asyncpg.connect("${ownUrl.replace("postgresql+asyncpg://", "postgresql://")}"))
PY`);
    assert.equal(crossDatabase.ok, false, "slot role connected to another slot database");
    await broker.checkpointWorkspace(session, slot);
    await broker.updateSlotPolicy(
      { ...slot, owner: "private:1001", sessionRef: "integration-session" },
      { inbound: { enabled: true, user_deny: [] }, outbound: {} },
    );
    const queued = await openWebSocket(ingress.host, ingress.port, "/slot/0/", secrets[0]!);
    queued.write(websocketText(JSON.stringify({
      post_type: "message",
      message_type: "private",
      user_id: 1001,
      raw_message: "/test queued",
      message: "/test queued",
    })));
    await delay(750);
    await broker.vmInstance().fs.writeFile("/tmp/staging-probe.py", `
import asyncio
from pathlib import Path
from aiohttp import web

async def websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    message = await ws.receive()
    Path("/tmp/staging-event.json").write_text(message.data)
    await asyncio.sleep(2)
    return ws

app = web.Application()
app.router.add_get("/{tail:.*}", websocket)
web.run_app(app, host="127.0.0.1", port=9500, access_log=None)
`);
    const probeRuntime = await broker.vmInstance().exec(
      "setsid python3 /tmp/staging-probe.py >/tmp/staging-probe.log 2>&1 &",
    );
    assert.equal(probeRuntime.ok, true, probeRuntime.stderr);
    let staged = "";
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const result = await broker.vmInstance().exec("cat /tmp/staging-event.json 2>/dev/null || true");
      staged = result.stdout;
      if (staged) break;
      await delay(250);
    }
    assert.match(staged, /"message": "queued"/);
    queued.destroy();
    const stable = await openWebSocket(ingress.host, ingress.port, "/slot/0/", secrets[0]!);
    let closed = false;
    stable.once("close", () => { closed = true; });
    await delay(1_000);
    assert.equal(closed, false, "slot proxy closed while no staging runtime was available");
    stable.destroy();

    const stopped = await broker.vmInstance().exec(["/bin/sh", "-c", `
for stat in /proc/[0-9]*/stat; do
  test -r "$stat" || continue
  IFS=' ' read -r pid comm state rest < "$stat" || continue
  if test "$comm" = '(sandboxingress)' && test "$state" != Z; then kill "$pid"; fi
done
`]);
    assert.equal(stopped.ok, true);
    await broker.ensureHealthy();
    assert.equal(await handshake(ingress.host, ingress.port, "/slot/0/", secrets[0]), 101);

    await broker.close();
    broker = new GuestBroker(config);
    await broker.start();
    ingress = (broker as unknown as { ingress: { host: string; port: number } }).ingress;
    for (let slot = 0; slot < config.maxSlots; slot += 1) {
      assert.equal(await handshake(ingress.host, ingress.port, `/slot/${slot}/`, secrets[slot]), 101);
    }
  } finally {
    await broker.close().catch(() => undefined);
    privateServer.closeAllConnections();
    await new Promise<void>((resolveClose) => privateServer.close(() => resolveClose()));
    await rm(root, { recursive: true, force: true });
  }
});
