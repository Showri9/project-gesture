#!/usr/bin/env bash
# Download the MediaPipe gesture recognizer bundle (~8MB).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
BASE="https://storage.googleapis.com/mediapipe-models"

get() {  # name url
  if [ -s "models/$1" ]; then
    echo "models/$1 already present, skipping"
    return
  fi
  echo "Downloading $1 ..."
  curl -fL --progress-bar -o "models/$1" "$2"
}

get gesture_recognizer.task \
  "$BASE/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"

# Landmarks only, no gesture classifier head. Used by scripts/probe_mediapipe.py
# to tell "the whole pipeline is broken" apart from "the classifier is broken",
# and it is the model to fall back to if custom gestures replace the built-ins.
get hand_landmarker.task \
  "$BASE/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"

ls -lh models/
