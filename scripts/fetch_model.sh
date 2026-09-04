#!/usr/bin/env bash
# Download the MediaPipe gesture recognizer bundle (~8MB).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
URL="https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"
echo "Downloading gesture_recognizer.task ..."
curl -fL --progress-bar -o models/gesture_recognizer.task "$URL"
echo "Saved to models/gesture_recognizer.task"
