import { useWorkflow } from "../hooks/useWorkflow";
import "./WorkflowStaleBanner.css";

/**
 * Single global banner for stale workflow steps. It reads the backend workflow
 * state and only appears when the active step has real upstream work to review.
 */
export function WorkflowStaleBanner({ creatorId, searchId, currentStepKey, onJumpTo, workflowState }) {
  const localWorkflow = useWorkflow(workflowState ? null : creatorId, { searchId });
  const stepsByKey = workflowState?.stepsByKey || localWorkflow.stepsByKey;
  const step = currentStepKey ? stepsByKey[currentStepKey] : null;
  if (!step || !step.stale) return null;
  if (currentStepKey === "approve") return null;

  let target = null;
  if (currentStepKey === "persona") target = "approve";

  return (
    <div className="wf-stale-banner" role="status">
      <span className="wf-stale-banner-dot" aria-hidden="true" />
      <span className="wf-stale-banner-text">
        {step.blocked_reason || "This step needs a fresh review before it can continue."}
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
