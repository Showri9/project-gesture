"""Camera loop. Small on purpose - the interesting code is elsewhere."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Frame:
    bgr: object      # numpy HxWx3, BGR (what OpenCV gives and draws)
    rgb: object      # numpy HxWx3, RGB (what MediaPipe wants)
    t_ms: float      # milliseconds since capture started


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Could not open camera {index}. On macOS, grant camera access to "
                "your terminal in System Settings > Privacy & Security > Camera."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._t0 = time.monotonic()

    def frames(self) -> Iterator[Frame]:
        cv2 = self._cv2
        while True:
            ok, bgr = self._cap.read()
            if not ok:
                break
            # mirror, so moving your hand right moves it right on screen
            bgr = cv2.flip(bgr, 1)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            yield Frame(bgr, rgb, (time.monotonic() - self._t0) * 1000.0)

    def close(self) -> None:
        self._cap.release()
