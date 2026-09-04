"""MediaPipe Tasks wrapper. The only file that knows what MediaPipe is.

Everything downstream sees an Observation: 21 landmarks and a pose name. That is
the seam that lets the browser (phase 2) and the phone (phase 3) swap this file
out and change nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classify import Landmarks, Point, classify_pose


@dataclass(frozen=True, slots=True)
class Observation:
    landmarks: Landmarks | None
    pose: str | None
    confidence: float
    #: True when the pose came from our own geometry rather than the trained model
    from_rules: bool = False


class VisionEngine:
    """Wraps MediaPipe's GestureRecognizer, with classify.py as the fallback.

    The trained recognizer is more robust than hand-rolled rules for the seven
    poses it knows, so it wins when it is confident. classify.py catches the rest
    and is where gestures beyond those seven will live.
    """

    def __init__(
        self,
        model_path: str | Path,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        import mediapipe as mp  # imported late so tests never need it
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model not found at {model_path}. Run scripts/fetch_model.sh first."
            )

        self._mp = mp
        options = mp_vision.GestureRecognizerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._recognizer = mp_vision.GestureRecognizer.create_from_options(options)

    def process(self, frame_rgb, timestamp_ms: int) -> Observation:
        """`frame_rgb` is an HxWx3 uint8 RGB array."""
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=frame_rgb
        )
        result = self._recognizer.recognize_for_video(image, timestamp_ms)

        if not result.hand_landmarks:
            return Observation(None, None, 0.0)

        landmarks = [Point(p.x, p.y, p.z) for p in result.hand_landmarks[0]]

        if result.gestures and result.gestures[0]:
            top = result.gestures[0][0]
            if top.category_name and top.category_name != "None":
                return Observation(landmarks, top.category_name, float(top.score))

        pose = classify_pose(landmarks)
        return Observation(landmarks, pose, 0.8 if pose else 0.0, from_rules=True)

    def close(self) -> None:
        self._recognizer.close()
