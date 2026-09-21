# The startup jingle: how it plays, how it's built, and how to change it

This file walks through the startup jingle specifically — how the stock one plays, how its data is laid out, and two concrete ways to change it. For firmware facts (addresses, formats, the engine itself) this links into [`haptic-engine.md`](haptic-engine.md) rather than repeating it.

> Mute (below) needs no new code — just a few bytes changed and the checksum redone. Replacing the jingle with a different melody additionally needs small hand-written routines per note; the planned jingle tool (see the main [README](../README.md)) would generate those. Neither has been built or tried on hardware yet — this is the plan, worked through with real numbers.

## How the stock jingle plays

1. On every power-up, once the app passes the [boot check](haptic-engine.md#boot-check-and-app-checksum-h), the boot task sets a RAM flag that starts **haptic script 1** on both sides of the controller — see [Boot chain to the startup jingle](haptic-engine.md#boot-chain-to-the-startup-jingle-h-unless-noted).
2. Script 1 is a table of notes (below). An RTOS timer steps through the table one entry at a time, waiting the entry's `delay` before acting on it — see [Script format](haptic-engine.md#script-format-h-structure-m-unit).
3. Each note entry is a tiny routine that calls `tone(ctx, freq, dur, gain, arg5, arg6)`, which builds a "play this tone" message and hands it to the engine's dispatcher — see [Message types](haptic-engine.md#message-types-dispatcher-0x18eb0), type 3.
4. The dispatcher starts one sine-wave voice (slot 1 of 5) at that frequency, for that duration, at that gain. Only one note plays at a time — see [Polyphony](haptic-engine.md#polyphony--mixing-more-than-one-voice) for why, and what using more than one slot would take.
5. The engine mixes and clamps its output and drives the actuator — see [Engine](haptic-engine.md#engine).
6. The boot task itself also lowers the volume by up to 12 dB depending on the board's hardware ID and a data-flash setting, on top of each note's own gain — see [Startup jingle](haptic-engine.md#startup-jingle).

## How it's constructed

The stock script 1 is 3 notes:

| Step | Delay before it (ms) | Note | Gain |
|---|---|---|---|
| 1 | 0 | 588 Hz, 80 ms | −3 dB |
| 2 | 81 | 699 Hz, 80 ms | −3 dB |
| 3 | 81 | 785 Hz, 80 ms | −3 dB |
| end | 81 | — | — |

The table lives in flash, and a single 4-byte pointer (at a fixed location, `0x19650` in app build `69C6B03C`) says where it is. Changing the jingle only ever means: build a new table (and, for anything other than "silence", the small per-note routines that call `tone()`), put it somewhere in flash, and repoint that one pointer at it. See [Script format](haptic-engine.md#script-format-h-structure-m-unit) for the exact table layout, and [Memory map](haptic-engine.md#memory-map) for how much free flash there is to put a new one in.

Whatever changes, the app's checksum has to be recomputed and rewritten, because the pointer itself sits inside the range the bootloader checks — see [Boot check and app checksum](haptic-engine.md#boot-check-and-app-checksum-h) for the exact algorithm (it is *not* plain CRC-32, see the README's Findings).

## 1. Muting the jingle

Muting doesn't need a real note table — just a table whose very first entry already says "end". Looking at the entry format `{u32 delay, u32 fn}` ([Script format](haptic-engine.md#script-format-h-structure-m-unit)), `fn = 0` is what ends a script, so an 8-byte block of zeros is a valid "empty" script:

```
delay = 0x00000000   fn = 0x00000000
```

The plan:

1. Pick 8 free bytes in the app's free-flash region (e.g. the very start of it — see [Memory map](haptic-engine.md#memory-map)) and write `00 00 00 00 00 00 00 00` there.
2. Overwrite the 4-byte pointer at `0x19650` with that address.
3. Recompute the checksum over `[0x8000, 0x3FFFC)` and write it to `0x3FFFC` — see [Boot check and app checksum](haptic-engine.md#boot-check-and-app-checksum-h).

That's the entire change: 8 new bytes, one 4-byte pointer, one new checksum. This is simple enough to do by hand with a hex editor and the checksum snippet in `haptic-engine.md`; it doesn't need the jingle tool.

*Open question, not resolved by the firmware alone:* whether the very first entry being the end marker plays nothing at all, or whether the RTOS timer still waits out the `delay` word before checking `fn`. Using `delay = 0` avoids the question either way — worst case, a 0 ms wait before ending.

## 2. Replacing the jingle with a different melody

Unlike muting, this needs one small routine per note (each one loads that note's frequency/duration/gain and calls `tone()`, following the same pattern as the stock notes — see [Script format](haptic-engine.md#script-format-h-structure-m-unit)) plus a table pointing at them, written into free flash, with the pointer and checksum updated exactly as above. This is what the planned jingle tool would generate automatically (see the README's Option B); by hand it means hand-assembling those Thumb-2 routines.

### Worked example: "Twinkle, Twinkle, Little Star", opening phrase

The tune is the French melody "Ah! vous dirai-je, Maman," first published in 1761 [(Wikipedia)](https://en.wikipedia.org/wiki/Twinkle,_Twinkle,_Little_Star) — 260+ years old, so it's free to use; nothing modern or game-related is shipped here, consistent with the repo's rule of not distributing third-party jingle audio.

The first phrase ("Twin-kle, twin-kle, lit-tle star") is 7 notes: C C G G A A G. At a 280 ms note / 40 ms gap pace, that's about 2.2 seconds — comfortably in the requested 2–3 s range, and every frequency is well under the engine's roughly 2 kHz ceiling (see [Startup jingle](haptic-engine.md#startup-jingle)).

| Step | Note | Frequency (Hz, equal temperament) | Duration | Delay before it |
|---|---|---|---|---|
| 1 | C5 | 523 | 280 ms | 0 |
| 2 | C5 | 523 | 280 ms | 320 ms |
| 3 | G5 | 784 | 280 ms | 320 ms |
| 4 | G5 | 784 | 280 ms | 320 ms |
| 5 | A5 | 880 | 280 ms | 320 ms |
| 6 | A5 | 880 | 280 ms | 320 ms |
| 7 | G5 | 784 | 280 ms | 320 ms |
| end | — | — | — | 320 ms |

Suggested gain: −3 dB per note, matching the stock jingle (adjustable within the global −24…+6 dB haptic-gain setting — see [Settings](haptic-engine.md#settings)).

To build this: 7 note routines + an 8-entry table (7 notes + end) go into free flash, the pointer at `0x19650` is redirected to the new table's address, and the checksum is redone — same last two steps as muting. Free flash (tens of kilobytes — see [Memory map](haptic-engine.md#memory-map)) has ample room for a phrase this short; a much longer melody would eventually need checking that it still fits, which is exactly what step 4 of the README's Option B procedure does.
