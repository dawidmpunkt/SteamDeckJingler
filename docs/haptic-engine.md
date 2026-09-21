# Haptic engine reference (RA4E1 controller board)

What is inside the controller-board firmware, reverse-engineered from the app image. Not a measurement of the actuator or the sound.

Addresses are flash addresses of app build **`69C6B03C`** unless marked "old" (app build `65E4F1AD`, same code at different addresses — see [Old vs. new app](#old-vs-new-app) for the mapping).

## Memory map

| Region | Address range | Notes |
|---|---|---|
| Bootloader | `0x0`–`0x7FFF` | Byte-identical between app builds `65E4F1AD` and `69C6B03C`. Build stamp `62A9122B` (2022-06-14 22:56:43 UTC). |
| App | `0x8000`–`0x3FFFF` | Vector table at `0x8000`. |
| App code, build `65E4F1AD` | `0x8000`–`0x379AB` | |
| App code, build `69C6B03C` | `0x8000`–`0x37A13` | 104 B larger than `65E4F1AD`. |
| Free flash (0xFF), build `65E4F1AD` | `0x379AC`–`0x3FFFB` | 34,384 B |
| Free flash (0xFF), build `69C6B03C` | `0x37A14`–`0x3FFFB` | 34,280 B |
| Config area | `0x0100A100`–`0x0100A2FF` | 512 B, identical across dumps seen |
| Data flash | `0x08000000`–`0x08001FFF` | 8 KiB, 32 blocks of 256 B |

## Boot check and app checksum (H)

The bootloader (function `0x2C50`) validates the app before starting it:

1. Reset vector at `0x8004` must lie in `(0x8000, 0x40000)`; stack pointer at `0x8000` must lie in `(0x20000000, 0x20020000)`. Otherwise: "Bogus app start/stack address".
2. A "Target MCU image mismatch" check exists but is a stub that always passes in this build.
3. CRC-32 over **`[0x8000, 0x3FFFC)`** (includes 0xFF padding) is compared against the little-endian word stored at **`0x3FFFC`**. On mismatch the bootloader does not start the app.

**CRC parameters:** polynomial `0x04C11DB7`, init `0`, input and output reflected, final XOR `0`. This is *not* the common CRC-32 (which uses init `0xFFFFFFFF` and final XOR `0xFFFFFFFF`, e.g. [CRC-32/ISO-HDLC](https://reveng.sourceforge.io/crc-catalogue/17plus.htm)):

```python
crc = zlib.crc32(app[0x0:0x37FFC], 0xFFFFFFFF) ^ 0xFFFFFFFF   # app[] starts at flash 0x8000
app[0x37FFC:0x38000] = crc.to_bytes(4, "little")
```

Same algorithm and stored-word convention is used for the per-block checksums in data flash (below). The same function also accepts a debugger-attached override (checked via what looks like `DHCSR` at `0x2001FF04`) that skips the CRC check. address not confirmed against an ARM reference.

## Data flash block 0 — device info (H)

`0x08000000`–`0x080000FF`:

| Offset | Size | Field |
|---|---|---|
| `+0x00` | 4 | CRC-32 (LE), same parameters as above, over `+0x04`..`+0xFF` |
| `+0x04` | 4 | Magic `CE FA EF BE` (`0xBEEFFACE` LE). No code was found that checks it. |
| `+0x08` | 4 | Value `1` |
| `+0x0C` | 4 | Hardware ID. Values > `0x1F` get an extra −6 dB on the startup jingle (see [Startup jingle](#startup-jingle)). |
| `+0x10` | 30 | Serial 1 ("board serial"), NUL-padded ASCII. Default text when the block is invalid: `NO SERIAL`. |
| `+0x2E` | 30 | Serial 2 ("mainboard serial"), NUL-padded ASCII. Default text: `No Unit ID`. |
| `+0x4C`–`0xFF` | 180 | `0xFF` |

Block 0 is read in four places: the bootloader's USB serial-number string descriptor (serial 1 only, accepts `[0-9A-Za-z]`, falls back to `"N/A"`); a bootloader attribute reply that returns the hardware ID; and the app's block validator/loader (below), which on success copies the hardware ID and both serials into RAM, or on failure logs *"Board not provisioned. Setting to Defualts"* [firmware's spelling] and uses the two default strings above.

### Other data-flash blocks

| Block(s) | Address | Content |
|---|---|---|
| 0 | `0x0800_0000` | Device info, above |
| 1 | `0x0800_0100` | Text blob `VER=1;DC=<date>;DCS=R` |
| 2, 3 | `0x0800_0200`–`0x0300` | No valid CRC |
| 4–13 | `0x0800_0400`–`0x0D00` | Per-unit data. Blocks 10/11 look like calibration data (133/131 non-`0xFF` bytes). |
| 13 | `0x0800_0D00` | 2-byte payload read by the boot task: byte 0 must equal `1`; byte 1 (≤ 2) controls a second −6 dB offset on the startup jingle when it equals `1` (M — purpose of the setting beyond that is unknown). |
| 14–31 | `0x0800_0E00`–`0x1F00` | No valid CRC, random-looking data. Reachable only via a full boot-ROM read, not the USB bootloader interface. |

**Block validator** (app): for index ≤ `0x1F`, address = `0x08000000 + index·0x100`; CRC over `0xFC` bytes at `addr+4`, compared to the word at `addr`.

## Boot chain to the startup jingle (H unless noted)

1. A RAM flag word at **`0x20006970`** is initialised to `0xFFFFFFFF` at boot, and a byte at `0x2000696C` to `0x0D`.
2. The boot task checks bit 3 of that flag. If set, it starts **haptic script 1** on both sides (left/right), passing a gain offset of `0`, `−6` or `−12` dB depending on hardware ID and data-flash block 13 (see above).
3. Starting a script arms an RTOS timer per side; the timer callback executes one script step at a time and ends the script when it reaches an entry with `fn = 0`.
4. While a script is active, starting another script logs *"Haptics script already active"*; an unrecognised script ID logs *"Unknown script"*.

## Startup jingle

- **Trigger:** haptic **script 1** (see boot chain above).
- **Script-1 pointer:** literal at `0x19650` → table at `0x36264` (old build: `0x195A8` → `0x361D4`).
- **Stock notes:** 588 Hz, 699 Hz, 785 Hz — 80 ms each, 81 ms apart, gain −3 dB, played via `tone()` (below).
- **Gain offset applied by the boot task:** −6 dB if hardware ID (data-flash block 0, `+0x0C`) is > `0x1F`; an additional −6 dB if data-flash block 13 byte 1 equals `1`.

### Script format (H structure, M unit)

Each haptic script is a table of 8-byte entries: `{u32 delay, u32 fn}`. `fn` is a Thumb function pointer (bit 0 set); `fn = 0` ends the script. `delay` is the wait before that entry executes; the unit is assumed to be milliseconds (not independently confirmed).

Four stock scripts exist (old table address → new table address):

| Script | Old → new table | Steps (delay → note/action) |
|---|---|---|
| 1 (startup) | `0x361D4` → `0x36264` | `0`→588 Hz, `81`→699 Hz, `81`→785 Hz, `81`→end |
| 2 | `0x361AC` → `0x3623C` | `304`→pulse (wavetable, level 2), `130`→pulse, `120`→188 Hz/1000 ms, `1500`→178 Hz/1500 ms (+LFO), `1500`→end |
| 3 | `0x36184` → `0x36214` | `0`→900 Hz, `120`→985 Hz, `120`→900 Hz, `120`→985 Hz, `120`→end |
| 4 | `0x3615C` → `0x361EC` | `0`→985 Hz, `120`→900 Hz, `120`→985 Hz, `120`→900 Hz, `120`→end |

Each note entry is a small function ("note callback") that calls `tone(ctx, freq, dur, gain, arg5, arg6)` — `0x18F7C` in the new build, `0x18F24` in the old build — which builds a type-3 message (below) and hands it to the dispatcher.

While a script runs, the engine ignores further script-start requests (M, from the "already active" message above) — so a long jingle blocks the other three stock scripts until it finishes.

## Engine

- Each side (left/right trackpad) has an independent mixing synthesizer with **5 voice slots** of 0x48 bytes each, at `ctx+0x3E8`, `+0x430`, `+0x478`, `+0x4C0`, `+0x508`.
- **Sample tick rate: 4080 Hz** (inferred from the Q16 constant `0x0FF00000` = 4080.0, used throughout the frequency/duration math). This caps usable content at roughly 2 kHz.
- Every tick, each active slot produces one sample per its voice type; all 5 are summed with saturation, scaled by the global gain (setting `0x4C`, below), and clamped to **±511** before being written to the output register.
- Q16 fixed-point helpers: `0x1C76C` multiply, `0x1C79C` divide, `0x1C734` saturating add, `0x1C750` saturating subtract, `0x1CA68` sine, `0x1C350` dB→linear.
- Starting any voice calls a "kick" function pointer at `ctx+0x558` (argument `ctx+0x55C`) that arms the sample-rate timer.

### Slot layout (offsets relative to slot base)

| Offset | Field |
|---|---|
| `+0x00` | active (byte) |
| `+0x01` | voice type, 0–4 |
| `+0x02` | u16 length in samples (= duration · 4) |
| `+0x04` | amplitude, Q16 linear (from gain in dB via `0x1C350`) |
| `+0x0C` | s16 direction/step |
| `+0x14` | u16 sample counter |
| `+0x18` | phase, Q16 radians |
| `+0x1C` | phase increment = `2π·f/4080` |
| `+0x28`/`+0x2C` | LFO increment / depth (voice types with LFO) |
| `+0x30` | envelope (`0x10000` = 1.0) |

### Voice types (engine loop `0x18FF4`)

| Type | Handler | Behaviour |
|---|---|---|
| 0 | `0x191B6` | Wavetable: plays signed 8-bit samples from any flash address, one per tick, no resampling. See below. |
| 1 | `0x19218` | Sine tone: `sample = sin(phase) × amplitude` (× LFO if `+0x410 ≠ 0`); ends when the sample counter reaches the length. |
| 2 | `0x1911A` | Sine with a cycle-countdown byte (`+0x35`, init `2`) and envelope/tail state. |
| 3 | `0x1909E` | Sine with a cycle counter compared against `+0x20`/`+0x22` and a per-context global cycle count (`ctx+0x554`). |
| 4 | `0x19024` | Timed voice: counter (`+0x36`) vs. limit (`+0x38`); used by the sweep message. |

### Message types (dispatcher `0x18EB0`)

Message struct: byte 1 = type; byte 2 = level/mode; byte 3 = gain (int8 dB, clamped to setting `0x4C`'s min/max); bytes 4–5 = u16 frequency (types 3, 4); bytes 6–7 = s16 duration (×4 = samples, sign = direction, stored at slot `+0xC`); bytes 8–9 = u16 (type 5); bytes 10–11 = u16 "arg5"; byte 12 = "arg6" (types 3, 4); bytes `0xF`–`0x12` = two u16 (type 7).

| Type | Slot / voice | Effect |
|---|---|---|
| 0 | all | Stop all voices (clears `ctx+0x3E8`, 0x168 bytes) |
| 1 | 0 / 0 | Wavetable click pattern; only starts if slot 0 is idle |
| 2 | 0 / 0 | Wavetable click pattern; always starts, replacing a running one |
| 3 | 1 / 1 | Sine tone (frequency, duration, gain, optional LFO). Used by the jingle scripts via `tone()`. |
| 4 | 2 / 2 | Sine burst with a cycle countdown |
| 5 | 3 / 3 | Fixed **170 Hz** voice (phase increment from constant `0xAA0000`); extra u16 (bytes 8–9) stored at slot `+0x4E0`, purpose unconfirmed |
| 6 | – | Unhandled; logs `"ERROR: Unhandled haptic_event %u"` |
| 7 | 4 / 4 | Sweep/chirp, parameters from bytes `0xF`–`0x12` and the duration field |

Types 0, 4, 5 and 7 are reachable only through one wrapper (`0x18FB8`) inside a larger, unanalysed host-command/pad-feedback handler at `0x1A818`. The jingle scripts only ever emit type 3.

### Sampled waveforms (voice type 0)

The wavetable voice reads **signed 8-bit samples** from any flash address, one per engine tick:

- `sample = (int8) table[index]`; `table` and `index` (u16) live in the slot at `+0x3F8`/`+0x3FC`.
- `output = (sample << 18) × amplitude(+0x3EC, Q16) × level(+0x3F0, Q16, normally 0x10000 = 1.0)`.
- When `index` reaches the length (u16 at `+0x3EA`): if the repeat count at `+0x3F4` (s16) is negative, the voice loops forever; otherwise it is decremented, and the voice deactivates once it reaches 0. If the decremented count equals a second u16 at `+0x3F6`, the amplitude sign flips for the next pass (this is how the stock "click" plays a positive half-cycle then a negative one).
- Starting the voice (routine `0x186F0`): with interrupts masked, sets `+0x3E8 = (length<<16)|1` (active, type 0), `+0x3EC` = linear gain, `+0x3F0 = 0x10000`, `+0x3F4` = repeat count, `+0x3F8`= table pointer, `+0x3FC = 0`, then calls the kick function.
- Length is a u16, so one pass is limited to 65,535 samples (≈ 16 s at 4080 Hz).

Stock wavetable data: a 200-sample damped-oscillation "click" at `0x360F4`, and a 24-sample square + 24-sample sine table at `0x361BC` (24 samples ÷ 4080 Hz = 170 Hz exactly), played at lengths 8, 12 or 24 by message types 1/2.

Output stage (all voice types): the 5 slots are summed with saturation, multiplied by the global gain (setting `0x4C`), `+0x8000`, `>>16`, clamped to **±511**, and written as an s16 to a caller-supplied pointer. Two per-side wrapper functions (`0x12D0C`, `0x12D20`) are registered with what looks like a timer/PWM driver (`0x131C8` → `0xCDBC`/`0xCC48`…), which is assumed to call them once per tick; the timer type, PWM period and pin mapping were not identified.

## Settings

83 settings (IDs `0x00`–`0x52`) are held as `s16` in RAM (array `0x2000861C`, getter `0x17038`; out-of-range ID returns 0). Descriptors (12 bytes: `s16 default, s16 min, s16 max, s16 0, u32 0x00026298`) are in flash at `0x35BB0`.

### Settings with an inferred meaning

| ID | Default `[min..max]` | Meaning |
|---|---|---|
| `0x4C` | `0 [-24..6]` | Global haptic gain, dB. Scales the mixed output and is the clamp used for per-message gain bytes. |
| `0x46` | `1 [0..2]` | Engine on/off: `0` clears all voice slots after every tick. |
| `0x1A` | `0 [0..1]` | Selects between two variants of the wavetable click messages (types 1/2). |
| `0x4F` | `2 [1..4]` | Click "level" used when a type 1/2 message's level byte is 0. |

### Full settings table

`id:default[min..max]`, IDs `0x00`–`0x52`:

```
00:0[0..10]      01:2[0..10]      02:0[0..360]     03:1200[0..25000] 04:0[0..1]
05:0[0..1]       06:0[0..255]     07:0[0..8]       08:0[0..8]        09:1[0..1]
0A:7000[0..16384] 0B:200[0..1000] 0C:100[1..1000]  0D:50[1..500]     0E:7000[0..32767]
0F:923[0..2000]  10:382[0..2000]  11:2[1..10]      12:8000[0..20000] 13:1770[0..4096]
14:1630[0..4096] 15:5[0..500]     16:2[0..8]       17:2[0..8]        18:20[0..30]
19:40[3..99]     1A:0[0..1]       1B:27500[1..32767] 1C:16500[0..4096] 1D:15000[0..4096]
1E:0[0..2000]    1F:12000[1..16384] 20:1[0..1]     21:4000[0..8000]  22:200[1..500]
23:0[0..1]       24:0[0..1]       25:300[10..1500] 26:50[1..1000]    27:0[0..1]
28:20[1..180]    29:3900[0..25000] 2A:1[0..1]      2B:0[0..1]        2C:100[0..100]
2D:100[0..100]   2E:0[0..1]       2F:0[0..16]      30:1[0..32767]    31:2[1..2]
32:1800[0..32767] 33:250[0..32767] 34:50[-1..100]  35:50[-1..100]    36:100[0..100]
37:100[0..100]   38:10[0..100]    39:10[0..100]    3A:0[0..100]      3B:0[0..100]
3C:0[0..1]       3D:0[0..15]      3E:0[0..1]       3F:150[0..300]    40:4[1..16]
41:1[0..1]       42:1[0..1]       43:0[0..7]       44:90[40..99]     45:1[0..1]
46:1[0..2]       47:1[0..1]       48:1400[0..16000] 49:1000[0..16000] 4A:1[0..1]
4B:0[0..0]       4C:0[-24..6]     4D:50[10..100]   4E:1[0..1]        4F:2[1..4]
50:1[0..2]       51:0[0..1]       52:3[0..3]
```

## Implementation across different firmware versions

Across two different firmware versions, the haptic engine and startup jingle were implemented identically. Only the addresses shifted (mostly by a constant `+0xA8` in the jingle/engine code, see table). Free flash also differs slightly (34,384 B in `65E4F1AD` vs. 34,280 B in `69C6B03C`).

| Item | Old FW (pre 2026) (`65E4F1AD`) | New FW (2026) (`69C6B03C`) |
|---|---|---|
| Boot task / jingle trigger | `0x172A0` | `0x172DC` |
| Script trigger (pend call) | `0x196F8` | `0x197A0` |
| Script start (dispatch on id) | `0x194D4` | `0x1957C` |
| Tone function `tone()` | `0x18F24` | `0x18F7C` |
| Message dispatcher | `0x18E58` | `0x18EB0` |
| Type-3 (tone) handler | `0x18BF0` | `0x18C4C` |
| Block validator | `0x13990` | `0x13A2C` → `0x139D4` |
| Provisioning loader | `0x19D94` | `0x19E34` |
| Script-1 pointer literal | `0x195A8` | `0x19650` |

## Open questions

- Actuator type and real resonance frequency (170 Hz is an assumption, not a measurement).
- Purpose of the −6 dB hardware-ID offset and of data-flash block 13.
- Timer/PWM driver, pin mapping, and how the two output values map to left/right.
- Full behaviour of voice types 2 and 3 (envelope/tail state), and of message types 1, 2, 4, 5, 7 beyond what is listed above.
- The host-command handler at `0x1A818` that is the only way to reach message types 0, 4, 5 and 7, and how Steam/SteamOS drives it.
- Meaning of most of the 83 settings beyond the four listed above.
- Memory map, checksum and engine addresses on Microchip SAMD-based boards (not analysed at all).
