# Continuous Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the sync box record continuously for a whole day, stream to disk so a crash costs ≤1 second, and describe its own outages well enough that `wl-preproc` can mark unwitnessed trials positively.

**Architecture:** One directory per day, one segment file per process run. The barcode counter becomes seconds-since-2020 so an outage is visible in the shared alignment key itself. Records stream to an append-only sink with `fsync` at 1 Hz. A manifest, rebuilt by scanning segments at every start, enumerates segments and gaps — crash-vs-clean is derived from whether a segment has a trailer, never from a dying process cooperating.

**Tech Stack:** Python ≥3.11, pydantic ≥2.6, pytest. No Pi libraries — everything here runs on a laptop against `FakeBackend`.

**Spec:** `docs/superpowers/specs/2026-08-16-syncbox-continuous-recording-design.md`

## Global Constraints

- **Python ≥3.11, no upper bound.** Must work on 3.11 (`wl-preproc`'s floor) and on 3.13 (Raspberry Pi OS Trixie). Copied from `pyproject.toml`.
- **No Pi libraries in the base install.** CI asserts no `piolib`/`pigpio`/`lgpio`/`rpi-*` is present. Everything in this plan must import and test without them.
- **Barcode values are 32-bit:** `0 <= value <= (1 << 32) - 1`. Enforced by `barcode.encode()`, which raises outside that range.
- **`TICK_WRAP_US = 1 << 32`** microseconds ≈ 71.6 minutes. Ticks wrap ~20× per day.
- **Session id format is `YYYY-MM-DD_NN`**, validated by `SessionId.parse` and by `SyncBoxLogHeader`. Downstream (`wl-preproc`, the ELN) consumes it, so the format does not change. The day directory is named with it: `<out>/2026-08-16_01/`.
- **Existing tests must keep passing unchanged.** 130 at the time of writing. The `Recorder` sink seam (Task 5) exists specifically so the default in-memory behaviour is untouched.

---

### Task 1: The `B` barcode record type

The sync box currently hands every other device the alignment key and keeps no copy — `log.py` has only `E` and `W` records. Everything else in this plan depends on the log containing the barcodes it emitted.

**Files:**
- Modify: `wl_sync/log.py`
- Test: `tests/test_log.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BarcodeEmitted(tick_us: int, value: int)` frozen dataclass; `Record` union widened to `Edge | CodeWord | BarcodeEmitted`; `write_log`/`read_log` round-trip it as `B,tick_us,value`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_log.py`, and add `BarcodeEmitted` to its existing `from wl_sync.log import (...)` block:

```python
def test_barcode_records_round_trip(tmp_path):
    path = tmp_path / "syncbox.log"
    records = [
        BarcodeEmitted(tick_us=1_000, value=212_000_000),
        Edge(tick_us=1_500, gpio=26, level=0),
        BarcodeEmitted(tick_us=1_001_000, value=212_000_001),
    ]
    write_log(path, HEADER, records)
    _, read_back = read_log(path)
    assert read_back == records


def test_barcode_record_line_format(tmp_path):
    """The on-disk shape is a downstream contract, so pin the literal line."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [BarcodeEmitted(tick_us=1_000, value=212_000_000)])
    assert path.read_text().splitlines()[1] == "B,1000,212000000"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_log.py -q`
Expected: FAIL — `ImportError: cannot import name 'BarcodeEmitted' from 'wl_sync.log'`

- [ ] **Step 3: Write the minimal implementation**

In `wl_sync/log.py`, add the dataclass after `CodeWord` and widen the union:

```python
@dataclass(frozen=True, slots=True)
class BarcodeEmitted:
    """A barcode frame this box emitted, with the tick of its LEAD rising edge.

    The box hands every other device this value as the alignment key; until 2026-08-16
    it kept no copy of its own, so its log could not be aligned against the devices it
    was synchronising. One record per second, ~86 KB/day against a ~1 GB day.
    """

    tick_us: int
    value: int


Record = Edge | CodeWord | BarcodeEmitted
```

Update the module docstring's format table to add:

```
    B,tick_us,value          a barcode frame this box emitted
```

In `write_log`, replace the `if/else` with:

```python
        for record in records:
            if isinstance(record, Edge):
                handle.write(f"E,{record.tick_us},{record.gpio},{record.level}\n")
            elif isinstance(record, BarcodeEmitted):
                handle.write(f"B,{record.tick_us},{record.value}\n")
            else:
                handle.write(f"W,{record.tick_us},{record.word}\n")
```

In `read_log`, add before the `else` that raises:

```python
            elif kind == "B":
                tick, value = (int(field) for field in fields)
                records.append(BarcodeEmitted(tick_us=tick, value=value))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 132 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/log.py tests/test_log.py
git commit -m "feat(log): record the barcodes this box emits"
```

---

### Task 2: The barcode counter as a clock

**Files:**
- Create: `wl_sync/clock.py`
- Test: `tests/test_clock.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `BARCODE_EPOCH: datetime.datetime` (2020-01-01T00:00:00Z)
  - `CHECKPOINT_INTERVAL_S: int = 60`
  - `value_from_clock(now: datetime.datetime) -> int`
  - `next_value(now: datetime.datetime, checkpoint: int | None) -> int`
  - `read_checkpoint(path: Path) -> int | None`
  - `write_checkpoint(path: Path, value: int) -> None`
  - `ClockTrust(trusted: bool, reason: str)` frozen dataclass
  - `evaluate_clock(now, checkpoint, ntp_synchronized: bool | None) -> ClockTrust`

- [ ] **Step 1: Write the failing test**

Create `tests/test_clock.py`:

```python
import datetime

import pytest

from wl_sync.clock import (
    BARCODE_EPOCH,
    CHECKPOINT_INTERVAL_S,
    ClockTrust,
    evaluate_clock,
    next_value,
    read_checkpoint,
    value_from_clock,
    write_checkpoint,
)

UTC = datetime.timezone.utc


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_epoch_itself_is_zero():
    assert value_from_clock(BARCODE_EPOCH) == 0


def test_one_second_is_one_count():
    assert value_from_clock(at(2020, 1, 1, 0, 0, 1)) == 1


def test_value_is_seconds_since_epoch():
    assert value_from_clock(at(2020, 1, 2)) == 86_400


def test_naive_datetime_is_rejected():
    """A naive datetime silently means 'local time', which would shift every barcode."""
    with pytest.raises(ValueError, match="timezone-aware"):
        value_from_clock(datetime.datetime(2026, 8, 16, 12, 0))


def test_before_the_epoch_is_rejected():
    with pytest.raises(ValueError, match="before the barcode epoch"):
        value_from_clock(at(2019, 12, 31))


def test_healthy_clock_ignores_the_checkpoint():
    now = at(2026, 8, 16, 12, 0)
    assert next_value(now, checkpoint=None) == value_from_clock(now)
    assert next_value(now, checkpoint=10) == value_from_clock(now)


def test_clamp_binds_when_the_clock_goes_backwards():
    """The whole point: a backwards clock must never re-issue a used value."""
    now = at(2026, 8, 16, 12, 0)
    stale = value_from_clock(now) + 10_000
    assert next_value(now, checkpoint=stale) == stale + CHECKPOINT_INTERVAL_S + 1


def test_clamp_over_states_never_under_states():
    """When the clamp binds it must sit AHEAD of the checkpoint, so a gap is
    over-stated rather than claiming coverage the box did not have."""
    now = at(2026, 8, 16, 12, 0)
    stale = value_from_clock(now) + 1
    assert next_value(now, checkpoint=stale) > stale


def test_checkpoint_round_trips(tmp_path):
    path = tmp_path / ".wl-sync-barcode-checkpoint"
    write_checkpoint(path, 212_000_000)
    assert read_checkpoint(path) == 212_000_000


def test_missing_checkpoint_reads_as_none(tmp_path):
    assert read_checkpoint(tmp_path / "absent") is None


def test_corrupt_checkpoint_reads_as_none(tmp_path):
    """A torn write must degrade to 'no checkpoint', never crash the box at 8am."""
    path = tmp_path / "checkpoint"
    path.write_text("not-a-number")
    assert read_checkpoint(path) is None


def test_clock_is_trusted_when_everything_agrees():
    now = at(2026, 8, 16, 12, 0)
    assert evaluate_clock(now, checkpoint=None, ntp_synchronized=True) == ClockTrust(
        trusted=True, reason=""
    )


def test_clock_behind_checkpoint_is_untrusted():
    now = at(2026, 8, 16, 12, 0)
    trust = evaluate_clock(
        now, checkpoint=value_from_clock(now) + 500, ntp_synchronized=True
    )
    assert trust.trusted is False
    assert "behind_checkpoint" in trust.reason


def test_unsynchronised_ntp_is_untrusted():
    trust = evaluate_clock(at(2026, 8, 16, 12, 0), checkpoint=None, ntp_synchronized=False)
    assert trust.trusted is False
    assert "ntp_unsynchronized" in trust.reason


def test_unknown_ntp_state_is_not_by_itself_untrusted():
    """timedatectl may be absent (a container, a laptop). Unknown is not the same
    as unsynchronised, and must not cry wolf on every developer machine."""
    trust = evaluate_clock(at(2026, 8, 16, 12, 0), checkpoint=None, ntp_synchronized=None)
    assert trust.trusted is True


def test_reasons_accumulate():
    now = at(2026, 8, 16, 12, 0)
    trust = evaluate_clock(
        now, checkpoint=value_from_clock(now) + 500, ntp_synchronized=False
    )
    assert "behind_checkpoint" in trust.reason and "ntp_unsynchronized" in trust.reason
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_clock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_sync.clock'`

- [ ] **Step 3: Write the minimal implementation**

Create `wl_sync/clock.py`:

```python
"""The barcode counter, which is a clock.

Until 2026-08-16 the counter was "how many barcodes this lab has ever emitted", held
in memory and passed in as a constructor argument. It is now "seconds since 2020",
derived from the wall clock, and that change exists for one reason: it makes an OUTAGE
VISIBLE IN THE SHARED KEY. A device that recorded a barcode either side of a gap
computes the gap from the values alone, and a naive consumer assuming "one barcode =
one second" stays correct across it. Contiguous numbering would have made 1000 and 1001
five hundred seconds apart and silently mis-placed every trial after the boundary.

The cost is a dependency on a clock that survives power loss, which is what the CM5 IO
Board's CR2032 is for. The clamp below is what keeps a broken clock from turning a
precision problem into a correctness one.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
CHECKPOINT_INTERVAL_S = 60
_MAX_BARCODE = (1 << 32) - 1


def value_from_clock(now: datetime.datetime) -> int:
    """Barcode value for an instant: whole seconds since the epoch."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock reading must be timezone-aware")
    seconds = int((now - BARCODE_EPOCH).total_seconds())
    if seconds < 0:
        raise ValueError(f"clock reads before the barcode epoch: {now.isoformat()}")
    if seconds > _MAX_BARCODE:
        raise ValueError(f"barcode value out of 32-bit range: {seconds}")
    return seconds


def next_value(now: datetime.datetime, checkpoint: int | None) -> int:
    """The value to resume at, clamped so it can never repeat or go backwards.

    `+ CHECKPOINT_INTERVAL_S + 1` because the checkpoint is only a periodic lower
    bound -- up to one interval of barcodes may have been emitted after it was
    written. Landing ahead of them OVER-states the gap, which marks slightly more
    trials unwitnessed than strictly were. Landing behind would claim coverage the
    box did not have, which is the failure that matters.
    """
    from_clock = value_from_clock(now)
    if checkpoint is None:
        return from_clock
    return max(from_clock, checkpoint + CHECKPOINT_INTERVAL_S + 1)


def read_checkpoint(path: Path) -> int | None:
    """The last checkpointed value, or None if absent or unreadable.

    Unreadable degrades to None rather than raising: a torn checkpoint must not stop
    the first device of the day from starting. The cost of None is only that the
    clamp cannot bind, and the clock is then the sole source -- which is the normal
    case anyway.
    """
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def write_checkpoint(path: Path, value: int) -> None:
    """Write atomically, so a crash mid-write cannot leave a truncated number that
    happens to parse as a much smaller one."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{value}\n")
    temporary.replace(path)


