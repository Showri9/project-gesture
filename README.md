# gesturectl

Hand-gesture control for the TV — and, later, for everything else.

Point a camera at your hand, hold a pose, and the TV responds. Phase 1 runs on a
laptop webcam and controls a Roku over its local HTTP API. Nothing leaves your
network; there is no cloud service in the loop.

## Status

Phase 1 works end to end. Verified against an Insignia Roku TV (Roku OS 15.3.4)
from a MacBook Pro, Python 3.14, MediaPipe 0.10.35:

| | |
|---|---|
| TV control | working — wake, volume, mute, play/pause, power on and off |
| Gesture detection | working — seven poses, wake gate, per-gesture hold times |
| Tests | 47, all headless: no camera, no TV, no MediaPipe |
| Browser, mobile, other devices | not started |

The open question is **range**: how reliably a laptop webcam detects a hand from
where you actually sit. Step 4 below measures it, and the answer decides whether
this needs an external camera. Everything else about phase 1 is settled.

## Start here

Three checks before writing or running anything else. Each one can invalidate
what comes after it, so do them in order.

**1. Prove the TV is controllable.** Standard library only — no install needed.

```bash
python3 scripts/check_roku.py            # discover on the LAN
python3 scripts/check_roku.py --poke     # and actually move the volume
```

If it reports `is-tv true` and the poke is accepted, you're unblocked.

If the poke returns **HTTP 403**, keypresses are blocked by a TV setting:
*Settings → System → Advanced system settings → Control by mobile apps →
Network access*. Recent Roku OS ships this as **Limited**, which blocks
third-party control; set it to **Enabled**. (Roku OS 14.1+ enforces this;
older firmware ignored it. ECP is unauthenticated, so once enabled, anything
on your network can drive the TV — that is inherent to the protocol.)

If the poke **times out** instead, that is a different fault: check the POST
carries `Content-Length: 0` (Roku hangs without it), then that the panel is
awake, then restart the TV.

**2. Check Fast TV Start** under *Settings → System → Power*. A TV that fully
powers down leaves the network, so without this, `PowerOn` can never reach it.
If step 1 reported `power-mode: Ready`, it is already on — a fully-off Roku TV
answers nothing at all, so a reply while the TV is off is the proof.

**3. Install, then check the environment.**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
./scripts/fetch_model.sh
python3 scripts/doctor.py
```

`doctor.py` checks the interpreter, the imports, the model file and the camera,
and tells you what to do about each failure. Two things it watches for:

- **The MediaPipe pin.** `mediapipe` is pinned to **0.10.35** deliberately.
  The macOS arm64 wheels for 1.0.x abort at startup with
  `Check failed: service_ Service is unavailable` — their calculators require
  `kGpuService` and the Tasks API never installs it. It's an uncatchable abort,
  and no task, running mode or delegate avoids it. Don't raise the pin without
  running `./scripts/find_working_mediapipe.sh` first.
- **Python 3.13+ is fine.** MediaPipe advertises 3.9–3.12, but the wheel is
  `py3-none`, and 0.10.35 is confirmed working on 3.14.6 / arm64.
- **Camera permission.** On macOS the terminal needs it under
  *System Settings → Privacy & Security → Camera*, and a denied camera looks
  identical to a missing one.

**4. Test the range from your sofa.** This is the real risk in the project.

```bash
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

Hold **Victory** for about a second to wake it. Drop your hand, show Victory
again to put it back to sleep — and ten seconds with no hand in frame does the
same thing, so your arm gets a rest.

| Pose | Does |
|---|---|
| Victory | **wake / sleep** — hold to wake, release, show again to sleep |
| Open palm | power **on** — hold 3s |
| "I love you" | power **off** — hold 3s |
| Thumb up / down | volume, ramping while held |
| Fist | mute |
| Point up | play / pause |

Hold times are per gesture, in `config.yaml`:

```yaml
Thumb_Up:   VOLUME_UP                              # uses session.confirm_frames
Open_Palm:  {intent: POWER_ON, hold_ms: 3000}      # must be held 3 seconds
```

`hold_ms` is milliseconds rather than a frame count, because 8 frames is 270ms
on a 30fps camera and 530ms on a 15fps one — a frame count quietly means
different things on different hardware. Put a hold on anything that fires by
accident.

Nothing but the wake gesture does anything until you wake it. That single gate
is what stops the TV reacting to you scratching your nose.

A gesture that has just acted stays inert until you **release** it. That is what
makes one pose safe for both halves of a toggle: the hold that wakes you cannot
run straight on into a sleep, and holding a pose is one press rather than a
stream.

Tuning lives in `config.yaml`, not in the source, so you can adjust it from the
sofa. The dials that matter, in the order you will reach for them:
`hold_ms` on a single binding (the one to use when one specific gesture misfires),
`confirm_frames` (the global default — raise for fewer false fires, lower for
less lag), `cooldown_ms`, and `repeat_ms`.

## Run it as a service

The phone-as-sensor path. The backend keeps the session machine and the device
adapters; the phone only sends the pose it sees.

```bash
python3 -m gesturectl.server                 # http://localhost:8000
python3 -m gesturectl.server --host 0.0.0.0  # reachable from the phone
```

Drive it with no camera at all — the on-screen remote and a gesture travel the
identical path, so this is also how you test the TV:

```bash
curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/discover
curl -s -X POST localhost:8000/api/intent -H 'content-type: application/json' \
     -d '{"intent":"VOLUME_UP"}'
```

### The phone needs HTTPS

`getUserMedia` refuses to hand a camera to an insecure page, and there is no
exception for private network addresses. The Roku, meanwhile, speaks only plain
HTTP — which is exactly why the backend sits in the middle: an HTTPS page cannot
call plain HTTP, but a server can.

```bash
brew install mkcert && mkcert -install
mkcert -cert-file certs/cert.pem -key-file certs/key.pem <your-lan-ip>
python3 -m gesturectl.server --host 0.0.0.0 --cert certs/cert.pem --key certs/key.pem
```

Install mkcert's CA on the phone once (iOS: install the profile, then enable
full trust under *General → About → Certificate Trust Settings*). Don't reach
for a tunnel like ngrok — it would route your living room through the public
internet to reach a device three metres away.

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
