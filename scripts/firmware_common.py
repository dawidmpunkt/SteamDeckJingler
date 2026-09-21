"""
Shared logic for the SteamDeckJingler firmware-patching scripts (mute_jingle.py, replace_jingle.py,
fix_checksum.py). Not meant to be run directly.

The signature-based pointer locator, the CRC-32 implementation, and the Thumb-2 note-callback encoder
are ports of the analysis tools that found this format (fwtool.py / wavepatch.py in CB-Sing/analysis),
which were exercised against real firmware in an emulator. This split into separate single-purpose
scripts has not been independently re-run against a stock (unmodified) firmware image -- none was
available when these were written. See docs/haptic-engine.md and docs/jingle.md for what the addresses
and structures below mean.
"""
import re
import struct

APP_BASE = 0x8000              # start of the app region in flash
CRC_ADDR = 0x3FFFC             # boot CRC is stored here (LE); the checked range is [APP_BASE, CRC_ADDR)
IMG_END = 0x40000              # end of the app region (0x3FFFF inclusive)
APP_SIZE = IMG_END - APP_BASE  # 229376 B -- the size flash_app.py (and the real board) expect

STOCK_NOTES = [(588, 80), (699, 80), (785, 80)]  # the stock jingle's signature: 3 notes at these Hz/ms

u32 = lambda b, a: struct.unpack_from("<I", b, a)[0]


class LocateError(Exception):
    """Raised when the stock jingle's signature isn't found in the image exactly once."""


# ---- image I/O -----------------------------------------------------------------------------------

def load_app(path):
    """Load a 229376-byte app-only image (flash 0x8000..0x3FFFF) into a 0x40000-byte buffer, 0xFF-padded
    below 0x8000, so every address used elsewhere in this file can be used directly as an index."""
    data = open(path, "rb").read()
    if len(data) != APP_SIZE:
        raise SystemExit("expected a %d-byte app-only image (flash 0x8000..0x3FFFF), got %d bytes -- "
                          "these scripts don't read SREC or full code-flash dumps" % (APP_SIZE, len(data)))
    return bytearray(b"\xff" * APP_BASE) + bytearray(data)


def save_app(path, img):
    """Write the app region (flash 0x8000..0x3FFFF) of an in-memory image out as a 229376-byte file."""
    open(path, "wb").write(bytes(img[APP_BASE:IMG_END]))


def build_stamp(img):
    m = re.search(rb"BUILD_TIME_([0-9A-F]{8})", bytes(img[APP_BASE:CRC_ADDR]))
    return m.group(1).decode() if m else "?"


# ---- CRC-32 (poly 0x04C11DB7, init 0, reflected in/out, xorout 0) ---------------------------------
# NOT the common CRC-32 variant (which uses 0xFFFFFFFF for init and xorout) -- see
# docs/haptic-engine.md#boot-check-and-app-checksum-h.

_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


def crc32_ra(data):
    c = 0
    for b in data:
        c = _CRC_TABLE[(c ^ b) & 0xFF] ^ (c >> 8)
    return c


def check_crc(img):
    """Returns (computed, stored). Equal means the app passes the bootloader's check."""
    return crc32_ra(img[APP_BASE:CRC_ADDR]), u32(img, CRC_ADDR)


# ---- note name parsing (e.g. "C5", "F#4", or a plain number of Hz) --------------------------------

