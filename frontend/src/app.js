// Wiring. Screens read from the API module and render; nothing here knows what
// a Roku is, and nothing here talks to the network except through api/client.

import * as api from "./api/client.js";
import { createDetector, keepScreenAwake } from "./vision/detector.js";

const $ = (id) => document.getElementById(id);
const RING_CIRCUMFERENCE = 276.5;

const state = {
  detector: null,
  poses: null,
  releaseWakeLock: null,
  devices: [],
  config: null,
};

// -- screens -----------------------------------------------------------------

document.querySelectorAll("nav button").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b === button));
    for (const name of ["sensor", "devices", "settings"]) {
      $(name).hidden = name !== button.dataset.screen;
    }
    if (button.dataset.screen === "devices") loadDevices();
    if (button.dataset.screen === "settings") loadConfig();
  };
});

// -- events from the server --------------------------------------------------

api.openEvents(onEvent, (status) => {
  const link = $("link");
  link.dataset.state = status;
  link.textContent = status === "connected" ? "live" : "reconnecting";
});

function onEvent(event) {
  switch (event.type) {
    case "session_state": {
      if (state.detector) {
        $("badge").dataset.state = event.state;
        $("badge").textContent = event.state;
      }
      $("ring-fill").style.strokeDashoffset =
        RING_CIRCUMFERENCE * (1 - (event.progress ?? 0));
      $("pose").textContent = event.pose
        ? `${event.pose}  ${(event.confidence ?? 0).toFixed(2)}`
        : "—";
      break;
    }
    case "intent":
      addLog(event);
      break;
    case "device_status":
    case "device_selected":
      if (!$("devices").hidden) loadDevices();
      break;
    case "discovery":
      $("scanning").hidden = !event.scanning;
      if (!event.scanning) loadDevices();
      break;
  }
}

function addLog({ intent, result, detail, repeat }) {
  if (repeat) return;                       // a volume ramp is one action, not thirty
  const list = $("log");
  list.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  const when = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  li.innerHTML = `<span class="${result}">${intent}</span><time>${when}</time>`;
  li.title = detail ?? "";
  list.prepend(li);
  while (list.children.length > 12) list.lastElementChild.remove();
}

// -- the camera --------------------------------------------------------------

$("start").onclick = async () => {
  $("hint").textContent = "";
  if (!navigator.mediaDevices?.getUserMedia) {
    $("badge").dataset.state = "ERROR";
    $("badge").textContent = "NO CAMERA";
    $("hint").textContent = location.protocol === "https:"
      ? "This browser will not expose a camera here."
      : "The camera needs HTTPS. Open this page over https://, or use localhost.";
    return;
  }
  $("start").disabled = true;
  $("badge").textContent = "STARTING";
  try {
    state.poses = api.openPoseStream();
    state.detector = await createDetector({
      video: $("video"),
      canvas: $("overlay"),
      onFrame: ({ pose, confidence, tMs }) => state.poses.send(pose, confidence, tMs),
    });
    state.releaseWakeLock = keepScreenAwake();
    $("start").hidden = true;
    $("stop").hidden = false;
    $("badge").dataset.state = "IDLE";
    $("badge").textContent = "IDLE";
  } catch (error) {
    $("badge").dataset.state = "ERROR";
    $("badge").textContent = "ERROR";
    $("hint").textContent = describeCameraError(error);
    state.poses?.close();
    state.poses = null;
  } finally {
    $("start").disabled = false;
  }
};

$("stop").onclick = () => {
  state.detector?.stop();
  state.poses?.close();
  state.releaseWakeLock?.();
  state.detector = state.poses = state.releaseWakeLock = null;
  $("start").hidden = false;
  $("stop").hidden = true;
  $("badge").dataset.state = "OFF";
  $("badge").textContent = "CAMERA OFF";
};

function describeCameraError(error) {
  const name = error?.name ?? "";
  if (name === "NotAllowedError")
    return "Camera permission denied. Allow it in the browser's site settings and try again.";
  if (name === "NotFoundError") return "No camera on this device.";
  if (name === "NotReadableError")
    return "The camera is busy — another app or tab probably has it.";
  if (location.protocol !== "https:" && location.hostname !== "localhost")
    return "The camera needs HTTPS. Serve with --cert/--key, or use localhost.";
  return error?.message ?? String(error);
}

// -- the remote --------------------------------------------------------------

document.querySelectorAll(".remote button").forEach((button) => {
  button.onclick = async () => {
    button.disabled = true;
    try { await api.sendIntent(button.dataset.intent); }
    catch (error) { $("hint").textContent = String(error.message ?? error); }
    finally { button.disabled = false; }
  };
});

// -- devices -----------------------------------------------------------------

$("scan").onclick = async () => {
  $("scan").disabled = true;
  $("scanning").hidden = false;
  try { await api.discover(); }
  finally { $("scan").disabled = false; $("scanning").hidden = true; loadDevices(); }
};

