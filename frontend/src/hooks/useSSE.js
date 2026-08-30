import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
const _callbacks = new Map();

/**
 * Subscribe to an SSE event key.
 * Returns an unsubscribe function — call it in useEffect cleanup.
 *
 * @param {string}   key       e.g. "job_update:abc123" or "batch_update"
 * @param {Function} callback  receives the parsed event payload (object)
 * @returns {Function} unsubscribe
 */
export function subscribeToSSE(key, callback) {
  if (!_callbacks.has(key)) _callbacks.set(key, new Set());
  _callbacks.get(key).add(callback);
  return () => _callbacks.get(key)?.delete(callback);
}

/** Dispatch to all registered callbacks for a given key. */
function _dispatch(key, data) {
  _callbacks.get(key)?.forEach((cb) => {
    try {
      cb(data);
    } catch (err) {
      console.error("[SSE] callback error", err);
    }
  });
}

// Hook 
export default function useSSE() {
  const queryClient = useQueryClient();
  const esRef       = useRef(null);
  const retryDelay  = useRef(1000);

  useEffect(() => {
    let destroyed = false;

    function connect() {
      const token = localStorage.getItem("access_token");
      if (!token || destroyed) return;

      const url = `/api/v1/sse/stream?token=${encodeURIComponent(token)}`;
      const es = new EventSource(url);
      esRef.current = es;

      // job_update 
      es.addEventListener("job_update", (e) => {
        const data = JSON.parse(e.data);
        const { job_id, ...rest } = data;

        // Patch the specific job cache entry in-place
        queryClient.setQueryData(["job", job_id], (old) =>
          old ? { ...old, ...rest, id: job_id } : null
        );
        // Invalidate list queries (dashboard, history page)
        queryClient.invalidateQueries({ queryKey: ["jobs"] });

        // Targeted subscriptions (e.g. PipelinesPage waiting for THIS job)
        _dispatch(`job_update:${job_id}`, data);
        // Broad subscriptions (e.g. widgets showing any job activity)
        _dispatch("job_update", data);
      });

      // agent_update 
      es.addEventListener("agent_update", (e) => {
        const data = JSON.parse(e.data);
        const { run_id, ...rest } = data;

        queryClient.setQueryData(["agent", run_id], (old) =>
          old ? { ...old, ...rest, id: run_id } : null
        );
        queryClient.invalidateQueries({ queryKey: ["agents"] });

        _dispatch(`agent_update:${run_id}`, data);
        _dispatch("agent_update", data);
      });

      // batch_update
      es.addEventListener("batch_update", (e) => {
        const data = JSON.parse(e.data);
        // Invalidate the batch list — BatchPage will refetch
        queryClient.invalidateQueries({ queryKey: ["batches"] });
        _dispatch("batch_update", data);
      });

      // action_update — agentic action layer (completed/failed/awaiting_approval)
      es.addEventListener("action_update", (e) => {
        const data = JSON.parse(e.data);
        queryClient.invalidateQueries({ queryKey: ["action-runs"] });
        _dispatch("action_update", data);
      });

      // Any terminal event carrying an embedded `notification` payload also
      // feeds the global notification center (bell dropdown + dashboard panel).
      ["job_update", "agent_update", "action_update"].forEach((type) => {
        es.addEventListener(type, (e) => {
          const data = JSON.parse(e.data);
          if (data.notification) {
            queryClient.invalidateQueries({ queryKey: ["notifications"] });
            _dispatch("notification", data.notification);
          }
        });
      });

      // heartbeat — just reset the retry delay 
      es.addEventListener("heartbeat", () => {
        retryDelay.current = 1000;
      });

      // Reconnect on error 
      es.onerror = () => {
        es.close();
        esRef.current = null;
        if (destroyed) return;
        const delay = Math.min(retryDelay.current, 30_000);
        retryDelay.current = Math.min(delay * 2, 30_000);
        setTimeout(connect, delay);
      };

      es.onopen = () => {
        retryDelay.current = 1000; // reset on successful connect
      };
    }

    connect();

    return () => {
      destroyed = true;
      esRef.current?.close();
      esRef.current = null;
    };
  }, []); // runs once — token is read inside connect() on every (re)connect
}

export function waitForJobSSE(jobId, api, timeoutMs = 5 * 60_000) {
  return new Promise((resolve) => {
    let settled = false;

    const unsub = subscribeToSSE(`job_update:${jobId}`, (data) => {
      if (settled) return;
      if (data.status === "completed" || data.status === "failed") {
        settled = true;
        unsub();
        clearTimeout(timer);
        resolve(data);
      }
    });

    // Safety net: if SSE misses the event, fall back to a single REST poll
    const timer = setTimeout(async () => {
      if (settled) return;
      settled = true;
      unsub();
      try {
        const { data } = await api.get(`/jobs/${jobId}`);
        resolve(data);
      } catch {
        resolve({ id: jobId, status: "unknown" });
      }
    }, timeoutMs);
  });
}

export function waitForAgentSSE(runId, api, timeoutMs = 10 * 60_000) {
  return new Promise((resolve) => {
    let settled = false;

    const unsub = subscribeToSSE(`agent_update:${runId}`, (data) => {
      if (settled) return;
      if (data.status === "completed" || data.status === "failed") {
        settled = true;
        unsub();
        clearTimeout(timer);
        resolve(data);
      }
    });

    const timer = setTimeout(async () => {
      if (settled) return;
      settled = true;
      unsub();
      try {
        const { data } = await api.get(`/agents/${runId}`);
        resolve(data);
      } catch {
        resolve({ id: runId, status: "unknown" });
      }
    }, timeoutMs);
  });
}