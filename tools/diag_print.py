#!/usr/bin/env python3
"""One-shot diagnostic: LOCKLESS print, HOLD the connection open, poll to real
completion, log every byte the printer sends back.

This tests the one sequence we have NOT cleanly tried on our firmware:
  connect -> <print> XML -> JPEG bytes -> (HOLD socket) poll status on the SAME
  socket until imaging truly finishes -> THEN close (close triggers the cut).

No lock, no job_token (your call: other implementations work locklessly).
The key discipline learned the hard way: NEVER end the job early. Ignore empty
status replies and keep polling; only stop on a real SUCCESS/idle-after-busy or a
real reported error. Closing/ending before imaging completes = blank eject / jam
/ wedge.

NOT part of the shipped tool. Throwaway. Usage:
    uv run python tools/diag_print.py 192.168.25.219 tools/colortest.jpg
"""
import re
import socket
import sys
import time

PORT = 9100
HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'


def recv(sock, settle=0.5):
    time.sleep(settle)
    chunks = []
    try:
        while True:
            d = sock.recv(8192)
            if not d:
                break
            chunks.append(d)
            if len(d) < 8192:
                break
    except socket.timeout:
        pass
    return b"".join(chunks).decode("utf-8", "replace")


def field(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text)
    return m.group(1) if m else None


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.25.219"
    path = sys.argv[2] if len(sys.argv) > 2 else "tools/colortest.jpg"
    jpeg = open(path, "rb").read()
    print(f"[diag] host={host} jpeg={path} ({len(jpeg)} bytes) -- LOCKLESS, hold-connection")

    s = socket.create_connection((host, PORT), timeout=8.0)
    s.settimeout(8.0)
    try:
        # 1. print XML (NO lock, NO job_token) + JPEG
        pxml = (HEAD + "<print>\n<mode>vivid</mode>\n<speed>0</speed>\n<lpi>317</lpi>\n"
                "<width>0</width>\n<height>0</height>\n<dataformat>jpeg</dataformat>\n"
                "<autofit>1</autofit>\n"
                f"<datasize>{len(jpeg)}</datasize>\n<cutmode>full</cutmode>\n</print>\n")
        s.sendall(pxml.encode())
        r = recv(s, settle=0.6)
        print(f"[diag] PRINT reply:\n{r!r}\n")
        if "ready to receive" not in r.lower():
            print("[diag] WARNING: printer did not say 'ready to receive'")

        sent = s.sendall(jpeg)
        print(f"[diag] JPEG sent ({len(jpeg)} bytes); HOLDING connection, polling to completion...\n")

        # 2. poll on the SAME held connection. NEVER end early.
        time.sleep(1.5)
        deadline = time.time() + 90
        last = None
        saw_busy = False
        empties = 0
        while time.time() < deadline:
            try:
                s.sendall((HEAD + "<read>\n<path>/status.xml</path>\n</read>\n").encode())
                st = recv(s, settle=0.5)
            except OSError as e:
                # Held socket may have been reset by the printer when imaging began.
                print(f"[diag] poll send/recv failed ({e}); the printer likely closed "
                      f"the connection. Reconnecting to keep polling (NOT releasing).")
                try:
                    s.close()
                except OSError:
                    pass
                time.sleep(1.0)
                s = socket.create_connection((host, PORT), timeout=8.0)
                s.settimeout(8.0)
                continue

            state = field(st, "print_state")
            stage = field(st, "print_job_stage")
            err = field(st, "print_job_error")

            if state is None and stage is None:
                empties += 1
                if empties <= 3 or empties % 5 == 0:
                    print(f"[diag] (empty reply #{empties}, still polling)")
                time.sleep(1.0)
                continue
            empties = 0

            line = f"state={state} stage={stage} err={err}"
            if line != last:
                print(f"[diag] {line}")
                last = line

            if err and err not in ("NONE", "", None):
                print(f"[diag] >>> ERROR reported: {err}")
                break
            if state == "BUSY":
                saw_busy = True
            if stage and "SUCCESS" in stage:
                print("[diag] >>> SUCCESS stage")
                break
            if saw_busy and state == "IDLE" and (stage is None or "READY" in stage or "SUCCESS" in stage):
                print("[diag] >>> job ran (saw BUSY) and returned to IDLE/READY -- done")
                break
            time.sleep(2.0)
        else:
            print("[diag] !!! poll deadline hit without completion")
    finally:
        s.close()
        print("[diag] socket closed (this also triggers the cut)")


if __name__ == "__main__":
    main()
