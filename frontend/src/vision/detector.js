// Camera and MediaPipe. The only file that knows either exists.
//
// Pinned to 0.10.35 to match the Python side, so the two implementations return
// the same pose names and behaviour cannot drift between them.

const VERSION = "0.10.35";
const CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}`;

// Wrist, then the four fingers, then the palm edge. Same topology the Python
// HUD draws, so the two previews look like the same product.
const SKELETON = [
  [0,1],[1,2],[2,3],[3,4],
  [0,5],[5,6],[6,7],[7,8],
  [5,9],[9,10],[10,11],[11,12],
  [9,13],[13,14],[14,15],[15,16],
  [13,17],[17,18],[18,19],[19,20],
  [0,17],
];

export async function createDetector({ video, canvas, onFrame, facing = "user" }) {
  const { FilesetResolver, GestureRecognizer } = await import(
    `${CDN}/vision_bundle.mjs`
  );

  const fileset = await FilesetResolver.forVisionTasks(`${CDN}/wasm`);
  const recognizer = await GestureRecognizer.createFromOptions(fileset, {
    baseOptions: {
      // served by our own backend, so this works with no internet
      modelAssetPath: "/models/gesture_recognizer.task",
      delegate: "GPU",
    },
    runningMode: "VIDEO",
    numHands: 1,
  });

  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: facing,
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
    audio: false,
  });
  video.srcObject = stream;
  await video.play();

  const ctx = canvas.getContext("2d");
  let running = true;
  let lastTs = -1;

  const draw = (landmarks) => {
    if (canvas.width !== video.videoWidth) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!landmarks) return;
    const pts = landmarks.map((p) => [p.x * canvas.width, p.y * canvas.height]);
    ctx.lineWidth = Math.max(2, canvas.width / 320);
    ctx.strokeStyle = "rgba(120,200,160,.85)";
    ctx.beginPath();
    for (const [a, b] of SKELETON) {
      ctx.moveTo(pts[a][0], pts[a][1]);
      ctx.lineTo(pts[b][0], pts[b][1]);
    }
    ctx.stroke();
    ctx.fillStyle = "#eef6f1";
    const r = Math.max(3, canvas.width / 260);
    for (const [x, y] of pts) {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  const tick = () => {
    if (!running) return;
    const ts = performance.now();
    // MediaPipe rejects a timestamp that does not advance, which happens
    // whenever the browser hands us the same frame twice.
    if (ts > lastTs && video.readyState >= 2) {
      lastTs = ts;
      let result = null;
      try {
        result = recognizer.recognizeForVideo(video, ts);
      } catch {
        result = null;
      }
      if (result) {
        const landmarks = result.landmarks?.[0] ?? null;
        const top = result.gestures?.[0]?.[0];
        const pose = top && top.categoryName !== "None" ? top.categoryName : null;
        draw(landmarks);
        onFrame({
          pose,
          confidence: pose ? top.score : 0,
          tMs: ts,
          hand: Boolean(landmarks),
        });
      }
    }
    if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(tick);
    else requestAnimationFrame(tick);
  };
  tick();

  return {
    stop() {
      running = false;
      recognizer.close();
      stream.getTracks().forEach((t) => t.stop());
    },
  };
}

/**
 * A propped phone that dims after thirty seconds stops being a sensor. The lock
 * is dropped by the browser whenever the page is hidden, so it is re-taken on
 * every return to visibility rather than acquired once at startup.
 */
export function keepScreenAwake() {
  if (!("wakeLock" in navigator)) return () => {};
  let lock = null;
  const acquire = async () => {
    try { lock = await navigator.wakeLock.request("screen"); } catch { /* denied */ }
  };
  const onVisible = () => { if (document.visibilityState === "visible") acquire(); };
  acquire();
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    document.removeEventListener("visibilitychange", onVisible);
    lock?.release();
  };
}
