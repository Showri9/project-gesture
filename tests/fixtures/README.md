# Landmark fixtures

Recorded landmark sequences, stored as flat `[x, y, z, x, y, z, ...]` JSON —
21 points per frame.

They exist so the portable core (`classify`, `motion`, `session`, `intents`) has
a test suite that needs no camera and runs in CI. The real payoff comes in phases
2 and 3: **these same files become the test suite for the TypeScript and Swift
ports**. Identical inputs, identical expected gestures, three languages.

Record new ones with:

    python3 scripts/record_fixture.py my_gesture
