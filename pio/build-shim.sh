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
