# Print sequence — root cause of the EJECT JAM / wedge (2026-06-15)

## TL;DR (CORRECTED after live debugging)

The working print sequence on our firmware is **lockless, hold ONE connection
open through imaging, poll to completion, then close**:

```
connect → <print> XML → JPEG bytes → (HOLD socket) poll /status.xml until
imaging finishes (PROCESSING→PREPARING PRINT→PREHEAT→PRINTING→EJECTING→SUCCESS)
→ THEN close. No <lock>, no job_token.
```

**The real root cause of the EJECT JAM was a status-PARSE bug, not the cut
timing.** The printer **pipelines replies**: one socket read can return a
leftover ack (`print data received`) glued to one or more status bodies,
NUL-separated. Our parser used `re.search` (FIRST match), so it read a *stale*
status block, desynced from reality, and either exited with `state=None`
(silent no-op) or closed the socket mid-imaging → blank leader → `EJECT JAM`.

Fix: `Status.parse` now takes the **last** `<status>` block containing a
`print_state`; the poll loop requires the job to actually start (`saw_busy`)
before accepting SUCCESS/idle as "done" (so a leftover SUCCESS from the previous
print can't end the new job early); and the poll cadence was slowed so we don't
flood the single-slot firmware during the job-start window.

### What was WRONG in the first hypothesis

The "close socket immediately to trigger the cut, then reconnect to poll" idea
(from the prior-art repos below) is **firmware-dependent and wrong for our unit**.
Closing right after the JPEG aborts the job here. Our firmware needs the
connection held *through* imaging; the close at the END triggers the final cut.

### Separate hardware behavior (not a bug)

After a SUCCESSFUL print, the cut label **sits in the output slot and the
printer blinks (jam) until you physically remove it** — it won't print the next
label until the slot is clear. This is by design (confirmed against
`Sunburn-Schematics` notes). Back-to-back automated prints must account for it:
remove the label between prints.

## How we found it

After two consecutive `EJECT JAM` + wedge events (a blank ~5 cm strip ejected,
no image, `remain` frozen), we checked prior-art repos. Two independent sources
agree:

- **`Sunburn-Schematics/brother-vc500w-driver`** (protocol documentation):
  > "The printer will NOT cut the label until the TCP connection is closed."
  > Socket closure triggers the cut cycle automatically. Workflow: send print
  > XML → send JPEG → close socket → poll status until idle → reconnect for next
  > label. No explicit eject/feed/pageend command exists.

  Also notes the lock/job_token dance is "error-prone and usually unnecessary";
  sequential single prints with close/reconnect is more reliable.

- **`honeymaro/node-vc-500w`** (Node.js lib):
  > "Some firmware revisions appear to require the TCP socket to close before the
  > cutter engages." Has a `cutTriggerMode: 'close-reopen'` option. Polls status
  > via reconnection, not a persistent socket.

## Correct sequence

```
1. connect → lock (op=set) → get job_token
2. send <print> XML
3. send raw JPEG bytes (datasize = exact byte count)
4. CLOSE the socket   ← this is what triggers the cut/eject
5. reconnect → poll <read>/status.xml</read> until the job finishes
6. release/cleanup (and free the lock even on error)
```

## Our two bugs (original `print_jpeg`)

| # | Bug | Symptom |
|---|---|---|
| A | Polled + sent `lock cancel` on the **same open socket**; never closed-to-cut until the very end. | Cut never triggered; blank leader ejected; `EJECT JAM`. |
| B | On a print error we `raise`d and **skipped the lock release** (only `finally: sock.close()`). | Lock stranded → printer wedged `BUSY/PRINTING`, refuses new locks until power-cycle. |

Bug A is the root cause of the jam; bug B turns any error into a wedge.

## Caveat

"Close triggers cut" is firmware-dependent per honeymaro (hence their
`close-reopen` mode). Our firmware's behavior should be re-confirmed on the first
successful CLI print after the fix. The earlier *manual* color-grid print
(2026-06-14) worked because it happened to cycle the connection correctly; the
CLI codified a different, wrong sequence.

## Sources

- <https://github.com/Sunburn-Schematics/brother-vc500w-driver>
- <https://github.com/honeymaro/node-vc-500w>
- Other VC-500W prior-art repos (no relevant issues; knowledge is in READMEs):
  `unitof/brother-vc-500w-hacking` (firmware only), `corentin-soriano/vc-500w_autocut`
  (confirms `<cutmode>full</cutmode>`), `Tadelsucht/VC-500W-Color-Fix` (color).
