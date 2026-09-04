#!/usr/bin/env python3
"""Preflight. Answers "will this actually run here?" before you debug a camera.

    python3 scripts/doctor.py

Standard library only for the parts that must work before anything is installed.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def line(state: str, label: str, detail: str = "") -> None:
    print(f"[{state}] {label}" + (f"  -  {detail}" if detail else ""))


def check_python() -> bool:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} on {platform.machine()}"
    if v < (3, 11):
        line(BAD, "python", f"{detail} - need 3.11+")
        return False
    if v >= (3, 13):
        line(WARN, "python", f"{detail} - newer than MediaPipe advertises (3.9-3.12)")
        print("        The 1.0 wheel is py3-none so it usually works anyway.")
        print("        If `import mediapipe` fails below, make a 3.12 venv:")
        print("          python3.12 -m venv .venv && source .venv/bin/activate")
        return True
    line(OK, "python", detail)
    return True


def check_import(name: str, hint: str = "") -> bool:
    if importlib.util.find_spec(name) is None:
        line(BAD, name, hint or "not installed - run: pip install -e '.[dev]'")
        return False
    try:
        mod = __import__(name)
        line(OK, name, getattr(mod, "__version__", ""))
        return True
    except Exception as exc:  # noqa: BLE001
        line(BAD, name, f"installed but will not import: {exc}")
        return False


def check_model() -> bool:
    path = ROOT / "models" / "gesture_recognizer.task"
    if not path.exists():
        line(BAD, "model", "missing - run: ./scripts/fetch_model.sh")
        return False
    size = path.stat().st_size
    if size < 1_000_000:
        line(BAD, "model", f"only {size} bytes - the download was truncated, re-run it")
        return False
    line(OK, "model", f"{size / 1e6:.1f} MB")
    return True


def check_camera() -> bool:
    try:
        import cv2
    except Exception:
        line(WARN, "camera", "skipped - opencv not importable")
        return True
    cap = cv2.VideoCapture(0)
    try:
        if not cap.isOpened():
            line(BAD, "camera", "could not open index 0")
            if sys.platform == "darwin":
                print("        macOS: System Settings > Privacy & Security > Camera,")
                print("        and enable your terminal app.")
            return False
        ok, frame = cap.read()
        if not ok or frame is None:
            line(BAD, "camera", "opened but returned no frame")
            return False
        h, w = frame.shape[:2]
        line(OK, "camera", f"{w}x{h}")
        return True
    finally:
        cap.release()


def check_tv() -> bool:
    cache = ROOT / ".roku_host"
    if not cache.exists():
        line(WARN, "tv", "unknown - run: python3 scripts/check_roku.py --poke")
        return True
    line(OK, "tv", cache.read_text().strip())
    return True


def main() -> int:
    print(f"gesturectl doctor  -  {ROOT}\n")
    results = [
        check_python(),
        check_import("cv2", "not installed - run: pip install -e '.[dev]'"),
        check_import("mediapipe"),
        check_import("httpx"),
        check_import("yaml"),
        check_model(),
        check_camera(),
        check_tv(),
    ]
    print()
    if all(results):
        print("All clear. Next: python3 scripts/range_test.py, from the sofa.")
        return 0
    print("Fix the FAIL lines above, then re-run.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
