/*
 * wlsync_pio -- the slice of piolib that wl-sync needs, as plain exported functions.
 *
 * WHY THIS FILE EXISTS. piolib's API is a clone of the Pico SDK's, and nearly all of it
 * is `static inline` in piolib.h, dispatching through a per-chip table of function
 * pointers (`pio->chip->...`). Of the 28 functions wl_sync.rp1.backend once bound with
 * ctypes, libpio.so exports three, and ctypes can only call what a library exports. This
 * file compiles the inline calls into real symbols. piolib's own sources are compiled into
 * the same .so (pio/build-shim.sh), so the box needs no separate libpio.
 *
 * SCALARS ONLY ACROSS THE BOUNDARY. No struct crosses into Python: the state machine's
 * whole configuration happens here. Every instruction operand is named here rather than
 * passed in as a number, because piolib's `enum pio_src_dest` carries validity flags in
 * its high bits -- `pio_null` is not 3, and `pio_isr` is not 6.
 *
 * ERRORS ARE RETURNED, NEVER FATAL. piolib's default response to an error is pio_panic(),
 * which is exit(1): the whole recording process, gone. Fatal errors are switched off at
 * open, and every call here that can fail reports a negative errno instead.
 *
 * ONE PIO BLOCK PER PROCESS, held here, because wl-sync uses exactly one.
 */

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "piolib.h"

static PIO g_pio;

int wlsync_pio_open(unsigned index)
{
    if (pio_init() < 0)
        return -EIO;
    PIO pio = pio_open(index);
    if (pio == NULL)
        return -ENODEV;
    if (PIO_IS_ERR(pio))
        return PIO_ERR_VAL(pio);
    pio_enable_fatal_errors(pio, false);
    /* The encoders below ask piolib for the CURRENT block, so it must be selected
     * before any instruction is encoded. */
    pio_select(pio);
    g_pio = pio;
    return 0;
}

void wlsync_pio_close(void)
{
    if (g_pio != NULL) {
        pio_close(g_pio);
        g_pio = NULL;
    }
}

/* One encoder per instruction the strobe-capture program uses, matching
 * wl_sync/rp1/program.py name for name, so the backend can check its own encodings
 * against piolib's at startup. */
unsigned wlsync_encode_jmp(unsigned addr) { return pio_encode_jmp(addr); }
unsigned wlsync_encode_jmp_pin(unsigned addr) { return pio_encode_jmp_pin(addr); }
unsigned wlsync_encode_jmp_x_dec(unsigned addr) { return pio_encode_jmp_x_dec(addr); }
unsigned wlsync_encode_in_pins(unsigned count) { return pio_encode_in(pio_pins, count); }
unsigned wlsync_encode_push_noblock(void) { return pio_encode_push(false, false); }
unsigned wlsync_encode_mov_isr_x(void) { return pio_encode_mov(pio_isr, pio_x); }
unsigned wlsync_encode_mov_x_not_null(void) { return pio_encode_mov_not(pio_x, pio_null); }

/* Named, because RP1 answers only for clk_sys: any other index returns
 * PIO_ORIGIN_ANY, which as a uint32_t is 4294967295. */
uint32_t wlsync_clk_sys_hz(void) { return clock_get_hz(clk_sys); }

/*
 * Load the program and configure one state machine for strobed capture, left DISABLED:
 * the caller takes the timebase origin immediately before enabling it, because that
 * offset is spent straight out of the timing budget. Returns the state machine's
 * number, or a negative errno.
 */
int wlsync_start_capture(const uint16_t *instructions, unsigned length,
                         unsigned wrap_target, unsigned wrap,
                         unsigned data_base, unsigned data_count, unsigned strobe_pin,
                         float clkdiv, unsigned seed_instruction)
{
    if (g_pio == NULL)
        return -ENODEV;
    if (length == 0 || length > 32 || strobe_pin != data_base + data_count)
        return -EINVAL;

    pio_program_t program = {
        .instructions = instructions,
        .length = (uint8_t)length,
        .origin = -1,
        .pio_version = 0,
    };
    pio_clear_error(g_pio);
    if (!pio_can_add_program(g_pio, &program))
        return -ENOSPC;
    unsigned offset = pio_add_program(g_pio, &program);
    if (pio_get_error(g_pio))
        return -ENOSPC;

    int sm = pio_claim_unused_sm(g_pio, false);
    if (sm < 0)
        return -EBUSY;

    for (unsigned pin = data_base; pin <= strobe_pin; pin++)
        pio_gpio_init(g_pio, pin);
    pio_sm_set_consecutive_pindirs(g_pio, sm, data_base, data_count + 1, false);

    pio_sm_config config = pio_get_default_sm_config();
    sm_config_set_in_pins(&config, data_base);
    sm_config_set_jmp_pin(&config, strobe_pin);
    /* shift_right=false, so the lines land in ISR bits 0..n-1 and leave the upper half
     * clear. That clear upper half IS the pairing check in pio_capture.pair_fifo. */
    sm_config_set_in_shift(&config, false, false, 32);
    sm_config_set_fifo_join(&config, PIO_FIFO_JOIN_RX);
    sm_config_set_wrap(&config, offset + wrap_target, offset + wrap);
    sm_config_set_clkdiv(&config, clkdiv);
    pio_sm_init(g_pio, sm, offset, &config);

    /* Seed the counter before enabling: `set` carries a 5-bit immediate only. */
    pio_sm_exec(g_pio, sm, seed_instruction);
    if (pio_get_error(g_pio))
        return -EIO;
    return sm;
}

int wlsync_sm_set_enabled(unsigned sm, int enabled)
{
    if (g_pio == NULL)
        return -ENODEV;
    pio_clear_error(g_pio);
    pio_sm_set_enabled(g_pio, sm, enabled != 0);
    return pio_get_error(g_pio) ? -EIO : 0;
}

/* The three calls the word pump makes on every drain. None of them blocks: piolib
 * notes that a blocking call holds the whole RP1 firmware interface until it returns. */
unsigned wlsync_sm_rx_level(unsigned sm) { return pio_sm_get_rx_fifo_level(g_pio, sm); }
int wlsync_sm_rx_full(unsigned sm) { return pio_sm_is_rx_fifo_full(g_pio, sm) ? 1 : 0; }
uint32_t wlsync_sm_get(unsigned sm) { return pio_sm_get(g_pio, sm); }
