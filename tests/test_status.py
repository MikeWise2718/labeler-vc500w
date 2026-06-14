"""Tests for status.xml parsing and the ready property."""

from labler.status import Status

# A real reply captured from the printer (2026-06-14), envelope + body concatenated.
REAL_REPLY = """<?xml version="1.0" encoding="UTF-8"?>
<status>
<path>/status.xml</path>
<code>0</code>
<datasize>432</datasize>
</status>
 <?xml version="1.0" encoding="UTF-8"?>
<status>
<print_state>IDLE</print_state>
<print_job_stage>READY FOR PRINT</print_job_stage>
<print_job_error>NONE</print_job_error>
<print_line>0</print_line>
<print_num>0</print_num>
<remain>54.87</remain>
<config>
 <media_features>
  <cassette_type>1</cassette_type>
 </media_features>
</config>
<power>
 <online>1</online>
 <present>0</present>
 <capacity>100</capacity>
</power>
</status>
"""


def test_parse_real_reply():
    s = Status.parse(REAL_REPLY)
    assert s.print_state == "IDLE"
    assert s.print_job_stage == "READY FOR PRINT"
    assert s.print_job_error == "NONE"
    assert s.remain == 54.87
    assert s.cassette_type == 1
    assert s.online is True
    assert s.capacity == 100


def test_ready_when_idle_and_ready_stage():
    assert Status.parse(REAL_REPLY).ready is True


def test_ready_accepts_success_stage():
    # post-job terminal stage seen live
    s = Status(print_state="IDLE", print_job_stage="SUCCESS", print_job_error="NONE")
    assert s.ready is True


def test_not_ready_when_busy():
    s = Status(print_state="BUSY", print_job_stage="PRINTING", print_job_error="NONE")
    assert s.ready is False


def test_not_ready_on_error():
    s = Status(print_state="IDLE", print_job_stage="SUCCESS", print_job_error="COVER OPEN")
    assert s.ready is False


def test_missing_fields_are_none():
    s = Status.parse("<status></status>")
    assert s.print_state is None
    assert s.remain is None
    assert s.ready is False
