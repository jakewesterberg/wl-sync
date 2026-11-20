"""The shim's reading of the kernel's FIFO-state answer, compiled and callable from Python.

`pio/wlsync_fifo_state.h` holds the rule the shim's level read applies to the answer to
PIO_IOC_SM_FIFO_STATE (pio/wlsync_pio.c, `wlsync_sm_rx_level`). It needs nothing of piolib,
so this compiles it, with the host's C compiler, into a small shared library, once a
process, and hands the tests the very rule the box runs: the harness test checks it case by
case, the fuzz reads every level through it (tests/test_pio_capture.py), and the backend's
fake can answer through it as the box's shim would (tests/test_rp1_backend.py).

`answer()` is None on a host with no C compiler, and the tests that need it skip.
"""

from __future__ import annotations

import ctypes
import pathlib
import shutil
import subprocess
import tempfile

HEADER = pathlib.Path(__file__).resolve().parent.parent / "pio" / "wlsync_fifo_state.h"
LEVEL_SEED, FLAG_SEED = 0xFFFF, 0xFF
_cached: list = []


def answer():
    """`wlsync_fifo_state_answer(result, error, level, empty, full, depth)`, or None."""
    if _cached:
        return _cached[0]
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        _cached.append(None)
        return None
    built = pathlib.Path(tempfile.mkdtemp(prefix="wlsync-fifo-state-"))
    source = built / "fifo_state.c"
    source.write_text(
        f'#include "{HEADER}"\n'
        "int answer(int result, int error, unsigned level, unsigned empty,\n"
        "           unsigned full, unsigned depth)\n"
        "{\n"
        "    return wlsync_fifo_state_answer(result, error, level, empty, full, depth);\n"
        "}\n"
    )
    library = built / "libfifo_state.so"
    subprocess.run([cc, "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o",
                    str(library), str(source)], check=True)
    function = ctypes.CDLL(str(library)).answer
    function.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_uint] * 4
    function.restype = ctypes.c_int
    _cached.append(function)
    return function
