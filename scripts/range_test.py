#!/usr/bin/env python3
"""Step 3, and the one that de-risks the whole project.

MediaPipe is tuned for a hand that fills a decent part of the frame. A laptop
webcam at sofa distance sees a very small hand. If detection is unreliable at
your real viewing distance, phase 1 needs an external camera or a closer working
range - and it is far cheaper to learn that now than after building on top of it.

    python3 scripts/range_test.py

Stand where you actually watch TV. Hold each pose for a few seconds. The overlay
reports the detection rate over the last 90 frames and how much of the frame your
hand occupies. Test at night too: a backlit hand against a bright TV in a dark
room is both the worst case and exactly your use case.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gesturectl.capture import Camera            # noqa: E402
from gesturectl.classify import hand_scale       # noqa: E402
from gesturectl.vision import VisionEngine       # noqa: E402

MODEL = "models/gesture_recognizer.task"


def main() -> int:
    import cv2

    engine = VisionEngine(MODEL)
    camera = Camera(0, 640, 480)
    hits: deque[int] = deque(maxlen=90)
    poses: deque[str] = deque(maxlen=90)

    print("Stand where you actually watch TV. Press q to quit.")
    try:
        for frame in camera.frames():
            obs = engine.process(frame.rgb, int(frame.t_ms))
            hits.append(1 if obs.landmarks else 0)
            if obs.pose:
                poses.append(obs.pose)

            rate = 100.0 * sum(hits) / max(len(hits), 1)
            scale = hand_scale(obs.landmarks) if obs.landmarks else 0.0
            verdict = "GOOD" if rate > 90 else "MARGINAL" if rate > 60 else "POOR"
            colour = (149, 200, 70) if rate > 90 else (79, 150, 224) if rate > 60 else (70, 70, 220)

            cv2.putText(frame.bgr, f"detection {rate:5.1f}%  {verdict}", (12, 30),
                        cv2.FONT_HERSHEY_DUPLEX, 0.7, colour, 1, cv2.LINE_AA)
            cv2.putText(frame.bgr, f"hand size {scale:.3f} of frame width", (12, 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 205, 195), 1, cv2.LINE_AA)
            cv2.putText(frame.bgr, f"pose {obs.pose or '-'}", (12, 82),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 205, 195), 1, cv2.LINE_AA)

            if obs.landmarks:
                h, w = frame.bgr.shape[:2]
                for p in obs.landmarks:
                    cv2.circle(frame.bgr, (int(p.x * w), int(p.y * h)), 3,
                               (236, 238, 231), -1, cv2.LINE_AA)

            cv2.imshow("range test", frame.bgr)
            if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
    finally:
        camera.close()
        engine.close()
        cv2.destroyAllWindows()

    if hits:
        rate = 100.0 * sum(hits) / len(hits)
        print(f"\nOverall detection rate: {rate:.1f}%")
        if rate > 90:
            print("Good. Phase 1 works as designed with the laptop webcam.")
        elif rate > 60:
            print("Marginal. Usable up close; consider an external USB camera for the sofa.")
        else:
            print("Poor. You need an external camera, or a closer working range.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
