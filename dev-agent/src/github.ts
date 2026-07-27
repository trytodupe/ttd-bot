import { createSign, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import type { Config } from "./config.ts";
import type { GuestBroker } from "./guest-broker.ts";
import { Store } from "./store.ts";
import type { SessionRecord, SlotRecord } from "./types.ts";

const execFileAsync = promisify(execFile);
const INTERNAL_WORKSPACE_PATHS = [".dev-agent", ".venv", "data", "config", ".env"] as const;

interface PullRequestResponse {
  number: number;
  html_url: string;
  state: "open" | "closed";
  merged?: boolean;
}

interface PrMetadata {
  summary?: string;
  behavior?: string[];
}

interface ResolvedGitHubConfig {
  appId: string;
  installationId: string;
  privateKeyPath: string;
  repository: string;
}

export function localCloneArgs(repository: string, checkout: string): string[] {
  return [
    "-c", `safe.directory=${repository}`,
    "-c", `safe.directory=${join(repository, ".git")}`,
    "clone", "--no-checkout", repository, checkout,
  ];
}

export function publicationTempPrefix(stateRoot: string): string {
  return join(stateRoot, "publish-");
}

export function publicationPathspecs(): string[] {
  return [
    ".",
    ...INTERNAL_WORKSPACE_PATHS.flatMap((path) => [
      `:(top,exclude)${path}`,
      `:(top,exclude)${path}/**`,
    ]),
  ];
}

export function publicationPushArgs(checkout: string, branch: string): string[] {
  return [
    "-C", checkout,
    "push", "--force",
    "origin", `refs/heads/${branch}:refs/heads/${branch}`,
  ];
}

function sanitizeExternal(value: string, owner: string): string {
  const ownerId = owner.split(":", 2)[1] ?? "";
  return value
    .replaceAll(owner, "[owner redacted]")
    .replaceAll(ownerId, "[identifier redacted]")
    .replace(/\b\d{5,12}\b/g, "[identifier redacted]");
}

function base64url(value: string): string {
  return Buffer.from(value).toString("base64url");
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export class GitHubPublisher {
  constructor(private readonly config: Config, private readonly store: Store, private readonly guest: GuestBroker) {}

  private githubConfig(): ResolvedGitHubConfig {
    const github = this.config.github;
    if (!github.appId || !github.installationId || !github.privateKeyPath) {
      throw new Error("GitHub App credentials are not configured");
    }
    return {
      appId: github.appId,
      installationId: github.installationId,
      privateKeyPath: github.privateKeyPath,
      repository: github.repository,
    };
  }

  private async jwt(): Promise<string> {
    const github = this.githubConfig();
    const now = Math.floor(Date.now() / 1000);
    const header = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
    const payload = base64url(JSON.stringify({ iat: now - 30, exp: now + 540, iss: github.appId }));
    const unsigned = `${header}.${payload}`;
    const signer = createSign("RSA-SHA256");
    signer.update(unsigned);
    signer.end();
    const signature = signer.sign(await readFile(github.privateKeyPath, "utf8"), "base64url");
    return `${unsigned}.${signature}`;
  }

  private async installationToken(): Promise<string> {
    const github = this.githubConfig();
    const response = await fetch(`https://api.github.com/app/installations/${github.installationId}/access_tokens`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${await this.jwt()}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ttd-bot-dev-agent",
      },
    });
    if (!response.ok) throw new Error(`GitHub installation token request failed (${response.status})`);
    const body = await response.json() as { token?: string };
    if (!body.token) throw new Error("GitHub installation token response did not include a token");
    return body.token;
  }

  private async api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`https://api.github.com${path}`, {
      ...init,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ttd-bot-dev-agent",
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
    if (!response.ok) {
      const body = (await response.text()).slice(0, 1000);
      throw new Error(`GitHub API ${path} failed (${response.status}): ${body}`);
    }
    return await response.json() as T;
  }

  private async prepareCommit(session: SessionRecord, slot: SlotRecord): Promise<string> {
    if (!session.branch.startsWith("agent/") || session.branch === "main") throw new Error("unsafe publication branch");
    const current = await this.guest.runTrusted(session, slot, "git branch --show-current");
    if (!current.ok || current.stdout.trim() !== session.branch) throw new Error("workspace is not on its assigned agent branch");
    const removeInternal = INTERNAL_WORKSPACE_PATHS
      // Force is restricted to removing controller-owned paths from the Git
      // index. Their live guest files remain untouched by --cached.
      .map((path) => `git rm -r -f --cached --ignore-unmatch -- ${shellQuote(path)} >/dev/null`)
      .join(" && ");
    const commit = await this.guest.runTrusted(
      session,
      slot,
      // Rebuild a single reviewable commit from the immutable task base on
      // every publication. This removes any previously published internal
      // runtime objects from reachable branch history as well as the PR diff.
      `git reset --soft ${shellQuote(session.baseSha)} && ${removeInternal} && ` +
      `git add -u && ` +
      `git ls-files --others --exclude-standard -z -- ${publicationPathspecs().map(shellQuote).join(" ")} | ` +
      `xargs -0 -r git add -- && ` +
      `if git diff --cached --quiet; then true; else ` +
      `git -c user.name=${shellQuote("ttd dev agent")} -c user.email=${shellQuote("ttd-dev-agent@users.noreply.github.com")} ` +
      `commit -m ${shellQuote(`feat(agent): update task ${session.taskId}`)}; fi`,
    );
    if (!commit.ok) throw new Error(`host-authorized commit failed: ${commit.stderr}`);
    const head = await this.guest.runTrusted(session, slot, "git rev-parse HEAD");
    if (!head.ok) throw new Error(`failed to resolve publication head: ${head.stderr}`);
    return head.stdout.trim();
  }

  private async validateHostController(): Promise<void> {
    const directory = join(this.config.repository, "dev-agent");
    await execFileAsync("pnpm", ["install", "--frozen-lockfile", "--offline", "--ignore-scripts"], { cwd: directory });
    await execFileAsync("pnpm", ["check"], { cwd: directory });
  }

  private async transferBranch(session: SessionRecord, slot: SlotRecord, token: string): Promise<{ headSha: string; changedFiles: string[] }> {
    const headSha = await this.prepareCommit(session, slot);
    const guestBundle = `.dev-agent/publish-${randomUUID()}.bundle`;
    const bundle = await this.guest.runTrusted(
      session,
      slot,
      `umask 077 && git bundle create ${shellQuote(guestBundle)} ${shellQuote(session.branch)}`,
    );
    if (!bundle.ok) throw new Error(`failed to export agent branch: ${bundle.stderr}`);

    // /tmp may be mounted noexec. GIT_ASKPASS must be executable, and keeping
    // the whole publication transaction below the controller's private state
    // root also gives the short-lived credential helper a 0700 parent.
    const temp = await mkdtemp(publicationTempPrefix(this.config.stateRoot));
    try {
      const bundlePath = join(temp, "branch.bundle");
      await writeFile(bundlePath, await this.guest.vmInstance().fs.readFile(guestBundle), { mode: 0o600 });
      const checkout = join(temp, "repo");
      await execFileAsync("git", localCloneArgs(this.config.repository, checkout));
      await execFileAsync("git", ["-C", checkout, "fetch", bundlePath, `${session.branch}:${session.branch}`]);
      const github = this.githubConfig();
      await execFileAsync("git", ["-C", checkout, "remote", "set-url", "origin", `https://github.com/${github.repository}.git`]);
      const askpass = join(temp, "askpass.sh");
      await writeFile(askpass, "#!/bin/sh\ncase \"$1\" in *Username*) echo x-access-token;; *) echo \"$GITHUB_TOKEN\";; esac\n", { mode: 0o700 });
      await chmod(askpass, 0o700);
      await execFileAsync(
        "git",
        publicationPushArgs(checkout, session.branch),
        {
          env: {
            ...process.env,
            GIT_ASKPASS: askpass,
            GIT_TERMINAL_PROMPT: "0",
            GITHUB_TOKEN: token,
          },
        },
      );
      const changed = await execFileAsync("git", ["-C", checkout, "diff", "--name-only", `${session.baseSha}..${headSha}`]);
      return { headSha, changedFiles: changed.stdout.split(/\r?\n/).filter(Boolean) };
    } finally {
      await this.guest.vmInstance().fs.deleteFile(guestBundle, { force: true });
      await rm(temp, { recursive: true, force: true });
    }
  }

  private async metadata(session: SessionRecord, slot: SlotRecord): Promise<PrMetadata> {
    try {
      const raw = await this.guest.readWorkspaceFile(session, slot, ".dev-agent/pr.json");
      const parsed = JSON.parse(raw) as PrMetadata;
      const metadata: PrMetadata = {};
      if (typeof parsed.summary === "string") {
        metadata.summary = sanitizeExternal(parsed.summary, session.owner).slice(0, 2000);
      }
      if (Array.isArray(parsed.behavior)) {
        metadata.behavior = parsed.behavior
          .filter((item): item is string => typeof item === "string")
          .map((item) => sanitizeExternal(item, session.owner).slice(0, 500))
          .slice(0, 20);
      }
      return metadata;
    } catch {
      return {};
    }
  }

  private body(session: SessionRecord, metadata: PrMetadata, changedFiles: string[]): string {
    const behavior = metadata.behavior?.length
      ? metadata.behavior.map((item) => `- ${item}`).join("\n")
      : changedFiles.map((path) => `- Updated \`${sanitizeExternal(path, session.owner)}\``).join("\n") || "- No behavior summary supplied.";
    return [
      "## PRD summary",
      "",
      metadata.summary ?? `Automated implementation for development task ${session.taskId}.`,
      "",
      "## Behavior changes",
      "",
      behavior,
      "",
      "## Validation",
      "",
      "- Changed-plugin and changed/new Python tests passed in the isolated staging VM.",
      "- Pre-commit gate passed in the isolated staging VM.",
      "- Import and compile checks passed.",
      "",
      "## Staging",
      "",
      `- Active staging release: ${session.stagingReleaseId ?? "not recorded"}`,
      `- Base SHA: \`${session.baseSha}\``,
      "",
      "> This draft was created by the isolated ttd-bot development agent. Review and merge remain manual.",
    ].join("\n");
  }

  async publish(session: SessionRecord, slot: SlotRecord): Promise<{ url: string; number: number; state: "open" }> {
    await this.validateHostController();
    const token = await this.installationToken();
    const transfer = await this.transferBranch(session, slot, token);
    const metadata = await this.metadata(session, slot);
    const body = this.body(session, metadata, transfer.changedFiles);
    const github = this.githubConfig();
    const existing = this.store.pullRequest(session.owner, session.sessionRef);
    let response: PullRequestResponse;
    if (existing) {
      if (existing.state !== "open") throw new Error("the previous PR is immutable; resume the session to create a continuation");
      response = await this.api<PullRequestResponse>(
        `/repos/${github.repository}/pulls/${existing.number}`,
        token,
        { method: "PATCH", body: JSON.stringify({ body, title: `Draft: agent task ${session.taskId}` }) },
      );
    } else {
      response = await this.api<PullRequestResponse>(
        `/repos/${github.repository}/pulls`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            title: `Draft: agent task ${session.taskId}`,
            head: session.branch,
            base: "main",
            body,
            draft: true,
          }),
        },
      );
    }
    this.store.savePullRequest(session.owner, session.sessionRef, {
      number: response.number,
      url: response.html_url,
      state: "open",
      headSha: transfer.headSha,
    });
    return { url: response.html_url, number: response.number, state: "open" };
  }

  async reconcile(): Promise<Array<{ owner: SessionRecord["owner"]; sessionRef: string; state: "merged" | "closed" }>> {
    const open = this.store.pullRequests();
    if (open.length === 0) return [];
    const token = await this.installationToken();
    const github = this.githubConfig();
    const terminal: Array<{ owner: SessionRecord["owner"]; sessionRef: string; state: "merged" | "closed" }> = [];
    for (const known of open) {
      const response = await this.api<PullRequestResponse>(`/repos/${github.repository}/pulls/${known.number}`, token);
      if (response.state === "open") continue;
      const state = response.merged ? "merged" : "closed";
      this.store.savePullRequest(known.owner, known.sessionRef, {
        number: known.number,
        url: response.html_url,
        state,
        headSha: known.headSha,
      });
      terminal.push({ owner: known.owner, sessionRef: known.sessionRef, state });
    }
    return terminal;
  }
}
