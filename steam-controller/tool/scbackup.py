#!/usr/bin/env python3
"""scbackup - read-only backup of the controller-specific data (Windows / Linux).

Writes nothing to the controller's flash. Refuses to continue unless:
  1. the controller's firmware version is in firmware-table.json (with a "backup" section),
  2. the matching Valve firmware file is in the archive folder (SHA-256 checked) and you
     confirm it is kept safe (it is the file you would restore the controller with),
  3. the app header read from the bootloader (length + CRC32 of the whole app) equals
     the table entry, i.e. the controller runs exactly that file.
Then it saves UICR (serials, hw id) and all settings listed for that version
(calibration, puck bond, user settings) into one file:
  guide/controller-backups/<serial>_<time>.scbackup.json      (private: contains keys)

usage: python guide/scbackup.py [--manual] [--archive DIR] [--table FILE]
  --manual   enter the bootloader with the button chord instead of the USB command
"""
import datetime, hashlib, json, os, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import schid   # noqa: E402

OUT = os.path.join(HERE, 'controller-backups')
WIN = sys.platform.startswith('win')
PY = 'py' if WIN else 'python'
UICR_MAGIC = 0xAC32A429

# ------------------------------------------------------------------ user interface
def say(*lines):
    for ln in lines:
        print(ln)

def todo(title, *lines):
    print(f'\n>> {title}')
    for ln in lines:
        print(f'   {ln}')

def stop(msg, *help_lines):
    todo('STOP: ' + msg, *help_lines)
    sys.exit(1)

def enter(q='Press Enter to continue (Ctrl+C to quit) '):
    try:
        return input(f'\n{q}').strip()
    except (EOFError, KeyboardInterrupt):
        print(); sys.exit(1)

def steam_folders():
    home = os.path.expanduser('~')
    if WIN:
        pf = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        return [os.path.join(pf, 'Steam', 'bin', 'hardwareupdater')]
    return [os.path.join(home, '.local/share/Steam/bin/hardwareupdater'),
            os.path.join(home, '.steam/steam/bin/hardwareupdater'),
            os.path.join(home, '.var/app/com.valvesoftware.Steam/.local/share/Steam/bin/hardwareupdater')]

def hint_modules(name):
    return (f'The Python module "{name}" is missing. Install the modules once:',
            f'  {PY} -m pip install -r guide{os.sep}requirements.txt',
            'Help with Python/pip: guide/SETUP.md')

def hint_no_controller():
    lines = ['- Connect the controller with a USB cable (not Bluetooth, not the puck).',
             '- Use a data cable (some cables only charge).']
    if WIN:
        lines.append('- If it is connected: close Steam completely (it can block access), then try again.')
    return lines

def hint_permission():
    if WIN:
        return ['- Close Steam and other controller tools, then try again.']
    return ['- Linux needs USB permission rules. Steam installs them; without Steam see',
            '  "USB permissions" in guide/SETUP.md, then replug the controller.']

# ------------------------------------------------------------------ steps
def connect():
    while True:
        try:
            return schid.Controller()
        except schid.MissingModule as e:
            stop('Python module missing', *hint_modules(str(e)))
        except schid.NoPermission as e:
            todo(f'Cannot access the controller: {e}', *hint_permission())
        except schid.NotFound:
            todo('No controller found.', *hint_no_controller())
        enter('Fix this, then press Enter to try again (Ctrl+C to quit) ')

