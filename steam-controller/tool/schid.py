"""schid - cross-platform (Windows / Linux) access to the Steam Controller (2026).

Controller (running firmware): USB HID feature reports on the Valve vendor collection,
framing [0x01][opcode][len][body...] in 64 bytes; the reply has the same shape.
  Linux: /dev/hidrawN ioctls (no extra module). Windows / fallback: the `hidapi` module
  (pip install hidapi), vendor collection = usage page 0xFF00.
Bootloader (USB serial 28DE:1005, COMx or /dev/ttyACMx): frames 0xAD ... 0xAE with escape
  0xAC 00/01/02, payload = u16 message id + body, reply starts with 0x00 = ACK (pyserial).
"""
import glob, os, struct, sys, time

VID = 0x28DE
APP_PIDS = (0x1302,)            # controller over USB cable
BL_PID = 0x1005                 # controller bootloader
RPT = 64

class DeviceError(Exception):
    pass

class NoPermission(DeviceError):
    pass

class MissingModule(DeviceError):
    pass

class NotFound(DeviceError):
    pass

# ------------------------------------------------------------------ running firmware
class Controller:
    def __init__(self, path=None):
        self.kind = None
        if sys.platform.startswith('linux'):
            p = path or self._find_hidraw()
            if p:
                try:
                    self.fd = os.open(p, os.O_RDWR)
                except PermissionError:
                    raise NoPermission(f'no permission to open {p}')
                self.kind, self.path = 'hidraw', p
                return
            raise NotFound('no Steam Controller found on USB')
        self._open_hidapi(path)

    @staticmethod
    def _find_hidraw():
        for d in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
            try:
                ue = open(d + '/device/uevent').read().upper()
            except OSError:
                continue
            if any(f'HID_ID=0003:000028DE:0000{p:04X}' in ue for p in APP_PIDS):
                return '/dev/' + os.path.basename(d)
        return None

    def _open_hidapi(self, path):
        try:
            import hid
            hid.device
        except (ImportError, AttributeError):
            raise MissingModule('hidapi')
        cand = path
        if cand is None:
            for pid in APP_PIDS:
                for e in hid.enumerate(VID, pid):
                    if int(e.get('usage_page') or 0) >= 0xFF00:
                        cand = e['path']; break
                if cand:
                    break
        if cand is None:
            raise NotFound('no Steam Controller found on USB')
        self.h = hid.device()
        try:
            self.h.open_path(cand if isinstance(cand, bytes) else cand.encode())
        except (OSError, IOError) as e:
            raise NoPermission(f'cannot open the controller ({e}); it may be in use by another program')
        self.kind, self.path = 'hidapi', cand

    def xfer(self, op, body=b'', reply=True):
        body = bytes(body)
        r = bytearray(RPT)
        r[0], r[1], r[2] = 1, op, len(body)
        r[3:3 + len(body)] = body
        if self.kind == 'hidraw':
            import fcntl
            ioc = lambda nr: (3 << 30) | (RPT << 16) | (ord('H') << 8) | nr
            fcntl.ioctl(self.fd, ioc(6), bytes(r))
            if not reply:
                return None
            b = bytearray(RPT); b[0] = 1
            fcntl.ioctl(self.fd, ioc(7), b)
        else:
            if self.h.send_feature_report(bytes(r)) < 0:
                raise DeviceError(f'feature 0x{op:02x} rejected')
            if not reply:
                return None
            b = bytes(self.h.get_feature_report(1, RPT))
        if len(b) < 3 or b[1] != op or 3 + b[2] > len(b):
            raise DeviceError(f'bad reply to 0x{op:02x}')
        return bytes(b[3:3 + b[2]])

    def attributes(self):
        p = self.xfer(0x83)
        return {p[i]: struct.unpack_from('<I', p, i + 1)[0] for i in range(0, len(p) - len(p) % 5, 5)}

    def string(self, attr):
        r = self.xfer(0xAE, bytes([1, attr]))
        return r[1:].split(b'\x00')[0].decode(errors='replace') if r else ''

    def read_setting(self, key):
        """stock READ_SETTING (0xED): value bytes, or None if the key is not stored"""
        r = self.xfer(0xED, key.encode() + b'\x00')
        return r if r else None

    def reboot_to_bootloader(self):
        """0x90 REBOOT_TO_ISP: sets a flag in RAM and resets; writes no flash"""
        try:
            self.xfer(0x90, b'', reply=False)
        except Exception:
            pass                                   # USB drops during the reset

    def close(self):
        try:
            os.close(self.fd) if self.kind == 'hidraw' else self.h.close()
        except Exception:
            pass

def controller_present():
    try:
        c = Controller(); c.close(); return True
    except Exception:
        return False

# ------------------------------------------------------------------ bootloader
MSG_INFO, MSG_RESET = 0x1233, 0x1237
SOF, EOF, ESC = 0xAD, 0xAE, 0xAC

def find_bootloader_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        raise MissingModule('pyserial')
    for p in list_ports.comports():
        if p.vid == VID and p.pid == BL_PID:
            return p.device
    return None

def wait_for(fn, seconds):
    t = time.time() + seconds
    while time.time() < t:
        r = fn()
        if r:
            return r
        time.sleep(0.5)
    return None

class Bootloader:
    def __init__(self, port):
        import serial
        try:
            self.s = serial.Serial(port, timeout=5)
        except serial.SerialException as e:
            if 'ermission' in str(e) or 'denied' in str(e).lower():
                raise NoPermission(f'no permission to open {port}')
            raise DeviceError(f'cannot open {port}: {e}')
        self.port = port

    def cmd(self, msg, body=b''):
        pl = struct.pack('<H', msg) + body
        tx = bytearray([SOF])
        for x in pl:
            tx += bytes([ESC, {ESC: 0, SOF: 1, EOF: 2}[x]]) if x in (ESC, SOF, EOF) else bytes([x])
        tx.append(EOF)
        self.s.reset_input_buffer()
        self.s.write(bytes(tx))
        for _ in range(16):                        # skip stray / empty frames
            data = self._frame()
            if data and data[0] == 0:
                return data[1:]
        raise DeviceError(f'bootloader gave no ACK for 0x{msg:04X}')

    def _frame(self):
        raw, inside = bytearray(), False
        while True:
            c = self.s.read(1)
            if not c:
                raise DeviceError('bootloader timeout')
            c = c[0]
            if c == SOF:
                raw, inside = bytearray(), True
            elif inside and c == EOF:
                out, i = bytearray(), 0
                while i < len(raw):
                    if raw[i] == ESC and i + 1 < len(raw):
                        out.append({0: ESC, 1: SOF, 2: EOF}.get(raw[i + 1], raw[i + 1])); i += 2
                    else:
                        out.append(raw[i]); i += 1
                return bytes(out)
            elif inside:
                raw.append(c)

    def info(self):
        """164 B: u32 bootloader build, 32 B app header (magic, len, crc32, 0...), 128 B UICR customer area"""
        r = self.cmd(MSG_INFO)
        if len(r) != 164:
            raise DeviceError(f'unexpected INFO reply ({len(r)} bytes)')
        return r

    def leave(self):
        """MESSAGE_RESET: the bootloader checks the app and starts it"""
        try:
            self.cmd(MSG_RESET)
        except Exception:
            pass                                   # resets immediately, the ACK may not arrive
        try:
            self.s.close()
        except Exception:
            pass