@dataclass(frozen=True, slots=True)
class ClockTrust:
    trusted: bool
    reason: str


def evaluate_clock(
    now: datetime.datetime, checkpoint: int | None, ntp_synchronized: bool | None
) -> ClockTrust:
    """Whether gap arithmetic across this boundary can be believed.

    `ntp_synchronized=None` means "could not tell" -- timedatectl absent, as on a
    laptop or in a container -- and is NOT treated as untrusted. Crying wolf on every
    developer machine would train people to ignore the flag on the one box where it
    means something.
    """
    reasons = []
    if checkpoint is not None and value_from_clock(now) <= checkpoint:
        reasons.append("clock_behind_checkpoint")
    if ntp_synchronized is False:
        reasons.append("ntp_unsynchronized")
    return ClockTrust(trusted=not reasons, reason=",".join(reasons))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 147 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/clock.py tests/test_clock.py
git commit -m "feat(clock): barcode counter as seconds-since-2020, with a monotonicity clamp"
```

---

### Task 3: Canonical pin map

`FRAME_TIME_GPIO` lives in `service.py` and covers two pins; the run loop needs all of them, and the log header's `gpio_map` field needs filling from one source rather than from literals at a call site.

**Files:**
- Create: `wl_sync/pins.py`
- Modify: `wl_sync/service.py`, `tests/test_service.py`
- Test: `tests/test_pins.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GPIO_MAP: dict[str, int]`, `FRAME_TIME_GPIO: dict[str, int]`, `EDGE_PINS: tuple[int, ...]`, `BARCODE_PIN: int`, `CODE_DATA_BASE: int`, `CODE_DATA_COUNT: int`, `CODE_STROBE_PIN: int`. `service.py` re-exports `FRAME_TIME_GPIO` from here so Task 5's callers are unaffected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pins.py`:

```python
from wl_sync.pins import (
    BARCODE_PIN,
    CODE_DATA_BASE,
    CODE_DATA_COUNT,
    CODE_STROBE_PIN,
    EDGE_PINS,
    FRAME_TIME_GPIO,
    GPIO_MAP,
)


def test_capture_window_is_contiguous_and_starts_at_zero():
    """PIO parallel capture reads a contiguous range; spec Sec.4 pins it at GPIO0-16."""
    assert CODE_DATA_BASE == 0
    assert CODE_DATA_COUNT == 16
    assert CODE_STROBE_PIN == CODE_DATA_BASE + CODE_DATA_COUNT


def test_barcode_is_gpio17():
    assert BARCODE_PIN == 17


def test_edge_pins_are_the_inputs_outside_the_capture_window():
    assert EDGE_PINS == (20, 21, 22, 23, 24, 25, 26, 27)


def test_frame_time_pins_match_the_board():
    assert FRAME_TIME_GPIO == {"cam_frame_eye": 26, "cam_frame_beh": 27}


def test_every_named_pin_is_in_the_gpio_map():
    for pin in (*EDGE_PINS, BARCODE_PIN, CODE_STROBE_PIN):
        assert pin in GPIO_MAP.values(), f"GPIO{pin} is used but unnamed in GPIO_MAP"


def test_gpio_map_has_no_duplicate_pins():
    assert len(set(GPIO_MAP.values())) == len(GPIO_MAP)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_pins.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_sync.pins'`

- [ ] **Step 3: Write the minimal implementation**

Create `wl_sync/pins.py`:

```python
"""The board's GPIO allocation, in one place.

Transcribed from spec Sec.4's own table. A bare pin number at a call site survives a
board change silently, which is this project's named recurring failure -- right about
the model, wrong about the board.
"""

from __future__ import annotations

CODE_DATA_BASE = 0
CODE_DATA_COUNT = 16
CODE_STROBE_PIN = 16
BARCODE_PIN = 17

# Camera frame-time inputs, one exposure strobe per camera group. These were spare
# until 2026-08-16; the cameras free-run, so this box records when they exposed rather
# than telling them when to. See hardware/breakout/frame-time-inputs.md.
FRAME_TIME_GPIO = {"cam_frame_eye": 26, "cam_frame_beh": 27}

GPIO_MAP: dict[str, int] = {
    **{f"evt_d{i}": CODE_DATA_BASE + i for i in range(CODE_DATA_COUNT)},
    "evt_strobe": CODE_STROBE_PIN,
    "barcode_out": BARCODE_PIN,
    "pd1_comp": 20,
    "pd2_comp": 21,
    "rwd_cmd": 22,
    "rwd_dlvr": 23,
    "stim_trig": 24,
    "acc_trig": 25,
    **FRAME_TIME_GPIO,
}

# Individually-captured input transitions: everything above the contiguous capture
# window. GPIO18/19 are spare.
EDGE_PINS: tuple[int, ...] = (20, 21, 22, 23, 24, 25, 26, 27)
```

In `wl_sync/service.py`, delete the `FRAME_TIME_GPIO = {...}` definition and its block comment, and import it instead:

```python
from wl_sync.pins import FRAME_TIME_GPIO
```

Keep `_FALLING = 0` where it is. `tests/test_service.py` needs no change: it imports `FRAME_TIME_GPIO` from `wl_sync.service`, which still re-exports it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 153 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/pins.py wl_sync/service.py tests/test_pins.py
git commit -m "feat(pins): one canonical GPIO map instead of literals at call sites"
```

---

### Task 4: Segment writer

**Files:**
- Create: `wl_sync/segment.py`
- Test: `tests/test_segment.py`

**Interfaces:**
- Consumes: `BarcodeEmitted`, `Edge`, `CodeWord`, `Record`, `SyncBoxLogHeader` from `wl_sync.log`.
- Produces:
  - `SEGMENT_PATTERN: str = "seg-*.log"`
  - `segment_name(index: int) -> str`
  - `next_segment_index(day_dir: Path) -> int`
  - `SegmentWriter(path: Path, header: SyncBoxLogHeader)` with `.write(record) -> None`, `.flush() -> None`, `.close(closed_at: datetime.datetime) -> None`, and read-only attributes `.barcode_first: int | None`, `.barcode_last: int | None`, `.record_count: int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_segment.py`:

```python
import datetime

from wl_sync.log import BarcodeEmitted, Edge, read_log
from wl_sync.segment import SegmentWriter, next_segment_index, segment_name
from tests.test_log import HEADER

UTC = datetime.timezone.utc
CLOSED_AT = datetime.datetime(2026, 8, 16, 17, 0, tzinfo=UTC)


def test_segment_names_are_zero_padded_and_sort_lexically():
    assert segment_name(0) == "seg-000.log"
    assert segment_name(12) == "seg-012.log"
    assert sorted([segment_name(2), segment_name(10)]) == [segment_name(2), segment_name(10)]


