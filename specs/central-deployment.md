# Central deployment — multi-person shared printer

**Status:** draft, not started
**Created:** 2026-08-03
**Supersedes nothing.** Builds on `specs/flask-app.md` (single-user web app, v0.7.5).

## Goal

Move the VC-500W to a central physical location and let 2–4 people print to it from their
own machines, without wedging the printer and without pooling everyone's label content in
one place.

## The constraint that drives the whole design

The VC-500W accepts **exactly one TCP connection on :9100 at a time**, and a client that
dies mid-job or releases early **wedges the printer in BUSY/PRINTING until it is
power-cycled** (see `CLAUDE.md` GOTCHAs). With multiple clients this is not a
theoretical risk — it is the default outcome unless a single process owns the socket.

Therefore: **one `labler-web` process is the sole owner of :9100.** Everyone else talks to
it over HTTP. This is already how the app works (`_printer_lock`, a module-level
`threading.Lock` in `web/app.py:34`) — it is correct precisely as long as there is one
server. Multi-client is "run that one server somewhere central," not "add a queue daemon."

## Host decision: munchlax, not togepi

Togepi was the initial candidate. Measured 2026-08-03 over SSH:

```
Linux togepi 6.12.47+rpt-rpi-v6 armv6l   Raspberry Pi Model B Rev 2
Mem: 427Mi total, 269Mi available
```

That is the 2012 original Pi (ARMv6, single core), not a Pi 3/4. Two blockers:

1. **No ARMv6 binary wheels for Pillow** on PyPI or piwheels — it would compile from
   source on one ARMv6 core, and again on every upgrade. Pillow is the entire render path,
   not an optional dep.
2. **Rendering is the workload.** A 313 DPI label composite runs on every keystroke in the
   editor (live preview). On ARMv6 with ~270 MB free, previews would take seconds and the
   designer would be unusable.

**Munchlax** (M4 Pro, 24 GB) is already the production Flask target per the global
CLAUDE.md: launchd, code in `~/projects/<app>/`, always on. Renders faster than spearow.
No new infrastructure.

Togepi stays out of this design entirely. Remote power-cycling (below) is handled by a
Shelly, which does not need a host next to the printer.

## Privacy model — DECIDED

**Label content never reaches the server. The server logs statistics only.**

| Data | Where it lives | Why |
|---|---|---|
| Print history + thumbnails | **Client browser (IndexedDB)** | Images and label text are private to the person who printed them |
| Saved designs | **Client browser (IndexedDB)** | Same |
| Tape statistics (used, remain, timestamp, media, ok/fail) | **Server** (`~/.labler/stats.jsonl`) | "Who is burning the roll, when do we reorder" is inherently a shared question, and carries no label content |
| Settings | **Server**, shared | Host/media/cut are properties of the shared printer, not of a person |

Thumbnails are PNG blobs, so **IndexedDB, not localStorage** (quota).

### Accepted tradeoff

Browser-local history is per-*browser*, not per-person: your phone and your desktop show
different histories, and clearing site data wipes it with no backup. This is the price of
"no images on the server" and is accepted deliberately.

If this becomes annoying, the fallback is per-user server-side directories
(`~/.labler/users/<user>/`) behind a login — private *between people*, but no longer
private from whoever administers munchlax. The storage layer is small enough that
switching later is cheap. Do not build it pre-emptively.

## Work items

### 1. Statistics-only server logging (privacy-critical)

`log_event(event, message, **fields)` in `web/runtime.py:84` passes **arbitrary kwargs**
straight to disk. Leaks found on inspection:

- `app.py:266` — `log_event("design.save", ..., name=name)` writes the label's name.
  **Real leak; fixed by the allowlist.**
- `app.py:180` — `print.done` logs `entry=entry`. **Not a leak after all**: `_append_history`
  returns an `entry_id` *string*, not the record. Kept on the allowlist as an id. The
  content lives in `history.jsonl` (`name`, `display_list`) — that is Phase B's problem,
  not the log's.
- **`message` is positional and bypasses the field filter.** Three call sites pass
  `str(e)` (app.py:127, 159, 175); a render exception can quote the label text that caused
  it. Capped at `MAX_MESSAGE_LEN`; the exception *type* (`kind`) is the useful part.

**Fix with an explicit field allowlist enforced inside `log_event` itself**, not by
auditing call sites. A denylist, or discipline at the call site, re-introduces a leak the
first time someone adds a debug field. Allowed: timestamps, host, event name, tape
figures, print state, error kind, counts, ids. Rejected-and-dropped: anything else.

Add a test that asserts a disallowed field is dropped rather than written.

### 2. Client-side history + designs (IndexedDB)

Move history and designs out of `~/.labler/` into browser storage. Server-side endpoints
to remove or repoint: `/api/history*`, `/api/designs*`, `/api/history_thumb`,
`/api/design_preview`.