def check_archive(archive, e):
    fw = os.path.join(archive, e['fw_file'])
    while True:
        if not os.path.exists(fw):
            found = [f for f in (os.path.join(d, e['fw_file']) for d in steam_folders()) if os.path.exists(f)]
            lines = [f'Needed file:  {e["fw_file"]}',
                     f'Copy it into: {os.path.abspath(archive)}',
                     'You copy it yourself; this tool does not use Steam. Steam keeps its newest firmware in:']
            lines += [f'  {d}' for d in steam_folders()]
            if found:
                lines += ['Found on this computer (copy it from here):', f'  {found[0]}']
            else:
                lines += ['Steam only keeps its newest file. If your controller runs an older version,',
                          'get the file from someone who archived it (compare the SHA-256 in firmware-table.json).']
            todo('The firmware file for your controller is not in the archive folder.', *lines)
            enter('Copy the file, then press Enter to check again (Ctrl+C to quit) ')
            continue
        if hashlib.sha256(open(fw, 'rb').read()).hexdigest() != e['fw_sha256']:
            todo(f'{e["fw_file"]} in the archive folder is not the right file (SHA-256 differs).',
                 'It is damaged or a different file with the same name. Copy it again.')
            enter('Replace the file, then press Enter to check again (Ctrl+C to quit) ')
            continue
        return fw

def read_bootloader(c, manual):
    say('', 'Step 3/4: read the app header in the bootloader',
        '  The controller restarts into its bootloader (LED off), is read, and restarts',
        '  back to normal. Nothing is written. This takes a few seconds.')
    enter()
    if manual:
        c.close()
        say('  Hold View + Menu + A, then press the Steam button. Keep holding until this continues.')
    else:
        c.reboot_to_bootloader()
        c.close()
    try:
        port = schid.wait_for(schid.find_bootloader_port, 30)
    except schid.MissingModule as e:
        stop('Python module missing', *hint_modules(str(e)))
    if not port and not manual:
        todo('The bootloader did not appear.',
             'Enter it by hand: hold View + Menu + A, then press the Steam button.')
        port = schid.wait_for(schid.find_bootloader_port, 60)
    if not port:
        stop('No bootloader found.',
             'Unplug and replug the controller: it starts normally again. Then run the tool again,',
             f'optionally with: {PY} guide{os.sep}scbackup.py --manual',
             *([] if not WIN else ['Windows: check the Device Manager for a new COM port (USB serial device).']))
    time.sleep(0.5)
    try:
        bl = schid.Bootloader(port)
    except schid.NoPermission as e:
        stop(str(e), *hint_permission(), 'Unplug and replug the controller to return to normal mode.')
    except schid.DeviceError as e:
        stop(str(e), 'Unplug and replug the controller to return to normal mode.')
    try:
        info = bl.info()
    except schid.DeviceError as e:
        bl.leave()
        stop(f'The bootloader did not answer correctly ({e}).',
             'Unplug and replug the controller to return to normal mode, then try again.')
    bl.leave()
    say(f'  bootloader on {port}: header read, controller restarting ...')
    back = schid.wait_for(schid.controller_present, 20)
    say('  controller is back in normal mode' if back else
        '  controller did not come back by itself: unplug and replug it (that is safe)')
    return info

