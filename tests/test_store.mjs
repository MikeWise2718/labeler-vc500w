// Integration tests for static/store.js against a real IndexedDB implementation.
//
// The Python suite never exercises browser JS (CLAUDE.md lesson #6), and store.js
// is where a bug silently eats someone's saved designs — so it gets real tests, not
// just a syntax check. Uses fake-indexeddb (dev-only, not shipped).
//
// Run:  node --test tests/test_store.mjs
// Needs: npm install fake-indexeddb   (see tools/run-js-tests.sh)

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

// Install a fake IndexedDB into the global scope before loading store.js.
await import("fake-indexeddb/auto");

const store = require("../src/labler/web/static/store.js");

// Each test gets a clean DB: delete and let store.js reopen lazily.
async function reset() {
  // store.js caches its open promise; clear the DB contents rather than the handle.
  const designs = await store.listDesigns();
  await Promise.all(designs.map(d => store.deleteDesign(d.id)));
  const history = await store.listHistory();
  await Promise.all(history.map(h => store.deleteHistory(h.id)));
  store.markMigrationDone === undefined || localStorage.removeItem("labler_migrated_v083");
}

// localStorage shim — fake-indexeddb doesn't provide one.
if (typeof globalThis.localStorage === "undefined") {
  const mem = new Map();
  globalThis.localStorage = {
    getItem: k => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: k => mem.delete(k),
  };
}

test("save then load a design round-trips the display list", async () => {
  await reset();
  const design = {
    name: "Shelf Label", media_mm: 25, length_px: "auto", rotate: 0,
    background: "white",
    elements: [{ type: "text", x: 1, y: 2, w: 300, h: 40, text: "Screws M4" }],
  };
  const id = await store.saveDesign(design, "data:image/png;base64,AAAA");
  assert.equal(id, "shelf-label");

  const loaded = await store.getDesign(id);
  assert.equal(loaded.name, "Shelf Label");
  assert.equal(loaded.preview, "data:image/png;base64,AAAA");
  assert.deepEqual(loaded.display_list.elements, design.elements);
});

test("saving twice under the same name updates rather than duplicates", async () => {
  await reset();
  await store.saveDesign({ name: "Dup", elements: [] }, null);
  await store.saveDesign({ name: "Dup", elements: [{ type: "border" }] }, null);
  const all = await store.listDesigns();
  assert.equal(all.length, 1);
  assert.equal(all[0].display_list.elements.length, 1);
});

test("designs are listed alphabetically", async () => {
  await reset();
  for (const n of ["Zebra", "apple", "Mango"]) {
    await store.saveDesign({ name: n, elements: [] }, null);
  }
  const names = (await store.listDesigns()).map(d => d.name);
  assert.deepEqual(names, ["apple", "Mango", "Zebra"]);
});

test("deleting a design removes only that one", async () => {
  await reset();
  await store.saveDesign({ name: "Keep", elements: [] }, null);
  await store.saveDesign({ name: "Drop", elements: [] }, null);
  await store.deleteDesign("drop");
  const names = (await store.listDesigns()).map(d => d.name);
  assert.deepEqual(names, ["Keep"]);
});

test("a stored design is detached from the live editor object", async () => {
  await reset();
  const design = { name: "Live", elements: [{ type: "text", text: "before" }] };
  await store.saveDesign(design, null);
  design.elements[0].text = "after";          // user keeps editing
  const loaded = await store.getDesign("live");
  assert.equal(loaded.display_list.elements[0].text, "before");
});

test("history records a print with its tape stats", async () => {
  await reset();
  const design = { name: "Box 1", media_mm: 25, elements: [] };
  const result = { ok: true, entry: "abc123", remain_before: 10, remain_after: 8.5,
                   tape_used_in: 1.5 };
  const id = await store.addHistory(design, result, "data:image/png;base64,BBBB");
  assert.equal(id, "abc123");

  const [h] = await store.listHistory();
  assert.equal(h.name, "Box 1");
  assert.equal(h.tape_used_in, 1.5);
  assert.equal(h.ok, true);
  assert.equal(h.thumb, "data:image/png;base64,BBBB");
});

test("a failed print is still recorded", async () => {
  await reset();
  await store.addHistory({ name: "Jam", elements: [] }, { ok: false }, null);
  const [h] = await store.listHistory();
  assert.equal(h.ok, false);
});

test("history without a server entry id still gets a unique key", async () => {
  await reset();
  const a = await store.addHistory({ name: "A", elements: [] }, { ok: true }, null);
  const b = await store.addHistory({ name: "B", elements: [] }, { ok: true }, null);
  assert.notEqual(a, b);
  assert.equal((await store.listHistory()).length, 2);
});