Assets (`/api/assets`) need a decision: today an uploaded bitmap is saved server-side and
referenced by id. Under this model the bitmap **is** label content. Either inline assets
into the client-side display-list as data URIs, or accept that the upload path is a
deliberate exception. Inlining is cleaner and keeps the rule absolute.

### 3. Queue feedback

Today a second concurrent print blocks silently on `_printer_lock` until the socket times
out. With 2–4 people the browser must be told "you are 2nd in line" rather than hanging.
Needs a queue position endpoint and a spinner state in `app.js`.

### 4. Printer DHCP reservation

`192.168.25.219` is a **dynamic lease**. With several clients and a server config naming
the IP, a lease change breaks everyone simultaneously. A reservation on the Fritzbox also
makes it publish an **A record**, so clients can use the *name* — see the workspace
CLAUDE.md note on `getent ahostsv4`.

Also: the printer did not answer ping from spearow on 2026-08-03 ("Destination host
unreachable"). Probably just powered off — **confirm before relying on the IP.**

### 5. Remote wedge recovery (Shelly)

Today a wedge is fixed by walking to the printer and power-cycling it. Centrally located,
a wedge blocks everyone and nobody is next to it. Pair the printer with a Shelly outlet
(tooling already exists in `D:\hw\shelly/`) so recovery is an API call. Consider an
`/api/device/powercycle` endpoint gated behind a confirm.

### 6. launchd deploy on munchlax

Per global conventions: code to `~/projects/labler-vc5002/`, plist for
`com.labler.web`, restart via
`launchctl kickstart -k gui/$(id -u)/com.labler.web`. A `tools/deploy.sh` doing rsync +
restart follows the pokeflute pattern.

## Task tracker

| # | Task | Status |
|---|---|---|
| 1 | Statistics-only `log_event` allowlist + test | Not started |
| 2 | History + designs to IndexedDB; decide asset inlining | Not started |
| 3 | Queue position feedback | Not started |
| 4 | Printer DHCP reservation + confirm it is powered on | Not started |
| 5 | Shelly power-cycle recovery | Not started |
| 6 | launchd deploy + `tools/deploy.sh` | Not started |

## Resolved questions

- **Where is the printer physically going?**
  **Basement, next to munchlax.** App host and printer are co-located. The Shelly is worth
  wiring: the basement is not where people will be standing when a wedge happens.

- **Asset uploads** (item 2) — inline as data URIs, or keep as a server-side exception?
  **Inline as data URIs.** The privacy rule becomes absolute — no label content on the
  server at all. `/api/assets` is *removed*, not repointed.

## Execution plan

Ordered by dependency and by risk. Each step is independently committable and leaves the
app working. Version bumps per repo convention (`src/labler/__init__.py` + `pyproject.toml`
in the same commit).

### Phase A — privacy (server-side, no deploy needed)

**A1. `log_event` allowlist** → v0.8.0
Enforce the statistics-only rule inside `runtime.log_event`. Drop any field not on the
allowlist. Fixes the two live leaks (`print.done` `entry=`, `design.save` `name=`).
Tests: disallowed field dropped; allowed fields survive; leak-regression test naming both
current offenders.

**A2. Data-URI assets** → v0.8.1
`compose._resolve_image` already takes a PIL image / path / file object, so decoding a
`data:image/png;base64,...` to `BytesIO` slots in cleanly. Then:
- delete `/api/assets` (POST + GET) and `runtime.ASSETS_DIR`
- delete `_resolve_assets` (6 call sites) — elements carry their own `src` data URI
- `app.js`: `uploadAsset()` and the Ctrl+V paste path produce a data URI instead of POSTing
Tests: data URI renders identically to the old path-based asset; oversized URI rejected.

**A3. Stats stream** → v0.8.2
`~/.labler/stats.jsonl` — one record per print: timestamp, host, media, mode, cut,
remain_before/after, tape_used_in, ok, error kind. No label content, enforced by A1's
allowlist. Add `/api/stats` (aggregate: tape used per day/week, roll burn-down).

### Phase B — client-side storage (the big one)

**B1. IndexedDB layer in `app.js`** → v0.8.3
Object stores `designs` and `history` (thumbnails as PNG blobs). Wrap in a small
promise-based helper; no external library.

**B2. Cut over history + designs** → v0.8.4
Repoint the History and Edit tabs at IndexedDB. Remove server endpoints
`/api/history*`, `/api/designs*`, `/api/history_thumb`, `/api/design_preview`, and the
`~/.labler/designs|history` trees.
**Migration:** on first load, if the server still has designs/history, offer a one-time
"import my existing designs into this browser" action, then let the server copies be
deleted. Without this, existing saved designs silently vanish.
Thumbnails are generated client-side from the same render the printer got (preview ==
print still holds — the *server* renders, the client stores the PNG it was shown).

**B3. Export / import** → v0.8.5
Browser-local storage has no backup and dies with site data (accepted tradeoff, but
mitigate it). JSON export/import of designs + history from the Settings tab.

### Phase C — multi-client robustness

**C1. Print queue feedback** → v0.8.6
Replace the bare `_printer_lock` with a queue exposing position + estimated wait.
`/api/print` returns immediately with a job id; `/api/queue/<id>` polls. `app.js` shows
"2nd in line" instead of hanging until socket timeout.
Tests: two concurrent prints serialize; position reported correctly; a failed job does not
strand the queue.

**C2. Shelly power-cycle recovery** → v0.8.7
`/api/device/powercycle`, gated behind an explicit confirm, driving the Shelly via the
existing `D:\hw\shelly/` tooling. Wedge detection: `BUSY/PRINTING` with `remain` frozen
across N polls (per the CLAUDE.md wedge fingerprint) → surface "printer appears wedged,
power-cycle?" in the UI.
Needs the Shelly's outlet assignment — ask when we get here.

### Phase D — deploy

**D1. `tools/deploy.sh`** → v0.9.0
rsync to `munchlax:~/projects/labler-vc5002/`, `uv sync`, launchd restart. Pokeflute
pattern.

**D2. launchd plist** `tools/munchlax/com.labler.web.plist`
KeepAlive, log to `~/.labler/logs/`, bind `0.0.0.0:5000` so other machines can reach it.

**D3. Docs pass**
`CLAUDE.md` (new deployment reality, privacy model, retire the single-user assumptions),
`README.md`, `specs/flask-app.md` cross-reference.

### 🔧 PHYSICAL STEP — move the printer

**Do this between C2 and D1.** Reasons for that ordering:
- All software work up to C2 is testable against the printer wherever it currently sits.
- D1/D2 deploy *to munchlax*, which is where the printer needs to already be, since
  munchlax will hold the :9100 connection.
- C2 (Shelly) wants the printer in its final outlet before wiring is verified.

Checklist when the time comes:
1. Power off the printer cleanly (not mid-job — check `print_state=IDLE` first).
2. Move to the basement next to munchlax; plug into the Shelly outlet.
3. **Add the DHCP reservation on the Fritzbox** (item 4) — MAC `04:fe:a1:53:bf:2b`. This
   also publishes the A record so we can stop hardcoding `192.168.25.219`.
4. Verify: `ping`, then `getent ahostsv4 VC-500W5087` for the A record, then a status read
   on :9100 from munchlax specifically.
5. Print one colortest label from munchlax to confirm the full path end-to-end.

## Test suite plan

Current coverage: 808 lines across 5 files (`compose`, `protocol`, `render`, `status`,
`web`). Gaps this work must close:

| Area | Test |
|---|---|
| Privacy | `log_event` drops non-allowlisted fields; regression test for the two known leaks |
| Privacy | No endpoint returns label content once B2 lands (assert removed routes 404) |
| Assets | Data URI renders byte-identical to the old path-based asset |
| Assets | Malformed / oversized data URI rejected cleanly |
| Queue | Concurrent prints serialize; queue position correct; failed job doesn't strand |
| Stats | `stats.jsonl` record shape; no label content present |
| Migration | Server→browser design import preserves the display-list exactly |
| Deploy | `/api/ping` returns the four standard fields with the new version |

**JS is the known blind spot** (`CLAUDE.md` lesson #6: Python tests never exercise browser
JS, so JS bugs sail through green tests). B1/B2 are almost entirely JS. Mitigation:
`node --check` on `app.js` in CI, plus pure-logic functions (IndexedDB record shaping,
data-URI encode/decode, migration mapping) extracted so they are testable under `node -e`
without a DOM.

## Task tracker

| # | Task | Version | Status |
|---|---|---|---|
| A1 | Statistics-only `log_event` allowlist + tests | 0.8.0 | **Done** |
| A2 | Data-URI assets; remove `/api/assets` + `_resolve_assets` | 0.8.1 | **Done** |
| A3 | `stats.jsonl` + `/api/stats` | 0.8.2 | **Done** |
| B1 | IndexedDB storage layer | 0.8.3 | **Done** |
| B2 | Cut over history + designs; migration import | 0.8.4 | **Done** |
| B3 | Export / import JSON | 0.8.5 | **Done** |
| C1 | Print queue + position feedback | 0.8.6 | **Done** |
| C2 | Shelly power-cycle + wedge detection | 0.8.7 | **Done** |
| 🔧 | **Move the printer** (physical) | — | Not started |
| D1 | `tools/deploy.sh` | 0.9.0 | Not started |
| D2 | launchd plist | 0.9.0 | Not started |
| D3 | Docs pass | 0.9.0 | Not started |
