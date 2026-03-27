import { useEffect, useState } from "react";

import { api } from "../lib/api";

export function useDashboard(pollMs = 3000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;

    const load = async () => {
      try {
        const next = await api.dashboard();
        if (!alive) {
          return;
        }
        setData(next);
        setError("");
      } catch (err) {
        if (!alive) {
          return;
        }
        setError(err instanceof Error ? err.message : "Dashboard request failed");
      } finally {
        if (alive) {
          setLoading(false);
        }
      }
    };

    load();
    const timer = window.setInterval(load, pollMs);

    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [pollMs]);

  return { data, loading, error, refresh: async () => setData(await api.dashboard()) };
}
