/*
 * wlsync_fifo_state.h -- what the shim makes of the kernel's answer to
 * PIO_IOC_SM_FIFO_STATE (pio/wlsync_pio.c, wlsync_sm_rx_level).
 *
 * APART FROM THE SHIM, so that it can be compiled and run on its own: it needs nothing of
 * piolib, and tests/test_rp1_backend.py builds it with the host's C compiler and runs every
 * case against it. The shim cannot be run off the box.
 */
#ifndef WLSYNC_FIFO_STATE_H
#define WLSYNC_FIFO_STATE_H

#include <errno.h>

/* The values the shim seeds the message's three answer fields with: none of them is an
 * answer a FIFO can give, so a field still holding one was not written. */
#define WLSYNC_LEVEL_SEED 0xFFFFu
#define WLSYNC_FLAG_SEED 0xFFu

/* The widest level the kernel's answer has given: RP1's joined RX FIFO holds 16 words,
 * and its level field reads 0-14 exactly, then 15 for both 15 and 16 words, with `full`
 * set only at 16 (bench probe, wl-rig-sync0, 2026-11-20). */
#define WLSYNC_LEVEL_FIELD_MAX 15u

/* What the shim makes of the kernel's answer to PIO_IOC_SM_FIFO_STATE: the level, exactly,
 * or a negative errno. `result` and `error` are the ioctl's return and errno, `level`,
 * `empty` and `full` the answer's fields, and `depth` the FIFO's joined depth
 * (wlsync_rx_fifo_depth).
 *
 * SUCCESS IS ANY RESULT >= 0: the box's ioctl returns 8 (bench probe, wl-rig-sync0,
 * 2026-11-20).
 *
 * EXACT, OR REFUSED. The word pump's proof needs each level exact (wl_sync/rp1/
 * pio_capture.py, CodeWordPairer), so a level is returned only where it equals the FIFO's
 * count at some instant of the call. The status flags and the level may be read from the
 * hardware at instants of their own, but only the drain pops, and not during the call, so
 * the count only rises through it, a word at a time: a value between the count at either
 * read is the count at some instant between them. So:
 *   * `full` set: the FIFO held its depth when the flag was read. The level must read the
 *     depth, or WLSYNC_LEVEL_FIELD_MAX where the depth is above it, the field saturated;
 *     either way the answer is the depth (fix round 3). Until fix round 3 the shim
 *     returned the saturated 15 for a full FIFO, and the box's depth check failed.
 *   * `full` clear: the level must be below the depth, and agree with `empty`. A level of
 *     WLSYNC_LEVEL_FIELD_MAX is exact only where the depth is one more than it, as on RP1:
 *     not full, the FIFO held at most depth - 1 = 15 when the flag was read, and the
 *     field says at least 15. Below a deeper FIFO it is only "15 or more", and refused.
 *   * a field still holding its seed was not answered, and is refused. ANY OTHER VALUE IS
 *     AN ANSWER: the flags are truth values, as piolib reads them (pio_rp1.c:573-580 at utils
 *     b57c48e), so `empty` as a bit mask, 1 << sm, is read as it means (fix round 2). */
static inline int wlsync_fifo_state_answer(int result, int error, unsigned level,
                                           unsigned empty, unsigned full, unsigned depth)
{
    if (result < 0)
        return error > 0 ? -error : -EIO;
    if (level == WLSYNC_LEVEL_SEED || empty == WLSYNC_FLAG_SEED || full == WLSYNC_FLAG_SEED)
        return -EIO;
    if (depth == 0)
        return -EIO;
    if (full != 0) {
        if (empty != 0)
            return -EIO;
        if (level == depth)
            return (int)depth;
        if (level == WLSYNC_LEVEL_FIELD_MAX && depth > WLSYNC_LEVEL_FIELD_MAX)
            return (int)depth;
        return -EIO;
    }
    if (level >= depth)
        return -EIO;
    if (level == WLSYNC_LEVEL_FIELD_MAX && depth - 1 > WLSYNC_LEVEL_FIELD_MAX)
        return -EIO;
    if ((empty != 0) != (level == 0))
        return -EIO;
    return (int)level;
}

#endif
