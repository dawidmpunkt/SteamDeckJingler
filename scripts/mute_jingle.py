#!/usr/bin/env python3
"""
Mute the Steam Deck controller's startup jingle.

  mute_jingle.py APP.bin -o OUT.bin

APP.bin: a STOCK (unmodified) app image, 229376 B = flash 0x8000..0x3FFFF -- e.g. read back from the
board, or one of the *-app.bin files a firmware dump produces.

What this does:
  1. Locates the script-1 (jingle) pointer by finding the stock jingle's signature -- see
     docs/jingle.md and docs/haptic-engine.md#script-format-h-structure-m-unit.
  2. Writes an 8-byte "already ended" script entry ({delay: 0, fn: 0}) into free flash.
  3. Redirects the script-1 pointer to that entry.

This does NOT fix the checksum and does NOT flash anything. Run fix_checksum.py on the output next,
then flash_app.py -- see docs/jingle.md.

Requires: capstone (pip install capstone)
"""
import argparse
import struct
import sys

import firmware_common as fw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="stock app.bin (229376 B)")
    ap.add_argument("-o", "--out", required=True, help="output app.bin (checksum not yet valid)")
    args = ap.parse_args()

    img = fw.load_app(args.input)

    calc, stored = fw.check_crc(img)
    if calc != stored:
        sys.exit("input's checksum doesn't already match -- refusing to patch a non-stock or already-broken image")

    try:
        loc = fw.locate(img)
    except fw.LocateError as e:
        sys.exit("could not find the stock jingle: %s" % e)

    print("stock jingle found: pointer @0x%X -> table 0x%X (build %s)" % (loc["ptr_lit"], loc["table"], fw.build_stamp(img)))

    end_marker_addr = loc["free_start"]
    if not all(b == 0xFF for b in img[end_marker_addr:end_marker_addr + 8]):
        sys.exit("free flash at 0x%X is not erased (0xFF) -- refusing" % end_marker_addr)

    struct.pack_into("<II", img, end_marker_addr, 0, 0)          # {delay: 0, fn: 0} -- ends immediately
    struct.pack_into("<I", img, loc["ptr_lit"], end_marker_addr)  # redirect the script-1 pointer

    fw.save_app(args.out, img)
    print("muted: pointer @0x%X now points to 0x%X (an end marker, 8 bytes)" % (loc["ptr_lit"], end_marker_addr))
    print("checksum not yet valid -- next: python3 fix_checksum.py %s -o %s" % (args.out, args.out))


if __name__ == "__main__":
    main()