test("history is returned newest first", async () => {
  await reset();
  await store.addHistory({ name: "old", elements: [] }, { ok: true, entry: "e1" }, null);
  await new Promise(r => setTimeout(r, 5));
  await store.addHistory({ name: "new", elements: [] }, { ok: true, entry: "e2" }, null);
  const names = (await store.listHistory()).map(h => h.name);
  assert.equal(names[0], "new");
});

test("export then import restores designs and history", async () => {
  await reset();
  await store.saveDesign({ name: "Exported", elements: [{ type: "border" }] }, null);
  await store.addHistory({ name: "Printed", elements: [] },
                         { ok: true, entry: "h1", tape_used_in: 2 }, null);
  const dump = await store.exportAll();
  assert.equal(dump.format, "labler-export");
  assert.equal(dump.designs.length, 1);
  assert.equal(dump.history.length, 1);

  await reset();
  assert.equal((await store.listDesigns()).length, 0);

  const counts = await store.importAll(dump);
  assert.equal(counts.designs, 1);
  assert.equal(counts.history, 1);
  const [d] = await store.listDesigns();
  assert.equal(d.name, "Exported");
  const [h] = await store.listHistory();
  assert.equal(h.tape_used_in, 2);
});

test("importing garbage throws rather than corrupting the store", async () => {
  await reset();
  await store.saveDesign({ name: "Safe", elements: [] }, null);
  await assert.rejects(() => store.importAll({ format: "something-else" }));
  await assert.rejects(() => store.importAll(null));
  assert.equal((await store.listDesigns()).length, 1);   // untouched
});

test("import derives an id for id-less designs instead of dropping them", async () => {
  // Regression: an externally generated / hand-authored export whose designs lack
  // an `id` used to be silently skipped — import reported "N designs" but stored
  // zero, so the user could not find their imported labels. Now a missing id is
  // derived from the name and the design is stored.
  await reset();
  const counts = await store.importAll({
    format: "labler-export", version: 1,
    designs: [{ id: "ok-one", name: "Fine" }, { name: "No Id Here", display_list: {} }],
    history: [],
  });
  assert.equal(counts.designs, 2);                       // both actually stored now
  const stored = await store.listDesigns();
  assert.equal(stored.length, 2);
  // the id-less one got a slug derived from its name
  assert.ok(stored.some(d => d.id === "no-id-here" && d.name === "No Id Here"));
});

test("import count reflects what stored, not the array length", async () => {
  // A completely unusable entry (not an object) is not counted as stored.
  await reset();
  const counts = await store.importAll({
    format: "labler-export", version: 1,
    designs: [{ id: "real", name: "Real" }, null, "garbage"],
    history: [],
  });
  assert.equal(counts.designs, 1);
  assert.equal((await store.listDesigns()).length, 1);
});

test("history is trimmed to MAX_HISTORY_ENTRIES", async () => {
  await reset();
  const max = store.MAX_HISTORY_ENTRIES;
  // add a handful past the cap, with increasing timestamps via explicit ids
  const over = 3;
  for (let i = 0; i < max + over; i++) {
    await store.addHistory({ name: "n" + i, elements: [] },
                           { ok: true, entry: `e${String(i).padStart(5, "0")}` }, null);
  }
  const rows = await store.listHistory();
  assert.equal(rows.length, max);
});

test("migration imports legacy server data exactly once", async () => {
  await reset();
  localStorage.removeItem("labler_migrated_v083");
  let calls = 0;
  const fakeFetch = async () => {
    calls++;
    return {
      ok: true,
      designs: [{ id: "legacy", name: "Legacy", display_list: { elements: [] } }],
      history: [{ id: "lh1", timestamp: "2026-06-01T00:00:00Z", name: "old", tape_used_in: 1 }],
    };
  };
  const first = await store.runMigration(fakeFetch);
  assert.equal(first.designs, 1);
  assert.equal((await store.listDesigns()).length, 1);

  // second boot: must not re-import
  const second = await store.runMigration(fakeFetch);
  assert.equal(second, null);
  assert.equal(calls, 1);
});

test("migration tolerates a server with no legacy data", async () => {
  await reset();
  localStorage.removeItem("labler_migrated_v083");
  const r = await store.runMigration(async () => ({ ok: true, designs: [], history: [] }));
  assert.equal(r, null);
  assert.equal(store.migrationDone(), true);   // don't ask again
});

test("migration tolerates a missing endpoint", async () => {
  await reset();
  localStorage.removeItem("labler_migrated_v083");
  const r = await store.runMigration(async () => { throw new Error("404"); });
  assert.equal(r, null);
  assert.equal(store.migrationDone(), false);  // endpoint may come back; retry later
});
