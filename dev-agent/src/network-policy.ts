import { isIP } from "node:net";

function ipv4Number(value: string): number {
  return value.split(".").reduce((acc, part) => ((acc << 8) | Number(part)) >>> 0, 0);
}

function ipv4In(value: string, base: string, bits: number): boolean {
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
  return (ipv4Number(value) & mask) === (ipv4Number(base) & mask);
}

const BLOCKED_V4: Array<[string, number]> = [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
];

function normalizeIpv6(value: string): string {
  return value.toLowerCase().split("%", 1)[0] ?? value.toLowerCase();
}

export function isPublicAddress(value: string): boolean {
  const family = isIP(value);
  if (family === 4) return !BLOCKED_V4.some(([base, bits]) => ipv4In(value, base, bits));
  if (family !== 6) return false;
  const ip = normalizeIpv6(value);
  const mapped = ip.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mapped?.[1]) return isPublicAddress(mapped[1]);
  if (ip === "::" || ip === "::1") return false;
  if (/^f[cd]/.test(ip)) return false;
  if (/^fe[89ab]/.test(ip)) return false;
  if (ip.startsWith("ff")) return false;
  if (ip.startsWith("2001:db8")) return false;
  return true;
}

export function isHttpProtocol(value: string): boolean {
  return value === "http:" || value === "https:";
}
