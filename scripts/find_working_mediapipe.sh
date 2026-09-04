#!/usr/bin/env bash
# Install each candidate MediaPipe and probe it, stopping at the first that works.
#
# The macOS arm64 wheels of some versions are built with GPU calculators that
# require kGpuService, which the Tasks API never installs - every task then dies
# at Open() with "Check failed: service_ Service is unavailable". It is a build
# problem in the wheel, so the only lever is which wheel.
#
# Candidates are the versions that actually ship an arm64 wheel: 0.10.30+.
# Anything older is x86_64 only and would need Rosetta.
set -uo pipefail
cd "$(dirname "$0")/.."

VERSIONS="${*:-0.10.35 0.10.33 0.10.32 0.10.31 0.10.30}"

echo "Trying: $VERSIONS"
echo

for v in $VERSIONS; do
  echo "=================================================="
  echo "  mediapipe==$v"
  echo "=================================================="
  if ! pip install -q "mediapipe==$v" 2>&1 | tail -3; then
    echo "  install failed, skipping"
    echo
    continue
  fi
  installed=$(python3 -c "import mediapipe; print(mediapipe.__version__)" 2>/dev/null || echo "import failed")
  echo "  installed: $installed"
  if [ "$installed" = "import failed" ]; then
    echo "  cannot import, skipping"
    echo
    continue
  fi

  if python3 scripts/probe_mediapipe.py; then
    echo
    echo "=================================================="
    echo "  WORKING: mediapipe==$v"
    echo "=================================================="
    echo "Pin it so a reinstall doesn't undo this:"
    echo "  sed -i '' 's/\"mediapipe>=1.0\"/\"mediapipe==$v\"/' pyproject.toml"
    echo
    echo "Then: python3 scripts/range_test.py"
    exit 0
  fi
  echo
done

echo "=================================================="
echo "  No native build works on this machine."
echo "=================================================="
echo "Stop here rather than burning more time on it. The browser front-end"
echo "(phase 2) uses MediaPipe's WASM build, which has none of this native"
echo "GPU-service machinery, and reuses the same portable core unchanged."
exit 1
