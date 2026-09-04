// The ONLY place that knows the contract exists.
//
// If a screen imports fetch directly, the seam has leaked. Everything the rest
// of the app can do to the backend is a function in this file.

const WS = location.protocol === "https:" ? "wss" : "ws";
const WS_BASE = `${WS}://${location.host}/api`;

async function json(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep it */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

export const health = () => json("/health");
export const listDevices = () => json("/devices");
export const discover = () => json("/discover", { method: "POST" });
export const selectDevice = (id) => json(`/devices/${id}/select`, { method: "POST" });
export const refreshDevice = (id) => json(`/devices/${id}/refresh`, { method: "POST" });
export const addByHost = (host, name) =>
  json("/devices/by-host", { method: "POST", body: JSON.stringify({ host, name }) });
export const sendIntent = (intent) =>
  json("/intent", { method: "POST", body: JSON.stringify({ intent }) });
export const getConfig = () => json("/config");
export const putConfig = (config) =>
  json("/config", { method: "PUT", body: JSON.stringify(config) });
export const resetSession = () => json("/session/reset", { method: "POST" });

/**
 * Server → client events. Reconnects on its own, because a propped phone will
 * lose this socket every time the network hiccups and nobody is standing there
 * to press reload.
 */
export function openEvents(onEvent, onStatus = () => {}) {
  let socket = null;
  let closed = false;
  let backoff = 500;

  const connect = () => {
    if (closed) return;
    socket = new WebSocket(`${WS_BASE}/events`);
    socket.onopen = () => { backoff = 500; onStatus("connected"); };
    socket.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
    socket.onclose = () => {
      onStatus("reconnecting");
      if (!closed) setTimeout(connect, backoff = Math.min(backoff * 2, 8000));
    };
    socket.onerror = () => socket.close();
  };
  connect();
  return () => { closed = true; socket?.close(); };
}

/**
 * Client → server pose stream. Same reconnection reasoning.
 *
 * Frames are dropped rather than queued while the socket is down: a pose from
 * four seconds ago is not worth delivering, and a backlog would replay a stale
 * gesture the moment the link came back.
 */
export function openPoseStream(onStatus = () => {}) {
  let socket = null;
  let closed = false;
  let backoff = 500;

  const connect = () => {
    if (closed) return;
    socket = new WebSocket(`${WS_BASE}/pose`);
    socket.onopen = () => { backoff = 500; onStatus("connected"); };
    socket.onclose = () => {
      onStatus("reconnecting");
      if (!closed) setTimeout(connect, backoff = Math.min(backoff * 2, 8000));
    };
    socket.onerror = () => socket.close();
  };
  connect();

  return {
    send(pose, confidence, tMs) {
      if (socket?.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify({ pose, confidence, t_ms: tMs }));
      return true;
    },
    close() { closed = true; socket?.close(); },
  };
}
