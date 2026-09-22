import assert from "node:assert/strict";
import test from "node:test";
import { openContactWorkspace, selectedWorkspaceBatch, workspaceDraftBatch } from "../src/workspaceNavigation.js";

test("batch navigation retains attribution only for the selected contact", () => {
  const storage = new Map();
  globalThis.window = {
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    location: {}, setTimeout() {},
  };
  try {
    openContactWorkspace(12, 0, 7);
    assert.equal(selectedWorkspaceBatch(12), 7);
    assert.equal(selectedWorkspaceBatch(13), null);
    openContactWorkspace(12);
    assert.equal(selectedWorkspaceBatch(12), null);
    openContactWorkspace(12, 0, -1);
    assert.equal(selectedWorkspaceBatch(12), null);
    window.sessionStorage.getItem = () => { throw new Error("storage unavailable"); };
    assert.equal(selectedWorkspaceBatch(12), null);
  } finally {
    delete globalThis.window;
  }
});

test("resuming a saved draft keeps its campaign outside batch navigation", () => {
  const storage = new Map();
  globalThis.window = {
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    location: {}, setTimeout() {},
  };
  try {
    openContactWorkspace(12, 0, 9);
    const draft = { contact_id: 12, campaign_id: 7 };
    assert.equal(workspaceDraftBatch(12, draft), 7);
    assert.equal(workspaceDraftBatch(12, { contact_id: 13, campaign_id: 7 }), 9);
    openContactWorkspace(12);
    assert.equal(workspaceDraftBatch(12, draft), 7);
    assert.equal(workspaceDraftBatch(12, null), null);
    window.sessionStorage.getItem = () => { throw new Error("storage unavailable"); };
    assert.equal(workspaceDraftBatch(12, draft), 7);
  } finally {
    delete globalThis.window;
  }
});