def test_first_segment_in_a_new_directory_is_zero(tmp_path):
    assert next_segment_index(tmp_path) == 0


def test_next_index_follows_the_highest_present(tmp_path):
    (tmp_path / segment_name(0)).touch()
    (tmp_path / segment_name(1)).touch()
    assert next_segment_index(tmp_path) == 2


def test_next_index_ignores_unrelated_files(tmp_path):
    (tmp_path / segment_name(0)).touch()
    (tmp_path / "manifest.json").touch()
    assert next_segment_index(tmp_path) == 1


def test_records_are_readable_before_close(tmp_path):
    """The whole point of streaming: a crash keeps everything already written."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.write(Edge(tick_us=1_500, gpio=26, level=0))
    writer.flush()
    _, records = read_log(path)
    assert records == [
        BarcodeEmitted(tick_us=1_000, value=212_000_000),
        Edge(tick_us=1_500, gpio=26, level=0),
    ]


def test_a_clean_close_writes_a_trailer(tmp_path):
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.write(BarcodeEmitted(tick_us=2_000, value=212_000_001))
    writer.close(CLOSED_AT)
    assert path.read_text().splitlines()[-1] == (
        f"T,{CLOSED_AT.isoformat()},212000000,212000001,2"
    )


def test_an_unclosed_segment_has_no_trailer(tmp_path):
    """Simulates a crash: the writer is abandoned without close()."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.flush()
    assert not path.read_text().splitlines()[-1].startswith("T,")


def test_barcode_span_tracks_what_was_written(tmp_path):
    writer = SegmentWriter(tmp_path / segment_name(0), HEADER)
    assert writer.barcode_first is None and writer.barcode_last is None
    writer.write(BarcodeEmitted(tick_us=1_000, value=500))
    writer.write(Edge(tick_us=1_200, gpio=26, level=0))
    writer.write(BarcodeEmitted(tick_us=2_000, value=501))
    assert (writer.barcode_first, writer.barcode_last, writer.record_count) == (500, 501, 3)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_segment.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_sync.segment'`

- [ ] **Step 3: Write the minimal implementation**

Create `wl_sync/segment.py`:

```python
"""One log segment: one process run's worth of records, appended as they arrive.

WHY SEGMENTS AND NOT ONE FILE PER DAY. RP1's tick counter wraps every ~71.6 minutes and
the unwrap offset is in memory, so ticks after a restart are not comparable BY NUMBER to
ticks before it. A single file under a single header would be lying about its own
timebase. One header per segment states one clock epoch, which is true.

WHY THE TRAILER IS THE CRASH TEST. Its presence means the segment was closed cleanly;
its absence means it was not. Nothing else is consulted, so the distinction never depends
on a dying process managing to record its own death. A torn final line is likewise
trailer-less, and therefore crash-closed, which is correct.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

from wl_sync.log import BarcodeEmitted, Edge, Record, SyncBoxLogHeader

SEGMENT_PATTERN = "seg-*.log"
_SEGMENT_RE = re.compile(r"^seg-(\d+)\.log$")


def segment_name(index: int) -> str:
    return f"seg-{index:03d}.log"


def next_segment_index(day_dir: Path) -> int:
    """One past the highest segment present. Derived from the filesystem, so a restart
    needs no memory of how many runs preceded it."""
    indices = [
        int(match.group(1))
        for path in day_dir.glob(SEGMENT_PATTERN)
        if (match := _SEGMENT_RE.match(path.name))
    ]
    return max(indices) + 1 if indices else 0


class SegmentWriter:
    """Append-only sink. Satisfies the `write(record)` protocol `Recorder` expects."""

    def __init__(self, path: Path, header: SyncBoxLogHeader) -> None:
        self._path = path
        self._handle = path.open("w", encoding="utf-8")
        self._handle.write(json.dumps(header.model_dump(mode="json")) + "\n")
        self.barcode_first: int | None = None
        self.barcode_last: int | None = None
        self.record_count = 0
        self.flush()

    def write(self, record: Record) -> None:
        if isinstance(record, Edge):
            line = f"E,{record.tick_us},{record.gpio},{record.level}"
        elif isinstance(record, BarcodeEmitted):
            line = f"B,{record.tick_us},{record.value}"
            if self.barcode_first is None:
                self.barcode_first = record.value
            self.barcode_last = record.value
        else:
            line = f"W,{record.tick_us},{record.word}"
        self._handle.write(line + "\n")
        self.record_count += 1

    def flush(self) -> None:
        """Push to the platter, not merely to the OS. At ~40 KB/s onto the NVMe spec
        Sec.4.1 chose, once per second is free and bounds crash loss to one second."""
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self, closed_at: datetime.datetime) -> None:
        self._handle.write(
            f"T,{closed_at.isoformat()},{self.barcode_first},"
            f"{self.barcode_last},{self.record_count}\n"
        )
        self.flush()
        self._handle.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 161 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/segment.py tests/test_segment.py
git commit -m "feat(segment): append-only segment writer with a crash-detecting trailer"
```

---

### Task 5: The `Recorder` sink seam

**Files:**
- Modify: `wl_sync/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Record` from `wl_sync.log`.
- Produces: `Recorder(backend, sink=None)`. `sink` is any object with `write(record) -> None`. Default behaviour — accumulate in memory, `records()` returns tick-sorted — is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_service.py`:

```python
class _CollectingSink:
    def __init__(self):
        self.written = []

    def write(self, record):
        self.written.append(record)


def test_recorder_streams_to_a_sink_in_arrival_order():
    """Arrival order, NOT tick order: the two capture paths deliver independently, so a
    stream cannot be globally sorted. The header declares ordering='per-path' and the
    reader merges. See the design spec, section 5."""
    backend = FakeBackend()
    sink = _CollectingSink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 2_000)
    backend.inject_edge(REWARD_PIN, 0, 1_000)
    assert [record.tick_us for record in sink.written] == [2_000, 1_000]


def test_a_sink_replaces_in_memory_accumulation():
    """Nothing may accumulate when a sink is given, or an all-day run exhausts RAM."""
    backend = FakeBackend()
    recorder = Recorder(backend, sink=_CollectingSink())
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 1_000)
    assert recorder.records() == []


