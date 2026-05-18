import { test } from "node:test";
import assert from "node:assert/strict";

import { parseLaunchUri } from "../src/uri.ts";

test("parses run URI", () => {
  const r = parseLaunchUri(
    "agentwiki://run?code=lc_xyz&tool=claude-code&endpoint=https%3A%2F%2Fw%2Fapi%2Fmcp",
  );
  assert.equal(r.action, "run");
  if (r.action !== "run") return;
  assert.equal(r.code, "lc_xyz");
  assert.equal(r.tool, "claude-code");
  assert.equal(r.endpoint, "https://w/api/mcp");
});

test("parses probe URI", () => {
  const r = parseLaunchUri(
    "agentwiki://probe?nonce=n123&endpoint=https%3A%2F%2Fw",
  );
  assert.equal(r.action, "probe");
  if (r.action !== "probe") return;
  assert.equal(r.nonce, "n123");
});

test("rejects unknown scheme", () => {
  assert.throws(() => parseLaunchUri("https://example.com"));
});

test("rejects unknown action", () => {
  assert.throws(() => parseLaunchUri("agentwiki://destroy?x=1"));
});

test("rejects missing params", () => {
  assert.throws(() => parseLaunchUri("agentwiki://run?code=lc"));
});
