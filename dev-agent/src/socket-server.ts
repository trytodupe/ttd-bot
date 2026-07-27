import { chmod, chown, mkdir, rm } from "node:fs/promises";
import { dirname } from "node:path";
import { createServer, type Socket } from "node:net";
import type { Controller } from "./controller.ts";
import type { Store } from "./store.ts";
import type { InboundEvent } from "./types.ts";
import { redactError } from "./util.ts";

interface RequestEnvelope {
  id: string;
  operation: string;
  payload: Record<string, unknown>;
}

const MAX_REQUEST_BYTES = 64 * 1024 * 1024;

export class SocketServer {
  private readonly server = createServer((socket) => this.accept(socket));

  constructor(private readonly path: string, private readonly socketGid: number, private readonly controller: Controller, private readonly store: Store) {}

  async listen(): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o750 });
    await rm(this.path, { force: true });
    await new Promise<void>((resolve, reject) => {
      this.server.once("error", reject);
      this.server.listen(this.path, () => {
        this.server.off("error", reject);
        resolve();
      });
    });
    await chown(this.path, process.getuid?.() ?? 0, this.socketGid);
    await chmod(this.path, 0o660);
  }

  async close(): Promise<void> {
    await new Promise<void>((resolve, reject) => this.server.close((error) => error ? reject(error) : resolve()));
    await rm(this.path, { force: true });
  }

  private accept(socket: Socket): void {
    socket.setEncoding("utf8");
    let buffer = "";
    socket.on("data", (chunk: string) => {
      buffer += chunk;
      if (Buffer.byteLength(buffer) > MAX_REQUEST_BYTES) {
        socket.destroy(new Error("request too large"));
        return;
      }
      const newline = buffer.indexOf("\n");
      if (newline < 0) return;
      const line = buffer.slice(0, newline);
      buffer = "";
      void this.respond(socket, line);
    });
  }

  private async respond(socket: Socket, line: string): Promise<void> {
    let id = "";
    try {
      const request = JSON.parse(line) as RequestEnvelope;
      id = String(request.id ?? "");
      if (!id || typeof request.operation !== "string" || typeof request.payload !== "object") {
        throw new Error("invalid request envelope");
      }
      const result = await this.dispatch(request.operation, request.payload);
      socket.end(`${JSON.stringify({ id, ok: true, result })}\n`);
    } catch (error) {
      socket.end(`${JSON.stringify({ id, ok: false, error: redactError(error) })}\n`);
    }
  }

  private async dispatch(operation: string, payload: Record<string, unknown>): Promise<unknown> {
    switch (operation) {
      case "inbound.route":
        return this.controller.route(payload as unknown as InboundEvent);
      case "outbox.poll":
        return { items: this.store.pollOutbox(String(payload.bot_id ?? ""), Number(payload.limit ?? 20)) };
      case "outbox.ack":
        this.store.ackOutbox(Number(payload.outbox_id), String(payload.bot_id ?? ""), String(payload.message_id ?? ""));
        return {};
      case "outbox.nack":
        this.store.nackOutbox(Number(payload.outbox_id), String(payload.bot_id ?? ""), String(payload.error ?? "delivery failed"));
        return {};
      default:
        throw new Error("unsupported operation");
    }
  }
}
