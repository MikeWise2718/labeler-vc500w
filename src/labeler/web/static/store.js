"use strict";
// Client-side storage for designs and print history (IndexedDB).
//
// WHY THIS EXISTS: the VC-500W is shared between several people, so label content
// — the text, the bitmaps, the design names — must not accumulate on the server.
// Designs and history therefore live in the browser; only tape STATISTICS go to
// the server (see runtime.record_print_stats). specs/central-deployment.md.
//
// ACCEPTED TRADEOFF: storage is per-BROWSER, not per-person. Your phone and your
// desktop keep separate histories, and clearing site data wipes them. Settings →
// Export exists to mitigate that; it is not a substitute for a real backup.
//
// Everything here is DOM-free except openDB(); the record-shaping helpers are pure
// so they can be exercised under `node -e` without a browser (CLAUDE.md lesson #6).

const DB_NAME = "labeler";
const DB_VERSION = 1;
const STORE_DESIGNS = "designs";
const STORE_HISTORY = "history";

// Keep the browser from growing without bound. History is a convenience log, not
// an archive; the oldest entries are trimmed once we pass this.
const MAX_HISTORY_ENTRIES = 500;

let _dbPromise = null;

function openDB() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_DESIGNS)) {
        db.createObjectStore(STORE_DESIGNS, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(STORE_HISTORY)) {
        const h = db.createObjectStore(STORE_HISTORY, { keyPath: "id" });
        h.createIndex("timestamp", "timestamp");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}

// Promisify one transaction against a store.
function tx(storeName, mode, fn) {
  return openDB().then(db => new Promise((resolve, reject) => {
    const t = db.transaction(storeName, mode);
    const store = t.objectStore(storeName);
    let result;
    try {
      result = fn(store);
    } catch (e) {
      reject(e);
      return;
    }
    t.oncomplete = () => resolve(result && result.result !== undefined ? result.result : result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  }));
}

// ---- pure helpers (testable without a DOM) ---------------------------------

// Slug used as a design's primary key. Mirrors the server's old _slug() so that
// designs imported from a pre-0.8.3 server keep their ids.
function slugify(s) {
  const keep = String(s || "").trim().toLowerCase()
    .split("").map(c => /[a-z0-9\-_]/.test(c) ? c : "-").join("");
  return keep.replace(/^-+|-+$/g, "") || "design";
}

// Shape a design for storage. `preview` is a PNG data URI (or null).
function designRecord(design, preview, now) {
  const id = design.id || slugify(design.name);
  return {
    id,
    name: design.name || id,
    updated: now,
    preview: preview || null,
    // store a detached copy so later edits to the live object don't mutate the record
    display_list: JSON.parse(JSON.stringify({ ...design, id })),
  };
}

// Shape a history entry. `thumb` is a PNG data URI of what was actually printed.
function historyRecord(design, result, thumb, now, id) {
  return {
    id,
    timestamp: now,
    name: design.name || "",
    media_mm: design.media_mm ?? 25,
    ok: !!result.ok,
    remain_before_in: result.remain_before ?? null,
    remain_after_in: result.remain_after ?? null,
    tape_used_in: result.tape_used_in ?? null,
    thumb: thumb || null,
    display_list: JSON.parse(JSON.stringify(design)),
  };
}

// Newest first; entries beyond MAX_HISTORY_ENTRIES are dropped.
function sortAndTrimHistory(entries, max = MAX_HISTORY_ENTRIES) {
  const sorted = [...entries].sort((a, b) =>
    String(b.timestamp || "").localeCompare(String(a.timestamp || "")));
  return sorted.slice(0, max);
}

// ---- designs ---------------------------------------------------------------

async function saveDesign(design, preview) {
  const rec = designRecord(design, preview, new Date().toISOString());
  await tx(STORE_DESIGNS, "readwrite", store => store.put(rec));
  return rec.id;
}

function listDesigns() {
  return tx(STORE_DESIGNS, "readonly", store => store.getAll())
    .then(rows => (rows || []).sort((a, b) =>
      String(a.name || "").localeCompare(String(b.name || ""))));
}

function getDesign(id) {
  return tx(STORE_DESIGNS, "readonly", store => store.get(id));
}

function deleteDesign(id) {
  return tx(STORE_DESIGNS, "readwrite", store => store.delete(id));
}

// ---- history ---------------------------------------------------------------

async function addHistory(design, result, thumb) {
  const now = new Date().toISOString();
  // entry id: server's id when it gave one, else timestamp-derived (still unique
  // enough for a per-browser log).
  const id = result.entry || `local-${now}-${Math.random().toString(36).slice(2, 8)}`;
  const rec = historyRecord(design, result, thumb, now, id);
  await tx(STORE_HISTORY, "readwrite", store => store.put(rec));
  await trimHistory();
  return rec.id;
}

function listHistory() {
  return tx(STORE_HISTORY, "readonly", store => store.getAll())
    .then(rows => sortAndTrimHistory(rows || []));
}

function deleteHistory(id) {
  return tx(STORE_HISTORY, "readwrite", store => store.delete(id));
}

async function trimHistory() {
  const rows = await tx(STORE_HISTORY, "readonly", store => store.getAll());
  if (!rows || rows.length <= MAX_HISTORY_ENTRIES) return 0;
  const keep = new Set(sortAndTrimHistory(rows).map(r => r.id));
  const drop = rows.filter(r => !keep.has(r.id));
  await tx(STORE_HISTORY, "readwrite", store => { drop.forEach(r => store.delete(r.id)); });
  return drop.length;
}

// ---- export / import -------------------------------------------------------
// Browser storage dies with site data. This is the user's escape hatch.

async function exportAll() {
  const [designs, history] = await Promise.all([listDesigns(), listHistory()]);
  return { format: "labeler-export", version: 1,
           exported: new Date().toISOString(), designs, history };
}

// Pure: validate and normalise an uploaded export blob. Throws on garbage.
function parseExport(obj) {
  if (!obj || typeof obj !== "object") throw new Error("not an export file");
  if (obj.format !== "labeler-export") throw new Error("not a labeler export file");
  const designs = Array.isArray(obj.designs) ? obj.designs : [];
  const history = Array.isArray(obj.history) ? obj.history : [];
  return { designs, history };
}

async function importAll(obj) {
  const { designs, history } = parseExport(obj);
  // An id-less design used to be silently dropped here — the import would report
  // "N designs" (the array length) while storing zero, leaving the user hunting for
  // labels that never landed. Give any design missing an id a derived one so a
  // hand-authored or externally generated export imports instead of vanishing.
  let storedDesigns = 0;
  if (designs.length) {
    await tx(STORE_DESIGNS, "readwrite", store => {
      designs.forEach(d => {
        if (!d || typeof d !== "object") return;
        if (!d.id) d.id = slugify(d.name || (d.display_list && d.display_list.name));
        if (!d.name) d.name = d.id;
        store.put(d);
        storedDesigns++;
      });
    });
  }
  if (history.length) {
    await tx(STORE_HISTORY, "readwrite", store => { history.forEach(h => h && h.id && store.put(h)); });
  }
  await trimHistory();
  return { designs: storedDesigns, history: history.length };
}

// ---- one-time migration from the pre-0.8.3 server --------------------------
// Older versions kept designs and history server-side. Those endpoints are gone,
// but an existing ~/.labeler/ may still hold them, so the server exposes them once
// via /api/migrate/export. Without this, upgrading silently loses saved designs.

const MIGRATION_FLAG = "labeler_migrated_v083";

function migrationDone() {
  return localStorage.getItem(MIGRATION_FLAG) === "1";
}

function markMigrationDone() {
  localStorage.setItem(MIGRATION_FLAG, "1");
}

async function runMigration(fetchFn) {
  if (migrationDone()) return null;
  let payload;
  try {
    payload = await fetchFn("/api/migrate/export");
  } catch {
    return null;                      // server too old / endpoint absent: nothing to do
  }
  if (!payload || !payload.ok) { markMigrationDone(); return null; }
  const designs = payload.designs || [];
  const history = payload.history || [];
  if (!designs.length && !history.length) { markMigrationDone(); return null; }
  const counts = await importAll({ format: "labeler-export", version: 1, designs, history });
  markMigrationDone();
  return counts;
}

// Expose for the app and for tests.
const store = {
  saveDesign, listDesigns, getDesign, deleteDesign,
  addHistory, listHistory, deleteHistory, trimHistory,
  exportAll, importAll, parseExport,
  runMigration, migrationDone, markMigrationDone,
  // pure helpers (unit-tested under node)
  slugify, designRecord, historyRecord, sortAndTrimHistory,
  MAX_HISTORY_ENTRIES,
};

if (typeof window !== "undefined") window.store = store;
if (typeof module !== "undefined" && module.exports) module.exports = store;
