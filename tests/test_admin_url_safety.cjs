const { test } = require("node:test");
const assert = require("node:assert/strict");
const { safeHttpUrl, safeSameOriginPath } = require("../web/admin/url-safety.js");

test("safeHttpUrl rejects script schemes while preserving ordinary web links", () => {
  assert.equal(safeHttpUrl("javascript:alert(1)", "https://vskr.example"), "");
  assert.equal(safeHttpUrl("https://video.example/watch", "https://vskr.example"),
    "https://video.example/watch");
  assert.equal(safeHttpUrl("https://user:secret@video.example/watch", "https://vskr.example"), "");
  assert.equal(safeHttpUrl("", "https://vskr.example"), "");
});

test("safeSameOriginPath accepts app routes and rejects external or script URLs", () => {
  assert.equal(safeSameOriginPath("/api/videos/id/file", "https://vskr.example"),
    "/api/videos/id/file");
  assert.equal(safeSameOriginPath("https://attacker.example/file", "https://vskr.example"), "");
  assert.equal(safeSameOriginPath("javascript:alert(1)", "https://vskr.example"), "");
});