$("by-host").onsubmit = async (e) => {
  e.preventDefault();
  const input = e.target.host;
  if (!input.value.trim()) return;
  try { await api.addByHost(input.value.trim(), null, e.target.kind.value); input.value = ""; }
  catch (error) { alert(error.message); }
  loadDevices();
};

async function loadDevices() {
  state.devices = await api.listDevices().catch(() => []);
  const list = $("device-list");
  list.innerHTML = "";
  if (!state.devices.length) {
    list.innerHTML = '<li class="empty">No devices yet — scan, or add by IP.</li>';
    return;
  }
  for (const device of state.devices) {
    const li = document.createElement("li");
    li.className = device.selected ? "on" : "";
    const shape = device.is_tv ? "TV" : "stick — no volume or power keys";
    const tag = device.kind === "googletv" ? "Google TV" : "Roku";
    li.innerHTML = `
      <span>
        <span class="name">${device.name}</span><span class="tag">${tag}</span>
        ${device.needs_pairing ? '<span class="tag pair">not paired</span>' : ""}
        <span class="meta">${device.model} · ${device.host} · ${shape}</span>
      </span>
      <span class="dot ${device.reachable ? "up" : ""}" title="${device.reachable ? "reachable" : "unreachable"}"></span>`;

    li.onclick = async (e) => {
      if (e.target.closest(".pairing")) return;      // don't select while pairing
      await api.selectDevice(device.id);
      loadDevices();
    };
    if (device.needs_pairing) li.append(pairingForm(device));
    list.append(li);
  }
}

/**
 * The pairing step, which only Google TV needs. The code appears on the
 * television, not here — so the button that asks for it and the field that
 * takes it have to be two separate moments, and the copy has to say where to
 * look or people stare at the phone waiting for a code.
 */
function pairingForm(device) {
  const wrap = document.createElement("form");
  wrap.className = "pairing";
  wrap.innerHTML = `
    <input name="code" inputmode="numeric" autocomplete="off"
           placeholder="code from TV" maxlength="8" disabled>
    <button type="button" class="primary">Pair</button>`;
  const input = wrap.querySelector("input");
  const button = wrap.querySelector("button");
  const note = document.createElement("span");
  note.className = "pair-note";

  const fail = (error) => {
    note.textContent = String(error.message ?? error);
    wrap.append(note);
  };

  button.onclick = async () => {
    if (button.textContent === "Pair") {
      button.disabled = true;
      try {
        await api.pairStart(device.id);
        input.disabled = false;
        input.focus();
        button.textContent = "Confirm";
        note.textContent = "Look at the TV — type the six digits it is showing.";
        wrap.append(note);
      } catch (error) { fail(error); }
      finally { button.disabled = false; }
      return;
    }
    if (!input.value.trim()) return;
    button.disabled = true;
    try {
      await api.pairFinish(device.id, input.value.trim());
      loadDevices();
    } catch (error) {
      fail(error);
      input.value = "";
      input.focus();
    } finally { button.disabled = false; }
  };

  wrap.onsubmit = (e) => { e.preventDefault(); button.click(); };
  return wrap;
}

// -- settings ----------------------------------------------------------------

const THRESHOLD_RANGES = {
  wake_hold_ms: [200, 3000, 50],
  confirm_frames: [1, 30, 1],
  cooldown_ms: [100, 3000, 50],
  repeat_ms: [30, 500, 10],
  idle_timeout_ms: [3000, 60000, 1000],
  min_confidence: [0.3, 0.95, 0.05],
  sleep_confirm_frames: [1, 20, 1],
  wake_grace_ms: [0, 5000, 100],
  power_confirm_frames: [1, 60, 1],
};

async function loadConfig() {
  state.config = await api.getConfig().catch(() => null);
  if (!state.config) return;

  const list = $("bindings");
  list.innerHTML = "";
  for (const binding of state.config.bindings) {
    const li = document.createElement("li");
    const hold = binding.hold_ms ? `${(binding.hold_ms / 1000).toFixed(1)}s hold` : "default";
    li.innerHTML = `<span class="pose-name">${binding.pose}</span>
                    <span>${binding.intent} <span class="hold">· ${hold}</span></span>`;
    list.append(li);
  }

  const box = $("thresholds");
  box.innerHTML = "";
  for (const [key, value] of Object.entries(state.config.thresholds)) {
    const [min, max, step] = THRESHOLD_RANGES[key] ?? [0, 1000, 1];
    const label = document.createElement("label");
    label.innerHTML = `<span class="k"><span>${key}</span><b data-for="${key}">${value}</b></span>`;
    const slider = Object.assign(document.createElement("input"), {
      type: "range", min, max, step, value,
    });
    slider.oninput = () => { label.querySelector("b").textContent = slider.value; };
    slider.onchange = async () => {
      const thresholds = { ...state.config.thresholds, [key]: Number(slider.value) };
      state.config = await api.putConfig({ thresholds });
    };
    label.append(slider);
    box.append(label);
  }
}

// -- start -------------------------------------------------------------------

loadDevices();
api.health().then((h) => { document.title = h.device ? `gesturectl — ${h.device}` : "gesturectl"; })
  .catch(() => {});
