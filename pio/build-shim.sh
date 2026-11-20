#!/bin/sh
# Build libwlsync_pio.so: pio/wlsync_pio.c together with piolib's own sources, as ONE
# shared library, so the box needs no separately installed libpio. See wlsync_pio.c
# for why the shim exists at all.
#
#   pio/build-shim.sh <raspberrypi/utils checkout>/piolib [output directory]
#
# Then install it where ctypes.util.find_library("wlsync_pio") looks:
#
#   sudo install -m 0644 <output>/libwlsync_pio.so /usr/local/lib/ && sudo ldconfig
set -eu

piolib=${1:?usage: build-shim.sh <path to raspberrypi/utils/piolib> [output dir]}
out=${2:-.}
here=$(cd "$(dirname "$0")" && pwd)

# LIBRARY_BUILD=1 is what piolib's own CMakeLists.txt defines for its library build.
cc -O2 -fPIC -shared -Wall -Wextra -DLIBRARY_BUILD=1 \
    -I "$piolib/include" \
    -o "$out/libwlsync_pio.so" \
    "$here/wlsync_pio.c" \
    "$piolib/piolib.c" "$piolib/library_piochips.c" "$piolib/pio_rp1.c"

echo "built $out/libwlsync_pio.so against piolib at $(git -C "$piolib" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# The shim reads the start of piolib's private RP1 handle to find its descriptor, checked
# against utils b57c48e only (wlsync_pio.c, struct wlsync_rp1_handle_prefix). A commit
# whose handle has moved is caught at start, not misread: every level read is -9, and the
# process records split with no W (deploy/README.md:616-621). Say so here, at build time.
at=$(git -C "$piolib" rev-parse HEAD 2>/dev/null || echo unknown)
case $at in
    b57c48e*) ;;
    *) echo "warning: piolib is at $at, not utils b57c48e, the commit the shim's read of" \
            "its private handle was checked against (deploy/README.md)" >&2 ;;
esac
