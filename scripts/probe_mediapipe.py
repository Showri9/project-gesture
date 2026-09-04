#!/usr/bin/env python3
"""Find a MediaPipe configuration that does not crash on this machine.

    python3 scripts/probe_mediapipe.py

A failed CHECK inside MediaPipe aborts the whole process - you cannot catch it,
so configurations cannot be tried in a loop. Each one runs in its own
subprocess, and the survivors are reported at the end along with the real error
text from the ones that died.

Known to matter on Apple Silicon: MediaPipe's macOS build initialises Metal in
TensorsToDetectionsCalculator::Open() regardless of the delegate you ask for,
because the delegate selects the inference backend, not the post-processing
calculators. So a Metal crash is not fixed by asking for the CPU delegate, and
the useful question becomes which task and running mode avoid that code path.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "gesture_recognizer.task"
HAND_MODEL = ROOT / "models" / "hand_landmarker.task"

CASES: list[tuple[str, str]] = [
    (
        "GestureRecognizer / IMAGE / cpu",
        "gesture:IMAGE:CPU",
    ),
    (
        "GestureRecognizer / VIDEO / cpu",
        "gesture:VIDEO:CPU",
    ),
    (
        "GestureRecognizer / IMAGE / default",
        "gesture:IMAGE:NONE",
    ),
    (
        "GestureRecognizer / VIDEO / default",
        "gesture:VIDEO:NONE",
    ),
    (
        "HandLandmarker / IMAGE / cpu",
        "hand:IMAGE:CPU",
    ),
    (
        "HandLandmarker / VIDEO / cpu",
        "hand:VIDEO:CPU",
    ),
]

CHILD = textwrap.dedent(
    '''
    import sys
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    task, mode, delegate = sys.argv[1].split(":")
    model = sys.argv[2]

    base = mp_python.BaseOptions(model_asset_path=model)
    if delegate == "CPU":
        base = mp_python.BaseOptions(
            model_asset_path=model,
            delegate=mp_python.BaseOptions.Delegate.CPU,
        )

    running = getattr(mp_vision.RunningMode, mode)
    if task == "gesture":
        options = mp_vision.GestureRecognizerOptions(
            base_options=base, running_mode=running, num_hands=1
        )
        detector = mp_vision.GestureRecognizer.create_from_options(options)
    else:
        options = mp_vision.HandLandmarkerOptions(
            base_options=base, running_mode=running, num_hands=1
        )
        detector = mp_vision.HandLandmarker.create_from_options(options)

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
    if mode == "VIDEO":
        fn = detector.recognize_for_video if task == "gesture" else detector.detect_for_video
        fn(image, 0)
        fn(image, 33)
    else:
        fn = detector.recognize if task == "gesture" else detector.detect
        fn(image)
    detector.close()
    print("SURVIVED")
    '''
).strip()


def run(spec: str, model: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, spec, str(model)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode == 0 and "SURVIVED" in proc.stdout:
        return True, ""
    err = (proc.stderr or "").strip().splitlines()
    interesting = [
        ln for ln in err
        if any(k in ln for k in ("CHECK", "Check fail", "Error", "error:", "F0", "E0",
                                 "Exception", "Traceback", "RuntimeError", "abort"))
    ]
    detail = interesting[0] if interesting else (err[0] if err else f"exit {proc.returncode}")
    return False, detail[:160]


def main() -> int:
    if not MODEL.exists():
        print(f"Missing {MODEL}. Run ./scripts/fetch_model.sh first.")
        return 2

    print(f"python {sys.version.split()[0]}")
    try:
        import mediapipe as mp
        print(f"mediapipe {mp.__version__}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"mediapipe will not import: {exc}")
        return 2

    survivors: list[str] = []
    for label, spec in CASES:
        model = HAND_MODEL if spec.startswith("hand") else MODEL
        if not model.exists():
            print(f"  skip   {label}  -  {model.name} not downloaded")
            continue
        print(f"  ...    {label}", end="\r", flush=True)
        ok, detail = run(spec, model)
        if ok:
            print(f"  PASS   {label}          ")
            survivors.append(label)
        else:
            print(f"  crash  {label}  -  {detail}")

    print()
    if survivors:
        print("Working configurations:")
        for s in survivors:
            print(f"  - {s}")
        print()
        print("Set the matching values in config.yaml (vision.running_mode,")
        print("vision.delegate) and re-run scripts/range_test.py.")
        return 0

    print("Nothing survived. MediaPipe's native macOS build cannot initialise here.")
    print("Next things to try, in order:")
    print()
    print("  1. pip install 'mediapipe==0.10.35'")
    print("     Also a py3 wheel, so it installs on this interpreter with no venv")
    print("     change. Different build vintage; worth one attempt.")
    print()
    print("  2. pip install 'mediapipe==0.10.21'")
    print("     The last releases before 0.10.30 shipped NO arm64 wheel at all, so")
    print("     this pulls the x86_64 build and runs it under Rosetta. Slower, and")
    print("     it needs an x86_64 Python:")
    print("       arch -x86_64 /usr/bin/python3 -m venv .venv-intel")
    print("     Ugly, but it sidesteps the arm64 Metal path entirely.")
    print()
    print("  3. Go to the browser first. MediaPipe's WASM build has none of this")
    print("     native Metal machinery, and phase 2 was always going to reuse the")
    print("     same portable core. The laptop app stops being the critical path.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
