const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

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
};

export function formatGHz(value) {
  return `${(Number(value) / 1e9).toFixed(6)} GHz`;
}

export function shortReadout(key) {
  return String(key || "r_v").replace("_v", "").toUpperCase();
}
