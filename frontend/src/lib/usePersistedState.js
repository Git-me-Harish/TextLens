/**
 * usePersistedState — useState that survives a reload, a navigation, and a
 * dropped connection.
 *
 * Why this exists
 * ───────────────
 * Every multi-step flow in this app (upload → pick a pipeline → write
 * instructions → run) held its state in plain useState. That state lives only
 * as long as the component is mounted, so a reload — or a router navigation,
 * or a crash recovery after the network dropped — silently threw away work the
 * user had already done: the file they picked, the domain and pipeline they
 * chose, the instructions they typed. There was no failure message because
 * nothing failed; the state simply ceased to exist.
 *
 * How it restores, and why synchronously
 * ──────────────────────────────────────
 * Restoration happens in the useState initializer, on the very first render,
 * NOT in a useEffect. That matters for three reasons:
 *   - No flash. An effect-based restore paints one frame of empty state first,
 *     so the user sees their selections blink away and come back.
 *   - No race. An async restore can be overwritten by whatever else the page
 *     does on mount, and the ordering is genuinely hard to reason about.
 *   - StrictMode-proof. React 18 StrictMode deliberately double-invokes mount
 *     effects in development; a lazy initializer is immune to that entirely.
 *
 * What belongs here, and what doesn't
 * ───────────────────────────────────
 * Persist the user's *intent* — selections, typed text, which step they're on,
 * ids of work they started. Do NOT persist server-owned payloads (extracted
 * text, agent results, chat transcripts): they can be megabytes, they go stale
 * the moment the server moves on, and storage quota is ~5MB. Persist the id
 * and re-fetch the body, so what comes back reflects reality rather than a
 * snapshot. See PipelinesPage for that pattern.
 *
 * sessionStorage by default: "resume where I left off" should mean this tab's
 * current work, not something resurrected days later in an unrelated session.
 * Pass { storage: "local" } for the rare thing that should outlive the tab.
 *
 * Every storage access is wrapped: quota limits, Safari private mode, and
 * browsers configured to block site data all throw on read or write. Losing
 * persistence is acceptable; breaking the page over it is not.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// Versioned so a future shape change can't be handed stale data it can't parse.
const NAMESPACE = "textlens:v1:";

function getStore(storage) {
  try {
    return storage === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

function readRaw(store, key) {
  if (!store) return undefined;
  try {
    const raw = store.getItem(NAMESPACE + key);
    return raw == null ? undefined : JSON.parse(raw);
  } catch {
    // Corrupt or unreadable — fall back to the caller's initial value rather
    // than propagating a parse error into render.
    return undefined;
  }
}

function writeRaw(store, key, value) {
  if (!store) return;
  try {
    if (value === undefined) store.removeItem(NAMESPACE + key);
    else store.setItem(NAMESPACE + key, JSON.stringify(value));
  } catch {
    // Quota exceeded or writes blocked — persistence is a convenience.
  }
}

/**
 * @param {string} key    Stable, page-scoped, e.g. "pipelines:instructions".
 * @param {*}      initial Value used when nothing is stored yet.
 * @param {{storage?: "session"|"local"}} [opts]
 * @returns {[*, Function]} Same tuple shape as useState.
 */
export function usePersistedState(key, initial, opts = {}) {
  const storage = opts.storage || "session";
  const store = getStore(storage);

  const [value, setValue] = useState(() => {
    const saved = readRaw(store, key);
    return saved === undefined ? initial : saved;
  });

  // Keyed by `key` so a component that changes its key mid-life writes to the
  // right slot rather than orphaning the old one.
  useEffect(() => {
    writeRaw(store, key, value);
  }, [store, key, value]);

  return [value, setValue];
}

/**
 * Remove every persisted key under a page prefix — the "Start over" path.
 * Call this when the user explicitly abandons a flow, so a stale step doesn't
 * outlive the work it described.
 */
export function clearPersisted(prefix, storage = "session") {
  const store = getStore(storage);
  if (!store) return;
  try {
    const doomed = Object.keys(store).filter((k) =>
      k.startsWith(NAMESPACE + prefix)
    );
    doomed.forEach((k) => store.removeItem(k));
  } catch {
    /* see writeRaw */
  }
}

/**
 * Rehydrate a server-owned record from a persisted id.
 *
 * The id is cheap and safe to persist; the record itself is not. This fetches
 * it once on mount and hands back { data, loading }.
 *
 * Critically, a failed fetch does NOT clear the id. A 401 mid-token-refresh,
 * an offline moment, or a 5xx would otherwise destroy the user's in-flight
 * work on exactly the transient blip this whole mechanism exists to survive.
 * Only an explicit 404 — the record is genuinely gone — clears it, via
 * onMissing.
 */
export function useHydratedRecord(id, fetcher, { onMissing } = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(Boolean(id));
  const onMissingRef = useRef(onMissing);
  onMissingRef.current = onMissing;

  useEffect(() => {
    if (!id) {
      setData(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    fetcher(id)
      .then((record) => {
        if (!cancelled) setData(record);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err?.response?.status === 404) onMissingRef.current?.();
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [id, fetcher]);

  const patch = useCallback((partial) => {
    setData((prev) => (prev ? { ...prev, ...partial } : prev));
  }, []);

  return { data, setData, patch, loading };
}
