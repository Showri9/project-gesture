# gesturectl

Hand-gesture control for the TV — and, later, for everything else.

Point a camera at your hand, hold a pose, and the TV responds. Phase 1 runs on a
laptop webcam and controls a Roku over its local HTTP API. Nothing leaves your
network; there is no cloud service in the loop.

## Start here

Three checks before writing or running anything else. Each one can invalidate
what comes after it, so do them in order.

**1. Prove the TV is controllable.** Standard library only — no install needed.

```bash
python3 scripts/check_roku.py            # discover on the LAN
python3 scripts/check_roku.py --poke     # and actually move the volume
```

If it reports `is-tv true` and the poke is accepted, you're unblocked. If the
poke returns HTTP 403, enable *Settings → System → Advanced system settings →
Control by mobile apps → Network access* (Roku OS 14.1+ requires it; older
firmware generally just answers).

**2. Enable Fast TV Start** under *Settings → System → Power*. A TV that fully
powers down leaves the network, so without this, `PowerOn` can never reach it.

**3. Test the range from your sofa.** This is the real risk in the project.

```bash
pip install -e ".[dev]"
./scripts/fetch_model.sh
python3 scripts/range_test.py
```

MediaPipe is tuned for a hand that fills a decent part of the frame, and a laptop
webcam at 3–4 metres sees a very small hand. Stand where you actually watch TV.
Test at night too — a backlit hand against a bright TV in a dark room is both the
worst case and exactly the use case. Below ~90% detection you want an external
USB camera rather than a cleverer classifier.

## Run it

```bash
python3 -m gesturectl.main --dry-run   # detect and log, send nothing
python3 -m gesturectl.main             # for real
```

Hold an **open palm** for about a second to wake it. A **fist** puts it back to
sleep, and so does ten seconds with no hand in frame.

| Pose | Does |
|---|---|
| Open palm (held) | wake |
| Fist | sleep |
| Thumb up / down | volume, ramping while held |
| Victory | mute |
| Point up | play / pause |
| "I love you" | power — asks twice |

Tuning lives in `config.yaml`, not in the source, so you can adjust it from the
sofa. The three dials that matter: `confirm_frames` (raise for fewer false
fires, lower for less lag), `cooldown_ms`, and `repeat_ms`.

## How it's put together

```
camera → vision → landmarks → classify → session → intent → adapter → device
                              └──────── portable core ────────┘
```

`classify`, `motion`, `session` and `intents` are pure functions over
coordinates — no I/O, no third-party imports. They are the four modules that get
ported to TypeScript (browser) and Swift/Kotlin (mobile), and the JSON fixtures
in `tests/fixtures/` test all three implementations with identical inputs.

The seam between the two halves is a platform-neutral intent message:

```json
{"intent": "VOLUME_UP", "target": "living-room", "source": "gesture",
 "gesture": "Thumb_Up", "confidence": 0.94, "repeat": true, "ts": 1757001234.567}
```

No IP address, no HTTP verb, no Roku key name — the adapter owns that
translation. A voice front-end or a wearable emits the same message and nothing
downstream changes.

## Tests

```bash
pytest
```

The state machine and the classifier test headlessly: no camera, no TV, no
MediaPipe. That's deliberate — every false-positive and runaway-repeat bug this
product could have is a bug in `session.py`, and all of it is deterministic.

## Devices

| Device | Protocol | Status |
|---|---|---|
| Roku / Roku TV | ECP, HTTP :8060 | implemented |
| Fire TV | ADB, TCP :5555 (`adb-shell`) | next |
| TCL Google TV | Android TV Remote v2, TLS :6466 (`androidtvremote2`) | next |

Roku volume, mute and power keys exist on Roku **TVs** only, not on sticks and
boxes, so the adapter reads `is-tv` from `query/device-info` at startup and
advertises its real capability set rather than assuming one.

## Where this goes

1. **Laptop → Roku** — this repo.
2. **Browser** — React + MediaPipe Tasks Vision JS in a Web Worker. Detection
   stays client-side; only intent JSON reaches the server.
3. **iOS / Android** — React Native reusing the phase 2 TypeScript core, plus a
   thin native MediaPipe module.
4. **Ecosystem, then wearable** — adapt to Home Assistant or Matter rather than
   writing a driver per brand.

The full architecture, gesture design rationale, latency budget and risk list are
in the project blueprint.

## Licence

MIT
