import { test } from "node:test";
import assert from "node:assert/strict";

import { interpolate, type InterpolateContext } from "../src/interpolate.ts";

const CTX: InterpolateContext = {
  token: "mcp_xyz",
  endpoint: "https://w/api/mcp",
  session_id: "as_1",
  cli_session_id: null,
  working_dir: "/home/u/p",
  prompt_file_path: "/tmp/p.txt",
  mcp_config_path: "/tmp/c.json",
  home: "/home/u",
  dirhash: "-home-u-p",
};

test("single var substitutes", () => {
  assert.equal(interpolate("${endpoint}", CTX), "https://w/api/mcp");
});

test("multiple vars in template", () => {
  assert.equal(interpolate("${home}/x/${dirhash}", CTX), "/home/u/x/-home-u-p");
});

test("unknown var throws", () => {
  assert.throws(() => interpolate("${not_a_var}", CTX), /unknown var/);
});

test("null var (resume context) throws", () => {
  assert.throws(
    () => interpolate("${cli_session_id}", CTX),
    /unset in this context/,
  );
});