def test_the_default_recorder_is_unchanged():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 2_000)
    backend.inject_edge(REWARD_PIN, 0, 1_000)
    assert [record.tick_us for record in recorder.records()] == [1_000, 2_000]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_service.py -q`
Expected: FAIL — `TypeError: Recorder.__init__() got an unexpected keyword argument 'sink'`

- [ ] **Step 3: Write the minimal implementation**

In `wl_sync/service.py`, replace `Recorder.__init__` and add the sink dispatch:

```python
class _MemorySink:
    """The historical behaviour: hold everything, sort on read. Correct for tests and
    short runs; fatal for a whole day, which is what SegmentWriter is for."""

    def __init__(self) -> None:
        self.records: list[Record] = []

    def write(self, record: Record) -> None:
        self.records.append(record)


class Recorder:
    """Collects strobed code words and individual edges onto one unwrapped clock.

    `sink` takes each record as it arrives. The default accumulates in memory and
    `records()` returns them tick-sorted. A streaming sink (`SegmentWriter`) receives
    them in ARRIVAL order instead, because the two capture paths deliver independently
    and a stream cannot be globally sorted -- the segment header declares
    `ordering="per-path"` and the reader merges.
    """

    def __init__(self, backend: SyncBackend, sink=None) -> None:
        self._backend = backend
        self._memory = _MemorySink() if sink is None else None
        self._sink = self._memory if sink is None else sink
        self._unwrap_words = _TickUnwrapper()
        self._unwrap_edges = _TickUnwrapper()
```

Change the two callbacks to write through the sink:

```python
    def _on_word(self, tick: int, word: int) -> None:
        self._sink.write(CodeWord(tick_us=self._unwrap_words(tick), word=word))

    def _on_edge(self, gpio: int, level: int, tick: int) -> None:
        self._sink.write(
            Edge(tick_us=self._unwrap_edges(tick), gpio=gpio, level=level)
        )
```

And `records()`:

```python
    def records(self) -> list[Record]:
        """Tick-sorted, and empty when a streaming sink was supplied -- nothing is held."""
        if self._memory is None:
            return []
        return sorted(self._memory.records, key=lambda record: record.tick_us)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 164 tests. The pre-existing `Recorder` tests must pass **unchanged**.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/service.py tests/test_service.py
git commit -m "feat(service): give Recorder a sink seam so records can stream to disk"
```

---

### Task 6: The manifest

**Files:**
- Create: `wl_sync/manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `segment_name`, `SEGMENT_PATTERN` from `wl_sync.segment`; `read_log` from `wl_sync.log`.
- Produces:
  - `SegmentSummary(file: str, barcode_first: int | None, barcode_last: int | None, clock_trusted: bool, closed: str)` frozen dataclass
  - `summarise_segment(path: Path) -> SegmentSummary`
  - `build_manifest(day_dir: Path) -> dict`
  - `write_manifest(day_dir: Path) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_manifest.py`:

```python
import datetime
import json

from wl_sync.log import BarcodeEmitted
from wl_sync.manifest import build_manifest, summarise_segment, write_manifest
from wl_sync.segment import SegmentWriter, segment_name
from tests.test_log import HEADER

UTC = datetime.timezone.utc
CLOSED_AT = datetime.datetime(2026, 8, 16, 17, 0, tzinfo=UTC)


def _segment(day_dir, index, first, last, *, clean):
    writer = SegmentWriter(day_dir / segment_name(index), HEADER)
    for value in range(first, last + 1):
        writer.write(BarcodeEmitted(tick_us=value * 1_000_000, value=value))
    if clean:
        writer.close(CLOSED_AT)
    else:
        writer.flush()
    return writer


def test_a_closed_segment_summarises_as_clean(tmp_path):
    _segment(tmp_path, 0, 100, 103, clean=True)
    summary = summarise_segment(tmp_path / segment_name(0))
    assert (summary.closed, summary.barcode_first, summary.barcode_last) == ("clean", 100, 103)


def test_an_unclosed_segment_summarises_as_crash(tmp_path):
    _segment(tmp_path, 0, 100, 103, clean=False)
    assert summarise_segment(tmp_path / segment_name(0)).closed == "crash"


def test_a_torn_final_line_still_summarises(tmp_path):
    """A crash mid-write leaves a partial line. It must not raise, and the segment must
    still report the barcodes that DID land."""
    path = tmp_path / segment_name(0)
    _segment(tmp_path, 0, 100, 103, clean=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("B,999999,10")  # no newline: torn
    summary = summarise_segment(path)
    assert summary.closed == "crash"
    assert summary.barcode_last == 103


def test_a_gap_between_segments_is_reported(tmp_path):
    _segment(tmp_path, 0, 1_000, 4_200, clean=False)
    _segment(tmp_path, 1, 4_700, 9_000, clean=True)
    manifest = build_manifest(tmp_path)
    assert manifest["gaps"] == [{"after": 4_200, "before": 4_700, "seconds": 500}]


def test_contiguous_segments_report_no_gap(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    _segment(tmp_path, 1, 1_101, 1_200, clean=True)
    assert build_manifest(tmp_path)["gaps"] == []


def test_segments_are_ordered_by_index_not_by_name_luck(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    _segment(tmp_path, 1, 1_200, 1_300, clean=True)
    files = [entry["file"] for entry in build_manifest(tmp_path)["segments"]]
    assert files == [segment_name(0), segment_name(1)]


def test_write_manifest_lands_on_disk(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    write_manifest(tmp_path)
    written = json.loads((tmp_path / "manifest.json").read_text())
    assert written["segments"][0]["barcode_last"] == 1_100


def test_manifest_is_rebuilt_not_appended(tmp_path):
    """It is derived state. A stale manifest from a crashed run must be replaced
    wholesale, never merged into."""
    (tmp_path / "manifest.json").write_text(json.dumps({"segments": ["nonsense"]}))
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    assert write_manifest(tmp_path)["segments"][0]["file"] == segment_name(0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_sync.manifest'`

- [ ] **Step 3: Write the minimal implementation**

Create `wl_sync/manifest.py`:

```python
"""The day's manifest: which segments exist, and where the box was NOT watching.

`gaps` is the point. The other recording devices keep running while this box is down,
so trials happen during an outage; they really occurred and must be recognisable as
UNWITNESSED rather than silently absent. Enumerating gaps lets wl-preproc say so
positively instead of discovering a hole.

The manifest is DERIVED STATE, rebuilt by scanning segments at every start. It is never
authoritative and never merged into, so a crash cannot leave it stale.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from wl_sync.segment import SEGMENT_PATTERN, _SEGMENT_RE

_TRAILER_RE = re.compile(r"^T,")
_BARCODE_RE = re.compile(r"^B,(\d+),(\d+)$")


@dataclass(frozen=True, slots=True)
class SegmentSummary:
    file: str
    barcode_first: int | None
    barcode_last: int | None
    clock_trusted: bool
    closed: str


def summarise_segment(path: Path) -> SegmentSummary:
    """Read a segment's header and barcode span without holding its records.

    Parsed line by line rather than via read_log() because a crashed segment's last line
    may be torn, and because a day's segment can hold tens of millions of records.
    """
    barcode_first: int | None = None
    barcode_last: int | None = None
    closed = "crash"
    clock_trusted = True
    with path.open("r", encoding="utf-8") as handle:
        header_line = handle.readline()
        try:
            clock_trusted = bool(json.loads(header_line).get("clock_trusted", True))
        except (json.JSONDecodeError, AttributeError):
            clock_trusted = False
        for line in handle:
            if not line.endswith("\n"):
                break  # torn final line: a crash caught mid-write
            if _TRAILER_RE.match(line):
                closed = "clean"
                continue
            match = _BARCODE_RE.match(line.rstrip("\n"))
            if match:
                value = int(match.group(2))
                if barcode_first is None:
                    barcode_first = value
                barcode_last = value
    return SegmentSummary(
        file=path.name,
        barcode_first=barcode_first,
        barcode_last=barcode_last,
        clock_trusted=clock_trusted,
        closed=closed,
    )


def build_manifest(day_dir: Path) -> dict:
    paths = sorted(
        (path for path in day_dir.glob(SEGMENT_PATTERN) if _SEGMENT_RE.match(path.name)),
        key=lambda path: int(_SEGMENT_RE.match(path.name).group(1)),
    )
    summaries = [summarise_segment(path) for path in paths]

    gaps = []
    for previous, following in zip(summaries, summaries[1:]):
        if previous.barcode_last is None or following.barcode_first is None:
            continue
        seconds = following.barcode_first - previous.barcode_last
        if seconds > 1:
            gaps.append(
                {
                    "after": previous.barcode_last,
                    "before": following.barcode_first,
                    "seconds": seconds,
                }
            )

    return {
        "day": day_dir.name,
        "segments": [asdict(summary) for summary in summaries],
        "gaps": gaps,
    }


def write_manifest(day_dir: Path) -> dict:
    manifest = build_manifest(day_dir)
    path = day_dir / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return manifest
```

In `wl_sync/segment.py`, `_SEGMENT_RE` is now imported by another module — leave the name as is but add a comment noting the cross-module use:

```python
_SEGMENT_RE = re.compile(r"^seg-(\d+)\.log$")  # also used by manifest.py's ordering
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 172 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/manifest.py wl_sync/segment.py tests/test_manifest.py
git commit -m "feat(manifest): enumerate segments and the gaps where the box was not watching"
```

---

### Task 7: Header fields for segments

**Files:**
- Modify: `wl_sync/log.py`
- Test: `tests/test_log.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SyncBoxLogHeader` gains `segment_index: int`, `ordering: str`, `clock_trusted: bool`, `clock_reason: str`, all with defaults so existing construction sites keep working.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_log.py`:

```python
def test_header_defaults_describe_a_single_trusted_segment():
    assert (HEADER.segment_index, HEADER.ordering) == (0, "per-path")
    assert (HEADER.clock_trusted, HEADER.clock_reason) == (True, "")


def test_header_carries_clock_distrust(tmp_path):
    header = HEADER.model_copy(
        update={"clock_trusted": False, "clock_reason": "ntp_unsynchronized"}
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [])
    read_back, _ = read_log(path)
    assert read_back.clock_trusted is False
    assert read_back.clock_reason == "ntp_unsynchronized"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_log.py -q`
Expected: FAIL — `AttributeError: 'SyncBoxLogHeader' object has no attribute 'segment_index'`

- [ ] **Step 3: Write the minimal implementation**

In `wl_sync/log.py`, add to `SyncBoxLogHeader` after `gpio_map`:

```python
    segment_index: int = 0
    # Records reach disk in ARRIVAL order, and the two capture paths deliver
    # independently, so the file is ordered WITHIN each path and not across them. A
    # reader merges. Declared rather than faked: a reorder buffer would invent a tuning
    # parameter and still need a late-arrival escape hatch, for a property the reader
    # recovers for free.
    ordering: str = "per-path"
    clock_trusted: bool = True
    clock_reason: str = ""
```

`model_config` already has `extra="forbid"` and `frozen=True`; defaults keep every existing construction site valid.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 174 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/log.py tests/test_log.py
git commit -m "feat(log): segment index, ordering and clock-trust in the header"
```

---

### Task 8: The run loop and CLI

**Files:**
- Create: `wl_sync/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above, plus `FakeBackend`, `BarcodeGenerator`, `Recorder` from existing modules.
- Produces:
  - `day_directory(out: Path, today: datetime.date) -> Path`
  - `probe_ntp_synchronized() -> bool | None`
  - `run(out: Path, backend, now_fn, duration_s: float | None, tick_fn) -> Path`
  - `main(argv: list[str] | None = None) -> int`
  - Console script `wl-sync`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import datetime
import json

from wl_sync.backend import FakeBackend
from wl_sync.cli import day_directory, main, run

UTC = datetime.timezone.utc


def test_day_directory_uses_the_session_id_format(tmp_path):
    """SyncBoxLogHeader validates session_id as YYYY-MM-DD_NN and wl-preproc consumes
    it, so the directory carries that form rather than a bare date."""
    day = day_directory(tmp_path, datetime.date(2026, 8, 16))
    assert day.name == "2026-08-16_01"


def test_the_same_day_is_reused_by_a_restart(tmp_path):
    first = day_directory(tmp_path, datetime.date(2026, 8, 16))
    first.mkdir(parents=True)
    assert day_directory(tmp_path, datetime.date(2026, 8, 16)) == first


def test_a_short_run_writes_a_clean_segment_and_manifest(tmp_path):
    clock = iter(
        datetime.datetime(2026, 8, 16, 9, 0, second=s, tzinfo=UTC) for s in range(10)
    )
    day = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=lambda: next(clock),
        duration_s=3,
        tick_fn=lambda _seconds: None,
    )
    manifest = json.loads((day / "manifest.json").read_text())
    assert manifest["segments"][0]["closed"] == "clean"
    assert manifest["gaps"] == []
    assert manifest["segments"][0]["barcode_first"] is not None


def test_a_restart_adds_a_segment_and_records_the_gap(tmp_path):
    def at(hour, minute):
        return datetime.datetime(2026, 8, 16, hour, minute, tzinfo=UTC)

    first = iter([at(9, 0), at(9, 0), at(9, 0), at(9, 0)])
    run(tmp_path, FakeBackend(), lambda: next(first), 2, lambda _s: None)
    later = iter([at(9, 30), at(9, 30), at(9, 30), at(9, 30)])
    day = run(tmp_path, FakeBackend(), lambda: next(later), 2, lambda _s: None)

    manifest = json.loads((day / "manifest.json").read_text())
    assert len(manifest["segments"]) == 2
    assert len(manifest["gaps"]) == 1
    assert manifest["gaps"][0]["seconds"] == 1_800


def test_main_requires_out(capsys):
    assert main(["record"]) != 0


def test_main_runs_end_to_end(tmp_path):
    assert main(["record", "--out", str(tmp_path), "--fake", "--duration", "2"]) == 0
    days = list(tmp_path.glob("2*_01"))
    assert len(days) == 1
    assert (days[0] / "manifest.json").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_sync.cli'`

