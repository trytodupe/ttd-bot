import assert from "node:assert/strict";
import { test } from "node:test";
import {
  localCloneArgs,
  publicationPathspecs,
  publicationPushArgs,
  publicationTempPrefix,
} from "../src/github.ts";

test("local publication clone trusts both the worktree and its git directory", () => {
  assert.deepEqual(localCloneArgs("/srv/ttd-bot", "/tmp/publish/repo"), [
    "-c", "safe.directory=/srv/ttd-bot",
    "-c", "safe.directory=/srv/ttd-bot/.git",
    "clone", "--no-checkout", "/srv/ttd-bot", "/tmp/publish/repo",
  ]);
});

test("publication temp files live below the private executable state root", () => {
  assert.equal(publicationTempPrefix("/var/lib/ttd-dev-agent"), "/var/lib/ttd-dev-agent/publish-");
});

test("publication excludes all controller-owned workspace state", () => {
  assert.deepEqual(publicationPathspecs(), [
    ".",
    ":(top,exclude).dev-agent",
    ":(top,exclude).dev-agent/**",
    ":(top,exclude).venv",
    ":(top,exclude).venv/**",
    ":(top,exclude)data",
    ":(top,exclude)data/**",
    ":(top,exclude)config",
    ":(top,exclude)config/**",
    ":(top,exclude).env",
    ":(top,exclude).env/**",
  ]);
});

test("publication history replacement is constrained to the assigned agent branch", () => {
  assert.deepEqual(publicationPushArgs("/private/repo", "agent/task-feature"), [
    "-C", "/private/repo",
    "push", "--force",
    "origin", "refs/heads/agent/task-feature:refs/heads/agent/task-feature",
  ]);
});
