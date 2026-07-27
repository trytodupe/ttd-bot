import { createHash, randomBytes } from "node:crypto";
import { chmod, mkdir } from "node:fs/promises";

export function opaqueId(bytes = 9): string {
  return randomBytes(bytes).toString("base64url");
}

export function taskId(): string {
  return randomBytes(6).toString("hex");
}

export function ownerNamespace(owner: string): string {
  return createHash("sha256").update(owner).digest("hex").slice(0, 24);
}

export function slugify(value: string): string {
  const slug = value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
  return slug || "task";
}

export async function privateDirectory(path: string): Promise<void> {
  await mkdir(path, { recursive: true, mode: 0o700 });
  await chmod(path, 0o700);
}

export function assertOwnerKey(value: string): asserts value is `private:${string}` | `group:${string}` {
  if (!/^(private|group):[^:]+$/.test(value)) {
    throw new Error("invalid owner chat key");
  }
}

export function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function redactError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message
    .replace(/(token|authorization|password|secret)=?\s*[^\s,;]+/gi, "$1=[redacted]")
    .slice(0, 1000);
}