- [ ] **Step 3: Write the minimal implementation**

Create `wl_sync/cli.py`:

```python
"""`wl-sync record` -- the run loop that makes this a sync box rather than a library.

The box is the first device on each day and the last off, and it runs continuously
while every other device starts and stops blocks inside that run. So a SESSION IS A DAY,
one segment file per process run, and a restart joins the day rather than beginning a
new one. systemd's Restart=always is what makes the stitching automatic.
"""

from __future__ import annotations

import argparse
import datetime
import signal
import subprocess
import time
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.clock import (
    CHECKPOINT_INTERVAL_S,
    evaluate_clock,
    next_value,
    read_checkpoint,
    write_checkpoint,
)
from wl_sync.log import BarcodeEmitted, SyncBoxLogHeader
from wl_sync.manifest import write_manifest
from wl_sync.pins import (
    BARCODE_PIN,
    CODE_DATA_BASE,
    CODE_DATA_COUNT,
    CODE_STROBE_PIN,
    EDGE_PINS,
    GPIO_MAP,
)
from wl_sync.segment import SegmentWriter, next_segment_index, segment_name
from wl_sync.service import BarcodeGenerator, Recorder

CHECKPOINT_NAME = ".wl-sync-barcode-checkpoint"


def day_directory(out: Path, today: datetime.date) -> Path:
    """`<out>/YYYY-MM-DD_01`. Index 01 because a day is one session; the `_NN` form is
    kept because SyncBoxLogHeader validates it and wl-preproc consumes it."""
    return out / f"{today.isoformat()}_01"


def probe_ntp_synchronized() -> bool | None:
    """True/False from timedatectl, or None when it cannot be asked -- a laptop, a
    container. None is 'unknown', which evaluate_clock deliberately does not treat as
    untrusted."""
    try:
        completed = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() == "yes"


def run(out: Path, backend, now_fn, duration_s: float | None, tick_fn) -> Path:
    """Record until `duration_s` elapses or a termination signal arrives.

    `now_fn` and `tick_fn` are injected so the whole loop is testable without sleeping:
    tests pass a scripted clock and a no-op sleep.
    """
    started = now_fn()
    day = day_directory(out, started.date())
    day.mkdir(parents=True, exist_ok=True)

    # Rebuild first, so any segment left trailer-less by a crash is recorded as such
    # before this run adds its own.
    write_manifest(day)

    checkpoint_path = out / CHECKPOINT_NAME
    checkpoint = read_checkpoint(checkpoint_path)
    trust = evaluate_clock(started, checkpoint, probe_ntp_synchronized())
    first_value = next_value(started, checkpoint)

    index = next_segment_index(day)
    header = SyncBoxLogHeader(
        schema_version=1,
        session_id=day.name,
        rig=out.name,
        boot_id=f"{started.timestamp():.0f}",
        written_at=started,
        gpio_map=GPIO_MAP,
        segment_index=index,
        clock_trusted=trust.trusted,
        clock_reason=trust.reason,
    )
    writer = SegmentWriter(day / segment_name(index), header)

    recorder = Recorder(backend, sink=writer)
    recorder.capture_codes(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN)
    recorder.capture_edges(EDGE_PINS)

    values = _ValueSource(now_fn, first_value)
    generator = BarcodeGenerator(backend, BARCODE_PIN, values)

    stopping = _StopFlag()
    for received in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(received, stopping)
        except ValueError:
            pass  # not the main thread, e.g. under a test runner

    elapsed = 0.0
    since_checkpoint = 0
    while not stopping.set and (duration_s is None or elapsed < duration_s):
        value = generator.emit_frame()
        writer.write(BarcodeEmitted(tick_us=backend.now_us(), value=value))
        writer.flush()
        since_checkpoint += 1
        if since_checkpoint >= CHECKPOINT_INTERVAL_S:
            write_checkpoint(checkpoint_path, value)
            since_checkpoint = 0
        tick_fn(1.0)
        elapsed += 1.0

    writer.close(now_fn())
    write_checkpoint(checkpoint_path, writer.barcode_last or first_value)
    write_manifest(day)
    return day


class _ValueSource:
    """Barcode values from the wall clock, anchored at the value this run resumed on."""

    def __init__(self, now_fn, first_value: int) -> None:
        self._now_fn = now_fn
        self._first = first_value
        self._emitted = 0

    def __call__(self) -> int:
        value = max(next_value(self._now_fn(), None), self._first + self._emitted)
        self._emitted += 1
        return value


class _StopFlag:
    def __init__(self) -> None:
        self.set = False

    def __call__(self, _signum, _frame) -> None:
        self.set = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wl-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="record a day")
    record.add_argument("--out", type=Path, required=True, help="rig session root")
    record.add_argument("--fake", action="store_true", help="run on the in-memory backend")
    record.add_argument("--duration", type=float, default=None, help="stop after S seconds")
    args = parser.parse_args(argv)

    if not args.fake:
        print("only --fake is available until the RP1 backend (Task 5b) lands")
        return 2

    day = run(
        out=args.out,
        backend=FakeBackend(),
        now_fn=lambda: datetime.datetime.now(datetime.timezone.utc),
        duration_s=args.duration,
        tick_fn=time.sleep,
    )
    print(f"recorded {day}")
    return 0
```

