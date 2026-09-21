# SteamDeckJingler

Change or mute the startup jingle of the Steam Deck controller board, and document its haptic engine along the way.

Disclaimer: Yes, Claude Code was utilized during disassembly and writing documentation and code.

## Background

The controller board drives the trackpad actuators with a small synthesizer. It can do much more than the Steam Deck software uses (tones, sweeps, sampled waveforms such as voice clips). There is no public documentation of it. This repo aims to fix that for software modders.

## Goals

1. **Change or mute the startup jingle**, for personal satisfaction.
2. **Document the haptic engine** as a reference for modding → [`docs/haptic-engine.md`](docs/haptic-engine.md)

## How does it sound?

See [`examples/`](examples/) e.g. a first test of [`"Secret discovered"`](examples/secret-discovered.mp4) or  [`"Are you still there?"`](examples/are_you_still_there.mp4)

## Supported hardware

| Board | Status |
|---|---|
| Renesas RA4E1 (used in both LCD and OLED Decks) | Supported. Analysed on app builds `65E4F1AD` (pre 2026) and `69C6B03C` (2026). |
| Microchip SAMD boards | Not supported and not analysed. The app is probably similar, but free space, addresses and possibly the checksum will differ. |

## Key findings

Details and firmware addresses are in [`docs/haptic-engine.md`](docs/haptic-engine.md).

- The startup jingle is **haptic script 1**. The app region (`0x8000`–`0x3FFFF`) holds one pointer to it.
- The bootloader checks a **CRC-32 over `0x8000`–`0x3FFFB`** before starting the app. The CRC is stored at `0x3FFFC`. It uses the Ethernet polynomial but **init 0 and final XOR 0**, so it is *not* the standard CRC-32. No secret key is involved.
- The engine mixes **5 voices** at an inferred sample rate of **4080 Hz**, which limits content to about 2 kHz.
- The actuators are presumably LRAs with resonance near **170 Hz**. This is not measured, but the firmware has a fixed 170 Hz voice.
- On boards with hardware ID > 0x1F, the firmware lowers the startup jingle by **6 dB**. Its purpose is unknown (possibly to protect the amplifier circuit or the LRA).

## How the jingle is changed

1. Dump the app region ([steamdeck-controller-collect](https://github.com/syberphunk/steamdeck-controller-collect) by Stanto (syberphunk)). Keep the dump as your backup.
2. Locate the script-1 pointer by searching for the stock jingle (588 / 699 / 785 Hz, 80 ms each).
3. Choose one option:
   - **Mute:** point the pointer at an end entry (`fn = 0`). *Not implemented yet.*
   - **Replace with notes:** write new note routines and a script table into the free flash at the end of the app, then redirect the pointer.
   - **Replace with a sampled waveform:** write a short start routine, a script table and signed 8-bit samples at 4080 Hz into the free flash, then redirect the pointer.
4. Recompute the CRC and store it at `0x3FFFC`. This is required for every option, including mute, because the pointer is inside the checked range.
5. Write the image back to `0x8000`–`0x3FFFF` through the RA4E1 boot ROM. This is a separate step that you run yourself. Never touch the bootloader (`0x0`–`0x7FFF`).

Free space in app `69C6B03C` is 34,280 bytes, enough for about 8 s of samples.

## Warning

- A bad image or an interrupted write leaves the board without a working app, and the controls will not work until a valid image is written. Keep the backup from step 1. Use at your own risk.
- Do not share or commit firmware dumps. They are Valve's firmware, and collector archives contain your Deck and board serial numbers and per-unit factory data.
- This project is not affiliated with Valve.
