import { useWorkflow } from "../hooks/useWorkflow";
import "./WorkflowStaleBanner.css";

/**
 * Single global banner that surfaces "this step is stale because something upstream changed"
 * for the active step. Reads the workflow FSM and renders nothing when nothing is stale.
 *
 *   - onJumpTo(stepKey): optional. If provided, the banner offers a "Fix it" link
 *     that jumps the user to the upstream step responsible for the staleness.
 */
export function WorkflowStaleBanner({ creatorId, currentStepKey, onJumpTo }) {
  const { stepsByKey } = useWorkflow(creatorId);
  const step = currentStepKey ? stepsByKey[currentStepKey] : null;
  if (!step || !step.stale) return null;

  // Decide where to send the user to fix it.
  let target = null;
  if (currentStepKey === "persona") target = "approve";
  else if (currentStepKey === "approve") target = "scrape";

  return (
    <div className="wf-stale-banner" role="status">
      <span className="wf-stale-banner-dot" aria-hidden="true" />
      <span className="wf-stale-banner-text">
        {step.blocked_reason || "Upstream sources changed — re-run to refresh this step."}
      </span>
      {target && onJumpTo && (
        <button
          type="button"
          className="wf-stale-banner-action"
          onClick={() => onJumpTo(target)}
        >
          Fix it
        </button>
      )}
    </div>
  );
}
