#!/usr/bin/env python3
"""
Recompute and write the Steam Deck controller app's boot checksum.

  fix_checksum.py APP.bin -o OUT.bin

Run this after mute_jingle.py or replace_jingle.py (or any other change to APP.bin) and before
flash_app.py -- the bootloader refuses to start an app whose checksum doesn't match. See
docs/haptic-engine.md#boot-check-and-app-checksum-h.

APP.bin: 229376 B = flash 0x8000..0x3FFFF. The checksum is CRC-32 (poly 0x04C11DB7, reflected), but
with init and final XOR both 0 -- NOT the common CRC-32 variant, which uses 0xFFFFFFFF for both. See
the README's Findings and docs/haptic-engine.md for why this matters.

Requires: nothing beyond the standard library.
"""
import argparse
import struct

import firmware_common as fw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="app.bin (229376 B); checksum may or may not already be valid")
    ap.add_argument("-o", "--out", required=True, help="output app.bin with a valid checksum")
    args = ap.parse_args()

    img = fw.load_app(args.input)

    old_calc, old_stored = fw.check_crc(img)
    print("before: stored 0x%08X, computed 0x%08X (%s)"
          % (old_stored, old_calc, "already valid" if old_calc == old_stored else "will be corrected"))

    struct.pack_into("<I", img, fw.CRC_ADDR, old_calc)

    new_calc, new_stored = fw.check_crc(img)
    assert new_calc == new_stored  # we just wrote it -- this should never fail

    fw.save_app(args.out, img)
    print("wrote %s: checksum 0x%08X (build %s)" % (args.out, new_stored, fw.build_stamp(img)))


if __name__ == "__main__":
    main()
