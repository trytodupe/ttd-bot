import assert from "node:assert/strict";
import { test } from "node:test";
import { loadConfig } from "../src/config.ts";

test("slot count defaults to three and permits only two or three", () => {
  assert.equal(loadConfig({}).maxSlots, 3);
  assert.equal(loadConfig({ TTD_DEV_MAX_SLOTS: "2" }).maxSlots, 2);
  assert.equal(loadConfig({ TTD_DEV_MAX_SLOTS: "3" }).maxSlots, 3);
  assert.throws(() => loadConfig({ TTD_DEV_MAX_SLOTS: "1" }), /must be 2 or 3/);
  assert.throws(() => loadConfig({ TTD_DEV_MAX_SLOTS: "4" }), /must be 2 or 3/);
});
