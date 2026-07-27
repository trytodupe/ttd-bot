import { resolve } from "node:path";
import type { ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { ModelSettings } from "./types.ts";
import { parsePositiveInt } from "./util.ts";

const THINKING_LEVELS = new Set<ThinkingLevel>([
  "off",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);

export interface Config {
  enabled: boolean;
  socketPath: string;
  socketGid: number;
  stateRoot: string;
  repository: string;
  maxSlots: number;
  ownerUidBase: number;
  model: ModelSettings;
  modelApiKey: string | undefined;
  gondolinImage: string;
  ingressHost: string;
  ingressPort: number;
  slotIngressSecrets: string[];
  github: {
    appId: string | undefined;
    installationId: string | undefined;
    privateKeyPath: string | undefined;
    repository: string;
  };
}

function bool(value: string | undefined, fallback = false): boolean {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const thinking = (env.TTD_DEV_MODEL_THINKING ?? "high") as ThinkingLevel;
  if (!THINKING_LEVELS.has(thinking)) throw new Error("invalid TTD_DEV_MODEL_THINKING");
  const maxSlots = parsePositiveInt(env.TTD_DEV_MAX_SLOTS, 3);
  if (maxSlots < 2 || maxSlots > 3) throw new Error("TTD_DEV_MAX_SLOTS must be 2 or 3");
  const secrets = (env.TTD_DEV_SLOT_INGRESS_SECRETS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);

  return {
    enabled: bool(env.TTD_DEV_AGENT_ENABLED),
    socketPath: env.TTD_DEV_SOCKET_PATH ?? "/run/ttd-dev-agent/controller.sock",
    socketGid: Number.parseInt(env.TTD_DEV_SOCKET_GID ?? String(process.getgid?.() ?? 0), 10),
    stateRoot: resolve(env.TTD_DEV_STATE_ROOT ?? "/var/lib/ttd-dev-agent"),
    repository: resolve(env.TTD_DEV_REPOSITORY ?? process.cwd()),
    maxSlots,
    ownerUidBase: parsePositiveInt(env.TTD_DEV_OWNER_UID_BASE, 250_000),
    model: {
      provider: env.TTD_DEV_MODEL_PROVIDER ?? "xiaomi-token-plan-cn",
      model: env.TTD_DEV_MODEL_ID ?? "mimo-v2.5-pro",
      thinkingLevel: thinking,
    },
    modelApiKey: env.TTD_DEV_MODEL_API_KEY,
    gondolinImage: env.TTD_DEV_GONDOLIN_IMAGE ?? "ttd-dev-agent:latest",
    ingressHost: env.TTD_DEV_INGRESS_HOST ?? "127.0.0.1",
    ingressPort: Number.parseInt(env.TTD_DEV_INGRESS_PORT ?? "0", 10),
    slotIngressSecrets: secrets,
    github: {
      appId: env.TTD_DEV_GITHUB_APP_ID,
      installationId: env.TTD_DEV_GITHUB_INSTALLATION_ID,
      privateKeyPath: env.TTD_DEV_GITHUB_PRIVATE_KEY_PATH,
      repository: env.TTD_DEV_GITHUB_REPOSITORY ?? "trytodupe/ttd-bot",
    },
  };
}
