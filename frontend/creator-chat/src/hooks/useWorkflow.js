import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCreatorWorkflow } from "../api/client";

/**
 * Single source of truth for the 5-step workflow FSM.
 *
 * Reads /creators/:id/workflow and exposes:
 *   - state:           the raw response { current_step, ready_to_chat, steps[] }
 *   - currentStep:     the step the user should be on right now
 *   - stepsByKey:      { setup, search, approve, persona, chat } for O(1) lookup
 *   - canNavigateTo:   (key) => bool
 *   - blockedReason:   (key) => string|null
 *   - refresh:         () => Promise<void>   manual refetch
 *   - loading / error
 *
 * Polls every `pollIntervalMs` (default 3.5s) so step counts/locks stay live.
 * Pass `creatorId = null` to disable.
 */
export function useWorkflow(creatorId, { pollIntervalMs = 3500, searchId = null } = {}) {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const inflightRef = useRef(false);

  const fetchOnce = useCallback(async () => {
    if (!creatorId || inflightRef.current) return;
    inflightRef.current = true;
    setLoading(true);
    try {
      const data = await getCreatorWorkflow(creatorId, { searchId });
      setState(data);
      setError(null);
    } catch (e) {
      setError(e?.message || "Failed to load workflow");
    } finally {
      inflightRef.current = false;
      setLoading(false);
    }
  }, [creatorId, searchId]);

  useEffect(() => {
    if (!creatorId) {
      setState(null);
      return undefined;
    }
    fetchOnce();
    if (!pollIntervalMs) return undefined;
    const id = setInterval(fetchOnce, pollIntervalMs);
    return () => clearInterval(id);
  }, [creatorId, pollIntervalMs, fetchOnce]);

  const stepsByKey = useMemo(() => {
    const out = {};
    for (const s of state?.steps || []) out[s.key] = s;
    return out;
  }, [state]);

  const canNavigateTo = useCallback(
    (key) => Boolean(stepsByKey[key]?.ready),
    [stepsByKey]
  );

  const blockedReason = useCallback(
    (key) => stepsByKey[key]?.blocked_reason || null,
    [stepsByKey]
  );

  return {
    state,
    currentStep: state?.current_step || null,
    readyToChat: Boolean(state?.ready_to_chat),
    steps: state?.steps || [],
    stepsByKey,
    canNavigateTo,
    blockedReason,
    refresh: fetchOnce,
    loading,
    error,
  };
}
