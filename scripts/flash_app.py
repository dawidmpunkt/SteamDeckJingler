#!/usr/bin/env python3
"""
  sudo python3 flash_app.py APP.bin      (APP.bin = 229376 B = flash 0x8000..0x3FFFF, e.g.
                                           fix_checksum.py's output)

Writes the app image to the controller board over the RA4E1 boot ROM. Saves the board's *current* app
first as app_backup_<hash>.bin, erases and writes only 0x8000..0x3FFFF (the bootloader, 0x0..0x7FFF, is
never touched), and reads it back to confirm. To go back to the old app: run this script again with the
backup file.

This must run ON THE STEAM DECK ITSELF (it shells out to the BatCtrl helper from Valve's own controller
firmware updater, and needs root). It is not something you run from a regular PC.

This is a close port of a script that has been run successfully on real hardware (see
docs/jingle.md and the project notes) -- the boot-ROM protocol and safety checks below are unchanged
from that; only comments and prompts have been translated/renamed for this repo. This particular file
has not been independently re-run here.

Modifying firmware and its checksum can render the controller board unusable if interrupted. Keep the
backup this script writes. Use at your own risk. This project is not affiliated with Valve.
"""
import glob
import hashlib
import os
import re
import select
import struct
import subprocess
import sys
import time
import tty
import zlib

BAT = "/usr/share/jupiter_controller_fw_updater/RA_bootloader_updater/linux_host_tools/BatCtrl"
LO, HI = 0x8000, 0x3FFFF
rng = lambda a, b: struct.pack(">II", a, b)


def pkt(cmd, data=b"", sod=1):
    b = struct.pack(">HB", len(data) + 1, cmd) + data
    return bytes([sod]) + b + bytes([-sum(b) & 0xFF, 3])


def rd(fd, n, t=10):
    buf, end = b"", time.time() + t
    while len(buf) < n and time.time() < end:
        if select.select([fd], [], [], 0.05)[0]:
            buf += os.read(fd, n - len(buf))
    return buf


def reply(fd, t=30):
    h = rd(fd, 4, t)
    if len(h) < 4:
        sys.exit("no reply from the chip")
    n = (h[1] << 8 | h[2]) - 1
    body = rd(fd, n + 2, t)
    if h[3] & 0x80:
        sys.exit("chip reports error 0x%02X" % body[0])
    return body[:n]


def talk(fd, cmd, data=b"", t=30):
    os.write(fd, pkt(cmd, data))
    return reply(fd, t)


def handshake(fd):
    end = time.time() + 4
    while time.time() < end:
        os.write(fd, b"\0\0\0")
        if rd(fd, 1, 0.1) == b"\0":
            os.write(fd, b"\x55")
            return rd(fd, 1, 1) == b"\xc6"
    return False


def find_tty():
    for v in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        d = os.path.dirname(v)
        try:
            if open(v).read().strip() == "045b" and open(d + "/idProduct").read().strip() == "0261":
                t = glob.glob(d + "/*/tty/ttyACM*")
                if t:
                    return "/dev/" + os.path.basename(t[0])
        except OSError:
            pass


def read(fd, a, b):
    os.write(fd, pkt(0x15, rng(a, b)))
    n, out = b - a + 1, b""
    while True:
        out += reply(fd)
        if len(out) >= n:
            return out[:n]
        os.write(fd, pkt(0x15, b"\0" + b"\xff" * 8, 0x81))  # ack -> next 1024-byte packet


def write(fd, a, data):
    talk(fd, 0x13, rng(a, a + len(data) - 1), 10)
    for o in range(0, len(data), 1024):
        os.write(fd, pkt(0x13, data[o:o + 1024], 0x81))
        reply(fd)
        if o % 0x8000 == 0:
            print("  0x%05X ..." % (a + o), flush=True)


# CRC-32 over 0x8000..0x3FFFB, stored at 0x3FFFC -- same algorithm as firmware_common.crc32_ra, computed
# via zlib's init/xorout=0xFFFFFFFF variant with both cancelled out (see docs/haptic-engine.md).
crc_ok = lambda d: zlib.crc32(d[:-4], 0xFFFFFFFF) ^ 0xFFFFFFFF == struct.unpack("<I", d[-4:])[0]


def stamp(d):
    m = re.search(rb"BUILD_TIME_([0-9A-F]{8})", d)
    return m.group(1).decode() if m else "?"


def check(d):
    if len(d) >= 0x40000:
        d = d[LO:0x40000]
    if len(d) != 0x38000:
        sys.exit("expected 229376 B (flash 0x8000..0x3FFFF)")
    if not crc_ok(d):
        sys.exit("boot checksum at 0x3FFFC doesn't match -- aborting (did you run fix_checksum.py?)")
    sp, rv = struct.unpack("<II", d[:8])
    if not (0x20000000 < sp <= 0x20020000 and LO < rv < 0x40000):
        sys.exit("vector table looks implausible -- aborting")
    return d


def run(fd, new):
    cur = read(fd, LO, HI)
    bak = "app_backup_%s.bin" % hashlib.sha256(cur).hexdigest()[:8]
    open(bak, "wb").write(cur)
    print("backup: %s (build %s, boot checksum %s) -> new: build %s"
          % (bak, stamp(cur), "ok" if crc_ok(cur) else "INVALID", stamp(new)))
    if cur == new:
        return print("app is already this image -- nothing to do")
    try:
        print("erasing ...")
        talk(fd, 0x12, rng(0x8000, 0xFFFF), 60)
        talk(fd, 0x12, rng(0x10000, HI), 60)  # one command per flash area (8 KiB / 32 KiB erase units)
        print("writing ...")
        write(fd, 0x8000, new[:0x8000])
        write(fd, 0x10000, new[0x8000:])
    except SystemExit as e:
        sys.exit("%s\nThe app region may now be incomplete (the bootloader won't start it like that). "
                  "Restore with: sudo python3 flash_app.py %s" % (e, bak))
    if read(fd, LO, HI) == new:
        print("OK, read-back matches")
    else:
        print("ERROR: read-back differs! Restore with: sudo python3 flash_app.py %s" % bak)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if os.geteuid():
        sys.exit("run with sudo")
    new = check(open(sys.argv[1], "rb").read())

    def cycle():
        subprocess.run([BAT, "SetCBPower", "0"], capture_output=True)
        time.sleep(1)
        subprocess.run([BAT, "SetCBPower", "1"], capture_output=True)

    input('Hold R1 + R4 + "..." and press Enter ')
    fd = None
    for _ in range(20):
        cycle()
        end = time.time() + 10
        while fd is None and time.time() < end:
            p = find_tty()
            try:
                f = os.open(p, os.O_RDWR | os.O_NOCTTY) if p else None
                if f is not None:
                    tty.setraw(f)
                    if handshake(f):
                        fd = f
                    else:
                        os.close(f)
            except OSError:
                pass
            time.sleep(0.02)
        if fd is not None:
            break
    if fd is None:
        sys.exit("could not reach the boot ROM (keep holding the buttons the whole time)")
    try:
        run(fd, new)
    finally:
        input("Release the buttons, then press Enter (the controller will restart) ")
        cycle()


if __name__ == "__main__":
    main()
