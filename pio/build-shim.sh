#!/bin/sh
# Build libwlsync_pio.so: pio/wlsync_pio.c together with piolib's own sources, as ONE
# shared library, so the box needs no separately installed libpio. See wlsync_pio.c
# for why the shim exists at all.
#
#   pio/build-shim.sh <raspberrypi/utils checkout>/piolib [output directory]
#
# It REFUSES (exit 1, before the compiler runs, so no .so is written) unless piolib is at
# raspberrypi/utils b57c48e, the one commit the shim's read of piolib's private RP1 handle was
# checked against. To build against another commit anyway, set WLSYNC_ALLOW_UNCHECKED_PIOLIB=1:
# the build then goes ahead and prints a warning. A directory that is not a git checkout reads
# as "unknown" and is refused the same way.
#
# Then install it where ctypes.util.find_library("wlsync_pio") looks:
#
#   sudo install -m 0644 <output>/libwlsync_pio.so /usr/local/lib/ && sudo ldconfig
set -eu

piolib=${1:?usage: build-shim.sh <path to raspberrypi/utils/piolib> [output dir]}
out=${2:-.}
here=$(cd "$(dirname "$0")" && pwd)

checked=b57c48e
at=$(git -C "$piolib" rev-parse HEAD 2>/dev/null || echo unknown)
case $at in
    $checked*) ;;
    *)
        if [ "${WLSYNC_ALLOW_UNCHECKED_PIOLIB:-}" = 1 ]; then
            echo "warning: piolib is at $at, not utils $checked, the commit the shim's read of" \
                 "its private handle was checked against (deploy/README.md); building anyway," \
                 "WLSYNC_ALLOW_UNCHECKED_PIOLIB=1" >&2
        else
            cat >&2 <<MSG
error: piolib is at $at, but the shim was checked against utils $checked only.
The shim reads the start of piolib's private RP1 handle to find its descriptor
(pio/wlsync_pio.c, struct wlsync_rp1_handle_prefix). On a commit where that handle has
moved, every level read fails with -9 and the box records split with no W.
Nothing was built. To build at the checked commit:
    git -C <utils checkout> checkout $checked     (clone without --depth 1)
To build against $at anyway, knowing the handle is unverified:
    WLSYNC_ALLOW_UNCHECKED_PIOLIB=1 $0 $piolib ${out}
MSG
            exit 1
        fi ;;
esac

# LIBRARY_BUILD=1 is what piolib's own CMakeLists.txt defines for its library build.
cc -O2 -fPIC -shared -Wall -Wextra -DLIBRARY_BUILD=1 \
    -I "$piolib/include" \
    -o "$out/libwlsync_pio.so" \
    "$here/wlsync_pio.c" \
    "$piolib/piolib.c" "$piolib/library_piochips.c" "$piolib/pio_rp1.c"

echo "built $out/libwlsync_pio.so against piolib at $(echo "$at" | cut -c1-7)"
