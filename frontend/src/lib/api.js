const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
const WS_BASE = import.meta.env.VITE_WS_BASE ?? "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json();
}

async function downloadRequest(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  return {
    blob: await response.blob(),
    filename: encodedName
      ? decodeURIComponent(encodedName)
      : plainName || "current_tracking.xlsx",
  };
}

export const api = {
  dashboard: () => request("/instruments/dashboard"),
  discoverLockins: (params) => {
    const query = new URLSearchParams({
      server_host: params.server_host,
      server_port: String(params.server_port),
      hf2: String(params.hf2),
    });
    return request(`/instruments/lockin/discover?${query.toString()}`);
  },
  connectLockin: (payload) =>
    request("/instruments/lockin/connect", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  disconnectLockin: () =>
    request("/instruments/lockin/disconnect", {
      method: "POST",
    }),
  saveLockinChannel: (payload) =>
    request("/instruments/lockin/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  discoverMicrowaves: () => request("/instruments/microwave/discover"),
  connectMicrowave: (payload) =>
    request("/instruments/microwave/connect", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  disconnectMicrowave: () =>
    request("/instruments/microwave/disconnect", {
      method: "POST",
    }),
  saveMicrowave: (payload) =>
    request("/instruments/microwave/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runOdmr: (payload) =>
    request("/measurement/odmr", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  stopOdmr: () =>
    request("/measurement/odmr/stop", {
      method: "POST",
    }),
  stopSensitivity: () =>
    request("/measurement/sensitivity/stop", {
      method: "POST",
    }),
  stopCurrent: () =>
    request("/measurement/current/stop", {
      method: "POST",
    }),
  stopStateEstimationCurrent: () =>
    request("/state-estimation-current/stop", {
      method: "POST",
    }),
  currentTrackingRecordingStatus: (sessionId = "") => {
    const query = sessionId
      ? `?session_id=${encodeURIComponent(sessionId)}`
      : "";
    return request(`/measurement/current/tracking/recording/status${query}`);
  },
  downloadCurrentTrackingRecording: (sessionId = "") => {
    const query = sessionId
      ? `?session_id=${encodeURIComponent(sessionId)}`
      : "";
    return downloadRequest(
      `/measurement/current/tracking/recording/download${query}`
    );
  },
};

export function wsUrl(path) {
  if (WS_BASE) {
    return `${WS_BASE}${path}`;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const isLocalDevHost = ["127.0.0.1", "localhost"].includes(window.location.hostname);
  const usesRelativeApiBase = API_BASE.startsWith("/");
  if (isLocalDevHost && usesRelativeApiBase && window.location.port && window.location.port !== "8000") {
    return `${protocol}//${window.location.hostname}:8000${API_BASE}${path}`.replace(/([^:]\/)\/+/g, "$1");
  }
  const apiUrl = new URL(API_BASE, window.location.origin);
  return `${protocol}//${apiUrl.host}${apiUrl.pathname}${path}`.replace(/([^:]\/)\/+/g, "$1");
}

export function formatGHz(value) {
  return `${(Number(value) / 1e9).toFixed(6)} GHz`;
}

export function shortReadout(key) {
  return String(key || "r_v").replace("_v", "").toUpperCase();
}