_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_hz(name):
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", name)
    if not m:
        return int(name)  # not a note name -- treat it as a frequency in Hz
    n = (int(m.group(3)) + 1) * 12 + _SEMITONE[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    return round(440 * 2 ** ((n - 69) / 12))  # 12-tone equal temperament, A4 = 440 Hz


def parse_notes(spec):
    """'C5:280,C5:280,G5:280' -> [(523, 280), (523, 280), (784, 280)]"""
    out = []
    for tok in spec.split(","):
        name, dur = tok.strip().split(":")
        out.append((note_to_hz(name), int(dur)))
    return out


# ---- locating the stock jingle by its signature ----------------------------------------------------
# This doesn't assume a fixed address: it scans the app for a script table whose notes match the stock
# jingle (588/699/785 Hz, 80 ms), so it works on any app build with that stock jingle, not only the two
# builds this project has looked at. See docs/haptic-engine.md#script-format-h-structure-m-unit.

try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    _MD = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
except ImportError:
    _MD = None


def _decode_note_fn(img, fn):
    if _MD is None:
        raise RuntimeError("the 'capstone' package is required for this (pip install capstone)")
    a = fn & ~1  # Thumb function pointers have bit 0 set; the real address doesn't
    regs, sub3, bl = {}, None, None
    for insn in _MD.disasm(bytes(img[a:a + 0x30]), a):
        m = re.match(r"(r[12]), #(0x[0-9a-f]+|\d+)$", insn.op_str)
        if insn.mnemonic in ("movw", "mov.w", "movs") and m:
            regs[m.group(1)] = (insn.address, insn.size, int(m.group(2), 0))
        elif insn.mnemonic == "subs" and insn.op_str.startswith("r3, #"):
            sub3 = int(insn.op_str.split("#")[1], 0)
        elif insn.mnemonic == "bl":
            bl = int(insn.op_str.lstrip("#"), 0)
        elif insn.mnemonic == "ldr" and insn.op_str.startswith("pc"):
            break
    g = lambda k, j: regs[k][j] if k in regs else None
    return dict(freq=g("r1", 2), dur=g("r2", 2), atten=sub3, bl=bl)


def _decode_script(img, table, max_entries=16):
    out, a = [], table
    for _ in range(max_entries):
        delay, fn = struct.unpack_from("<II", img, a)
        if fn == 0:
            out.append(dict(delay=delay, fn=0, at=a))
            return out
        if not (fn & 1) or not APP_BASE <= fn < CRC_ADDR:
            raise ValueError("not a script table")
        entry = _decode_note_fn(img, fn)
        entry.update(delay=delay, fn=fn, at=a)
        out.append(entry)
        a += 8
    raise ValueError("unterminated script (no end marker within %d entries)" % max_entries)


def locate(img):
    """Scan the app for the stock jingle. Returns the pointer literal's address, the table's address,
    its decoded entries, the tone() function it calls, its gain, and where the free flash after it
    starts/ends. Raises LocateError if the stock signature isn't found exactly once (e.g. because the
    image has already been patched, or isn't this app at all)."""
    hits = []
    for a in range(APP_BASE, CRC_ADDR - 3, 4):
        v = u32(img, a)
        if v % 4 or not 0x20000 <= v < CRC_ADDR - 0x40:
            continue
        try:
            entries = _decode_script(img, v)
        except (ValueError, struct.error):
            continue
        notes = [(e["freq"], e["dur"]) for e in entries if e["fn"]]
        if notes == STOCK_NOTES:
            hits.append((a, v, entries))
    if len(hits) != 1:
        raise LocateError(
            "found %d matches for the stock jingle signature (need exactly 1) -- this must be a stock, "
            "unmodified app image (a previously patched or muted image will not match)" % len(hits))
    ptr_lit, table, entries = hits[0]
    end = CRC_ADDR
    while end > APP_BASE and img[end - 1] == 0xFF:
        end -= 1
    return dict(ptr_lit=ptr_lit, table=table, entries=entries, tone_fn=entries[0]["bl"],
                atten=entries[0]["atten"], free_start=(end + 3) & ~3, free_end=CRC_ADDR)


# ---- Thumb-2 code generation for a note callback ---------------------------------------------------
# Each note callback is a tiny routine with the same shape as the stock ones: load duration, attenuation
# and frequency, then call tone(). See docs/haptic-engine.md#startup-jingle and #script-format.

def _enc_movw(rd, imm):
    assert 0 <= imm <= 0xFFFF
    return struct.pack("<HH",
                        0xF240 | (((imm >> 11) & 1) << 10) | ((imm >> 12) & 0xF),
                        (((imm >> 8) & 7) << 12) | (rd << 8) | (imm & 0xFF))


def _enc_bl(addr, target):
    off = target - (addr + 4)
    s = 1 if off < 0 else 0
    off &= (1 << 25) - 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = ((~i1) & 1) ^ s, ((~i2) & 1) ^ s
    return struct.pack("<HH",
                        0xF000 | (s << 10) | ((off >> 12) & 0x3FF),
                        0xD000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF))


# Preamble/mid/postamble shared with every stock note callback (identical machine code, just different
# immediates in between): set up a small stack frame, load the side's gain byte, call tone(), clean up.
_PRE = bytes.fromhex("0023" "00b5" "83b0" "cde90033" "90f83430")
_MID = bytes.fromhex("c06a" "5bb2")
_POST = bytes.fromhex("03b0" "5df804fb")


def note_callback(addr, freq, dur, atten, tone_fn):
    """Machine code for one note: loads (dur, atten, freq), then branches to
    tone_fn(ctx, freq, dur, gain, 0, 0). `addr` is where this code will live, needed to compute the
    branch offset to tone_fn."""
    assert 0 <= atten <= 127, "atten must be 0..127 (it's sign-extended after a subtract; >127 would flip to a boost)"
    code = _PRE + _enc_movw(2, dur) + struct.pack("<H", 0x3B00 | atten) + _enc_movw(1, freq) + _MID
    return code + _enc_bl(addr + len(code), tone_fn) + _POST
