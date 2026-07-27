import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";

test("service startup uses the pinned TypeScript runner supported by current Node", async () => {
  const script = await readFile(join(import.meta.dirname, "..", "start.sh"), "utf8");

  assert.doesNotMatch(script, /--experimental-transform-types/);
  assert.match(script, /node_modules\/\.bin\/tsx/);
});
