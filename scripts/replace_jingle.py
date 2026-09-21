#!/usr/bin/env python3
"""
Replace the Steam Deck controller's startup jingle with a different one, built from a list of notes.

  replace_jingle.py APP.bin --notes "C5:280,C5:280,G5:280" -o OUT.bin
  replace_jingle.py APP.bin --example twinkle -o OUT.bin

APP.bin: a STOCK (unmodified) app image, 229376 B = flash 0x8000..0x3FFFF.

--notes is a comma-separated list of NAME:duration_ms, where NAME is a note name (C5, F#4, Bb3, ...) or
a plain number of Hz. Notes play back-to-back with --gap-ms of silence between them. Frequency must be
1..2000 Hz (the engine's assumed Nyquist limit, see docs/haptic-engine.md) and duration 1..16000 ms.

--example twinkle plays the worked example from docs/jingle.md: the opening 7 notes of "Twinkle, Twinkle,
Little Star" (a 1761 French folk melody, public domain), about 2.2 seconds.

What this does:
  1. Locates the script-1 (jingle) pointer by finding the stock jingle's signature -- see
     docs/jingle.md and docs/haptic-engine.md#script-format-h-structure-m-unit.
  2. Writes one small routine per note into free flash, each calling tone() with that note's
     frequency/duration/gain -- the same mechanism the stock notes use.
  3. Writes a new script table (one entry per note, plus an end marker) into free flash.
  4. Redirects the script-1 pointer to the new table.

This does NOT fix the checksum and does NOT flash anything. Run fix_checksum.py on the output next,
then flash_app.py -- see docs/jingle.md.

Requires: capstone (pip install capstone)
"""
import argparse
import struct
import sys

import firmware_common as fw

# "Twinkle, Twinkle, Little Star" opening phrase: C5 C5 G5 G5 A5 A5 G5, 280 ms/note -- docs/jingle.md
TWINKLE = [(523, 280), (523, 280), (784, 280), (784, 280), (880, 280), (880, 280), (784, 280)]
EXAMPLES = {"twinkle": TWINKLE}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="stock app.bin (229376 B)")
    ap.add_argument("--notes", help='e.g. "C5:280,C5:280,G5:280" -- note names or Hz, each with a duration in ms')
    ap.add_argument("--example", choices=sorted(EXAMPLES), help="use a built-in note list instead of --notes")
    ap.add_argument("--gap-ms", type=int, default=40, help="silence between notes (default 40; the stock jingle uses 1)")
    ap.add_argument("--atten-db", type=int, default=None,
                     help="per-note attenuation in dB, 0..127 (default: same as the stock jingle's notes, usually 3). "
                          "This can only reduce volume, not boost it -- see note_callback() in firmware_common.py.")
    ap.add_argument("-o", "--out", required=True, help="output app.bin (checksum not yet valid)")
    args = ap.parse_args()

    if bool(args.notes) == bool(args.example):
        sys.exit("give exactly one of --notes or --example")
    notes = EXAMPLES[args.example] if args.example else fw.parse_notes(args.notes)

    for freq, dur in notes:
        if not (1 <= freq <= 2000):
            sys.exit("note frequency %d Hz is outside 1..2000 Hz" % freq)
        if not (1 <= dur <= 16000):
            sys.exit("note duration %d ms is outside 1..16000 ms" % dur)
    if args.atten_db is not None and not (0 <= args.atten_db <= 127):
        sys.exit("--atten-db must be 0..127")

    img = fw.load_app(args.input)

    calc, stored = fw.check_crc(img)
    if calc != stored:
        sys.exit("input's checksum doesn't already match -- refusing to patch a non-stock or already-broken image")

    try:
        loc = fw.locate(img)
    except fw.LocateError as e:
        sys.exit("could not find the stock jingle: %s" % e)

    atten = loc["atten"] if args.atten_db is None else args.atten_db
    free_bytes = loc["free_end"] - loc["free_start"]
    print("stock jingle found: pointer @0x%X -> table 0x%X | tone() @0x%X | free flash 0x%X-0x%X (%d B) | build %s"
          % (loc["ptr_lit"], loc["table"], loc["tone_fn"], loc["free_start"], loc["free_end"], free_bytes, fw.build_stamp(img)))

    p = loc["free_start"]
    if not all(b == 0xFF for b in img[p:loc["free_end"]]):
        sys.exit("free flash is not fully erased (0xFF) -- refusing")

    fn_ptrs = []
    for freq, dur in notes:
        code = fw.note_callback(p, freq, dur, atten, loc["tone_fn"])
        if p + len(code) > loc["free_end"]:
            sys.exit("does not fit in free flash (%d B free)" % free_bytes)
        img[p:p + len(code)] = code
        fn_ptrs.append(p | 1)  # Thumb function pointer: bit 0 set
        p = (p + len(code) + 3) & ~3  # 4-byte align the next routine

    table_addr = p
    for i, fn_ptr in enumerate(fn_ptrs):
        delay = 0 if i == 0 else notes[i - 1][1] + args.gap_ms
        struct.pack_into("<II", img, p, delay, fn_ptr)
        p += 8
    end_delay = notes[-1][1] + args.gap_ms
    if p + 8 > loc["free_end"]:
        sys.exit("does not fit in free flash (%d B free)" % free_bytes)
    struct.pack_into("<II", img, p, end_delay, 0)  # end marker
    p += 8

    struct.pack_into("<I", img, loc["ptr_lit"], table_addr)  # redirect the script-1 pointer

    fw.save_app(args.out, img)
    total_ms = sum(dur for _, dur in notes) + args.gap_ms * len(notes)
    print("wrote %d notes into 0x%X-0x%X (table @0x%X), ~%.1f s total, atten %d dB"
          % (len(notes), loc["free_start"], p, table_addr, total_ms / 1000, atten))
    print("pointer @0x%X now points to 0x%X" % (loc["ptr_lit"], table_addr))
    print("checksum not yet valid -- next: python3 fix_checksum.py %s -o %s" % (args.out, args.out))


if __name__ == "__main__":
    main()
