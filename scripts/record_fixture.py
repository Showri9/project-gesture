#!/usr/bin/env python3
"""Record a landmark sequence to tests/fixtures/<name>.json.

    python3 scripts/record_fixture.py victory

Hold the pose; press SPACE to capture a frame, q to finish. The fixtures are
flat [x, y, z, ...] arrays, which is what the TypeScript and Swift ports will
read too - so a gesture recorded once is tested in three languages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gesturectl.capture import Camera        # noqa: E402
from gesturectl.vision import VisionEngine   # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    import cv2

    engine = VisionEngine(ROOT / "models" / "gesture_recognizer.task")
    camera = Camera(0, 640, 480)
    frames: list[list[float]] = []

    print("SPACE captures a frame, q finishes.")
    try:
        for frame in camera.frames():
            obs = engine.process(frame.rgb, int(frame.t_ms))
            h, w = frame.bgr.shape[:2]
            if obs.landmarks:
                for p in obs.landmarks:
                    cv2.circle(frame.bgr, (int(p.x * w), int(p.y * h)), 3,
                               (236, 238, 231), -1, cv2.LINE_AA)
            cv2.putText(frame.bgr, f"{name}: {len(frames)} captured", (12, 28),
                        cv2.FONT_HERSHEY_DUPLEX, 0.6, (149, 200, 70), 1, cv2.LINE_AA)
            cv2.imshow("record", frame.bgr)

            key = cv2.waitKey(1) & 0xFF
            if key == ord(" ") and obs.landmarks:
                frames.append([c for p in obs.landmarks for c in (p.x, p.y, p.z)])
                print(f"  captured {len(frames)}")
            elif key in (27, ord("q")):
                break
    finally:
        camera.close()
        engine.close()
        cv2.destroyAllWindows()

    if not frames:
        print("Nothing captured.")
        return 1
    out = ROOT / "tests" / "fixtures" / f"{name}.json"
    out.write_text(json.dumps({"name": name, "frames": frames}, indent=2))
    print(f"Wrote {len(frames)} frames to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
