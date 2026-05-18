import { test } from "node:test";
import assert from "node:assert/strict";

import { isAllowed, assertAllowed } from "../src/allowed_binaries.ts";

test("claude allowed", () => {
  assert.equal(isAllowed("claude"), true);
});
test("codex allowed", () => {
  assert.equal(isAllowed("codex"), true);
});
test("rm not allowed", () => {
  assert.equal(isAllowed("rm"), false);
});
test("absolute path rejected", () => {
  assert.equal(isAllowed("/usr/bin/claude"), false);
});
test("traversal rejected", () => {
  assert.equal(isAllowed("../claude"), false);
});
test("assertAllowed throws binary_not_allowed", () => {
  assert.throws(() => assertAllowed("rm"), /binary_not_allowed/);
});
