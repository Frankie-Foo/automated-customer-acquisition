import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

const source = readFileSync(new URL("../src/pwa.js", import.meta.url), "utf8").replace("import.meta.env.PROD", "true");

test("PWA waiting update is surfaced and foreground checks are throttled without reload", async () => {
  const listeners = {}, events = [];
  let checks = 0;
  const registration = { waiting: {}, addEventListener() {}, async update() { checks++; } };
  const navigator = { onLine: true, serviceWorker: { register: async () => registration } };
  const document = { visibilityState: "visible", addEventListener: (name, fn) => { listeners[name] = fn; } };
  const window = { addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent: e => events.push(e.type) };
  vm.runInNewContext(source, { navigator, document, window, Event, Date, console });
  await listeners.load();
  assert.deepEqual(events, ["salesbot:pwa-update"]);
  await listeners.visibilitychange();
  await listeners.online();
  assert.equal(checks, 1);
  navigator.onLine = false;
  await listeners.online();
  assert.equal(checks, 1);
});

test("service worker preserves unrelated caches and never intercepts API writes", async () => {
  const handlers = {}, deleted = [];
  let claimed = false;
  const self = { location: { origin: "https://qa.example" },
    clients: { claim: async () => { claimed = true; } },
    addEventListener: (name, fn) => { handlers[name] = fn; } };
  const caches = { keys: async () => ["outbound-ops-shell-v1", "outbound-ops-shell-v2-performance", "unrelated-app"],
    delete: async key => { deleted.push(key); } };
  vm.runInNewContext(readFileSync(new URL("../public/sw.js", import.meta.url), "utf8"), { self, caches, URL });
  let work;
  handlers.activate({ waitUntil: promise => { work = promise; } });
  await work;
  assert.deepEqual(deleted, ["outbound-ops-shell-v1"]);
  assert.equal(claimed, true);
  for (const [method, url] of [["GET", "https://qa.example/api/email-performance"], ["POST", "https://qa.example/send"], ["GET", "https://other.example/"]]) {
    handlers.fetch({ request: { method, url }, respondWith() { assert.fail("unexpected cache interception"); } });
  }
});