In `wl_sync/service.py`, change `BarcodeGenerator` to take a value source instead of a start value:

```python
class BarcodeGenerator:
    """Emits one barcode frame per call.

    `value_source` is called for each frame and returns the value to emit -- normally
    the wall clock, since a barcode value IS seconds since 2020 (see wl_sync.clock).
    The result is clamped to strictly exceed the previous one, so monotonicity holds at
    the point of emission whatever the source does. A repeated barcode would be two
    different moments carrying one identity, which is the one thing it must never be.
    """

    def __init__(self, backend: SyncBackend, pin: int, value_source) -> None:
        self._backend = backend
        self._pin = pin
        self._value_source = value_source
        self._last: int | None = None

    def emit_frame(self) -> int:
        value = self._value_source()
        if self._last is not None and value <= self._last:
            value = self._last + 1
        self._backend.emit_pulses(self._pin, encode(value))
        self._last = value
        return value
```

Update the two existing generator tests in `tests/test_service.py` to pass a source:

```python
def test_generator_emits_a_decodable_frame():
    backend = FakeBackend()
    BarcodeGenerator(backend, pin=BARCODE_PIN, value_source=lambda: 1000).emit_frame()
    assert len(backend.emitted) == 1
    pin, pulses = backend.emitted[0]
    assert pin == BARCODE_PIN
    edges = pulses_to_edges(pulses, IDLE_MIN_US)
    assert [b.value for b in decode_edges(edges, start_us=0)] == [1000]


def test_generator_never_repeats_a_value():
    """A stalled clock must not re-issue an identity."""
    backend = FakeBackend()
    generator = BarcodeGenerator(backend, pin=BARCODE_PIN, value_source=lambda: 7)
    assert [generator.emit_frame() for _ in range(3)] == [7, 8, 9]
```

Add the console script to `pyproject.toml` after `[project.optional-dependencies]`:

```toml
[project.scripts]
wl-sync = "wl_sync.cli:main"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: PASS, 180 tests.

Then confirm it really is launchable:

```bash
pip install -e .
wl-sync record --out /tmp/wl-sync-demo --fake --duration 5
cat /tmp/wl-sync-demo/*/manifest.json
```

Expected: a day directory containing `seg-000.log` and a `manifest.json` whose single segment reports `"closed": "clean"`.

- [ ] **Step 5: Commit**

```bash
git add wl_sync/cli.py wl_sync/service.py pyproject.toml tests/test_cli.py tests/test_service.py
git commit -m "feat(cli): wl-sync record -- the run loop that makes this launchable"
```

---

### Task 9: The systemd unit and operator documentation

**Files:**
- Create: `deploy/wl-sync.service`, `deploy/README.md`
- Test: none (a unit file is configuration; its behaviour is verified at bring-up)

**Interfaces:**
- Consumes: the `wl-sync` console script from Task 8.
- Produces: a unit whose `Restart=always` is what makes crash-stitching automatic.

- [ ] **Step 1: Write the unit**

Create `deploy/wl-sync.service`:

```ini
[Unit]
Description=wl-sync: master timebase for the recording rig
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wl-sync record --out /data/rig1
Restart=always
RestartSec=1
# The box is the first device on each day and the last off. Restart=always is what
# makes stitching automatic: it comes back, derives its barcode counter from the
# clock, opens the next segment, and the outage lands in the day's manifest with
# nobody present.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the operator notes**

Create `deploy/README.md`:

```markdown
# Deploying the sync box

    sudo cp deploy/wl-sync.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now wl-sync

Edit `--out` in the unit to the rig's session root before enabling.

## Checking it

    systemctl status wl-sync          # is it running
    journalctl -u wl-sync -f          # what is it saying
    cat /data/rig1/$(date +%F)_01/manifest.json

The manifest is the thing to read. `gaps` lists spans where this box was **not
watching** — trials in those spans really happened and were recorded by the other
devices, but carry no sync-box coverage.

`clock_trusted: false` on a segment means gap arithmetic across that boundary is
unreliable. The commonest cause is a missing CR2032 on the CM5 IO Board; see
`hardware/assembly-checklist.md`.

## Before first boot

- Fit the CR2032 to the CM5 IO Board. Nothing on the board can detect its absence.
- Configure NTP. `timedatectl show -p NTPSynchronized` must report `yes`.
```

- [ ] **Step 3: Verify the unit parses**

Run: `systemd-analyze verify deploy/wl-sync.service`
Expected: no output. (Skip if `systemd-analyze` is unavailable on the development machine; it is present on the Pi.)

- [ ] **Step 4: Commit**

```bash
git add deploy/
git commit -m "feat(deploy): systemd unit whose Restart=always makes stitching automatic"
```

---

## Plan self-review

**Spec coverage.** §3 record on disk → Tasks 1, 4, 6, 7. §4 counter as a clock → Task 2, plus the generator change in Task 8. §5 streaming and durability → Tasks 4 and 5. §6 entry point and lifecycle → Tasks 8 and 9. §7 clock trust → Task 2 (`evaluate_clock`) and Task 7 (header fields). §8 testing → every task's own tests. §10 decisions 1 and 2 are documentation and bring-up configuration, covered by Task 9's `deploy/README.md` and the assembly checklist referenced there. **§10 decision 3, the GPIO18 status LED, is deliberately NOT in this plan** — it is a hardware change with a different toolchain and its own verification, and belongs in the companion plan noted below.

**Type consistency.** `SegmentWriter.write` matches the `sink.write(record)` protocol Task 5 defines. `summarise_segment` imports `_SEGMENT_RE` from `segment.py`, which Task 6 annotates as a deliberate cross-module use. `BarcodeGenerator(backend, pin, value_source)` is used consistently in Task 8's `run()` and in the updated tests. `next_value(now, checkpoint)` has the same signature in Task 2 and both call sites in Task 8.

**Known follow-on.** The GPIO18 status LED needs its own plan covering the schematic change in `gen_breakout_pi_interface.py`, `SPARE_GPIOS` in the pi-interface checker, the spare count in spec §4 / `camera-sync-change.md` / `frame-time-inputs.md`, the BOM, and the blink vocabulary plus the software that drives it. **It blocks layout**, so it should be written and executed before the board is laid out, independently of this plan's schedule.
