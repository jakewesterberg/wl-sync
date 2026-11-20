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

/* The level, or -EIO for an answer that cannot be trusted: one that left a field holding
 * its seed, so the kernel did not answer it, or whose empty flag contradicts its level.
 * ANY OTHER VALUE IS AN ANSWER (Task 1.20 fix round 2). The flags are read as piolib reads
 * them, as truth values (pio_rp1.c:573-580 at utils b57c48e), so a firmware that answers
 * `empty` as a bit mask, 1 << sm, is read as it means; until fix round 2 an `empty` other
 * than 0 or 1 was refused, which would have failed every read of every machine but SM 0. */
static inline int wlsync_fifo_state_level(unsigned level, unsigned empty, unsigned full)
{
    if (level == WLSYNC_LEVEL_SEED || empty == WLSYNC_FLAG_SEED || full == WLSYNC_FLAG_SEED)
        return -EIO;
    if ((empty != 0) != (level == 0))
        return -EIO;
    return (int)level;
}

#endif