def main(a):
    table_path = a[a.index('--table') + 1] if '--table' in a else os.path.join(ROOT, 'firmware-table.json')
    archive = a[a.index('--archive') + 1] if '--archive' in a else os.path.join(ROOT, 'firmware-archive')
    say('Steam Controller backup (read-only: nothing is written to the controller)',
        '  1. read the firmware version and check it against firmware-table.json',
        '  2. check the matching firmware file in your archive folder',
        '  3. read the app header in the bootloader (the controller restarts briefly)',
        '  4. read serials and settings and save them to one backup file')
    try:
        table = json.load(open(table_path))['builds']
    except (OSError, ValueError, KeyError) as e:
        stop(f'Cannot read the firmware table ({e}).', f'Expected at: {os.path.abspath(table_path)}')

    # 1
    say('', 'Step 1/4: firmware version')
    c = connect()
    attrs = c.attributes()
    build = f'{attrs.get(4, 0):08X}'
    serial = c.string(1) or 'unknown'
    say(f'  controller {serial}, firmware version {build}')
    e = table.get(build)
    if not e or e.get('device') != 'ibex' or not e.get('backup'):
        c.close()
        stop(f'Firmware version {build} is not supported (yet).',
             'Only versions listed in firmware-table.json with a "backup" section can be backed up.',
             'This protects you: unknown versions have not been checked.',
             'How versions are added: optional/ADD_A_FIRMWARE.md')

    # 2
    say('', 'Step 2/4: firmware file in the archive folder')
    fw = check_archive(archive, e)
    say(f'  {os.path.relpath(fw)}: SHA-256 OK')
    say('  This file is what you would restore the controller with. Keep a copy somewhere safe')
    say('  (e.g. a USB stick or cloud folder), not only in this folder.')
    if enter('Is the file kept safe? (y/N) ').lower() != 'y':
        c.close()
        stop('Please keep a safe copy of the firmware file first, then run the tool again.')
    # the controller may have been unplugged while the user copied files
    c.close()
    c = connect()

    # settings (read while the firmware is running)
    unreadable = e['backup'].get('not_readable', [])
    settings, missing = {}, []
    for k in [k for k in e['backup']['settings_keys'] if k not in unreadable]:
        try:
            v = c.read_setting(k)
        except Exception:
            v = None
        if v is None:
            missing.append(k)
        else:
            settings[k] = v.hex()

    # 3
    info = read_bootloader(c, '--manual' in a)
    bl_build = struct.unpack_from('<I', info, 0)[0]
    magic, ln, crc = struct.unpack_from('<III', info, 4)
    if (magic, ln, crc) != (int(e['app_magic'], 16), int(e['app_len'], 16), int(e['app_crc32'], 16)):
        stop('The firmware on the controller is NOT identical to the archived file.',
             f'controller: length 0x{ln:X}, CRC32 0x{crc:08X}',
             f'{e["fw_file"]}: length {e["app_len"]}, CRC32 {e["app_crc32"]}',
             'Do not flash anything. Please report this (it may be a modified or damaged firmware).')
    say(f'  app header matches {e["fw_file"]}: the controller runs exactly this file')

    # 4
    say('', 'Step 4/4: save the backup')
    uicr = info[36:164]
    ok_uicr = struct.unpack_from('<I', uicr, 0)[0] == UICR_MAGIC
    field = lambda b: b.split(b'\x00')[0].decode(errors='replace')
    ident = {'unit_serial': field(uicr[8:24]), 'pcba_serial': field(uicr[24:40]),
             'hw_id': struct.unpack_from('<I', uicr, 4)[0]} if ok_uicr else {}
    os.makedirs(OUT, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    path = os.path.join(OUT, f'{serial}_{now.strftime("%Y%m%d-%H%M%S")}.scbackup.json')
    json.dump({
        'format': 'scbackup/1', 'created_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'controller': {'serial': serial, **ident, 'uicr_provisioned': ok_uicr,
                       'attributes': {str(k): f'0x{v:08X}' for k, v in attrs.items()}},
        'firmware': {'version': build, 'fw_file': e['fw_file'], 'fw_sha256': e['fw_sha256'],
                     'app_header_ok': True, 'bootloader_build': f'0x{bl_build:08X}',
                     'table_status': {'build': e.get('status'), 'backup': e['backup'].get('status')}},
        'uicr_customer_hex': uicr.hex(),
        'bootloader_info_hex': info.hex(),
        'settings': settings,
        'settings_not_stored': missing,
        'settings_not_readable': unreadable,
    }, open(path, 'w'), indent=1)
    say(f'  serials: unit {ident.get("unit_serial", "?")}, PCBA {ident.get("pcba_serial", "?")}; '
        f'{len(settings)} settings saved')
    say('', 'DONE. Backup saved:', f'  {os.path.abspath(path)}',
        '  - Keep it private (it contains serials and pairing data) and store a copy elsewhere.',
        f'  - Together with {e["fw_file"]} it is everything needed to restore this controller.',
        '  - Not included: the bootloader (never written by USB updates), Bluetooth pairing',
        '    (re-pair instead) and gyro bias (not readable over USB).')

if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print('\ncancelled (nothing was written)')
        sys.exit(1)
