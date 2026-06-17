import { useEffect, useMemo, useRef } from "react";
import CreatorChatLogo from "../assets/creator-chat-logo.png";
import { useWorkflow } from "../hooks/useWorkflow";
import "./Stepper.css";
import "./WorkflowNav.css";

/**
 * Workflow-aware navigation. Replaces <Stepper> when a creator is selected.
 *
 * Rendering rules (FSM-driven):
 *   - status="complete":  complete state, fully clickable
 *   - status="active":    bold, accent ring (current focus)
 *   - status="available": clickable, neutral
 *   - status="locked":    lock icon, not clickable
 *   - stale=true:         dot; still clickable (forward action invalidates downstream)
 *
 * Counts/status adornments render only for Search and Approve.
 *
 * Props mirror <Stepper> so swap-in is mechanical:
 *   - currentStep:   numeric (1..5) used as a fallback highlight
 *   - steps:         the local STEPS array (for label + key fallback if backend silent)
 *   - onStepClick:   (stepNumber) => void
 *   - creatorId:     enables backend FSM polling
 *   - searchProgress: in-flight % for the Search step
 *   - onUserClick / userAvatarUrl: avatar bubble
 */
export function WorkflowNav({
  currentStep,
  steps,
  onStepClick,
  creatorId,
  searchId,
  searchProgress = 0,
  workflowState,
  onUserClick,
  userAvatarUrl,
}) {
  const activeStepRef = useRef(null);
  const localWorkflow = useWorkflow(workflowState ? null : creatorId, { searchId });
  const stepsByKey = workflowState?.stepsByKey || localWorkflow.stepsByKey;
  const serverStep = workflowState?.currentStep || localWorkflow.currentStep;

  // Merge local steps (label + ordering) with server FSM (status/count/blocked).
  const merged = useMemo(() => {
    return steps.map((s, idx) => {
      const remote = stepsByKey[s.key] || null;
      const fallbackReady = s.key === "search" ? false : true;
      return {
        index: idx + 1,
        key: s.key,
        label: s.label,
        status: remote?.status || (idx + 1 === currentStep ? "active" : "available"),
        ready: remote ? remote.ready : fallbackReady,
        stale: Boolean(remote?.stale),
        blocked_reason: remote?.blocked_reason || null,
        count: remote?.count || null,
        hidden: Boolean(remote?.hidden),
      };
    });
  }, [steps, stepsByKey, currentStep]);

  const visible = merged.filter((s) => !s.hidden);

  const activeKey = useMemo(() => {
    const visibleKeys = new Set(visible.map((s) => s.key));
    const localKey = steps[currentStep - 1]?.key;
    if (localKey && visibleKeys.has(localKey)) return localKey;

    if (serverStep && visibleKeys.has(serverStep)) return serverStep;

    const localIndex = steps.findIndex((step) => step.key === localKey);
    for (let idx = localIndex - 1; idx >= 0; idx -= 1) {
      const candidateKey = steps[idx]?.key;
      if (candidateKey && visibleKeys.has(candidateKey)) return candidateKey;
    }

    return visible[0]?.key || null;
  }, [visible, serverStep, steps, currentStep]);

  useEffect(() => {
    activeStepRef.current?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
      inline: "center",
    });
  }, [activeKey]);

  return (
    <div className="global-nav-stepper">
      <div className="app-branding">
        <img src={CreatorChatLogo} alt="Creator Chat" className="app-branding-mark" />
      </div>
      <div className="nav-steps-container">
        {visible.map((s) => {
          const isCurrent = s.key === activeKey;
          const isLocked = s.status === "locked" || !s.ready;
          const showStepSignal = s.key === "search" || s.key === "approve";
          const cls = [
            "nav-step",
            "wf-step",
            `wf-${s.status}`,
            isCurrent ? "active" : "",
            isLocked ? "disabled" : "",
            s.stale ? "wf-stale" : "",
          ].filter(Boolean).join(" ");

          return (
            <button
              key={s.key}
              ref={isCurrent ? activeStepRef : null}
              type="button"
              className={cls}
              disabled={isLocked}
              aria-label={`${s.label}${isLocked ? " (locked)" : ""}`}
              onClick={() => !isLocked && onStepClick && onStepClick(s.index)}
            >
              {showStepSignal && s.status === "complete" && (
                <span className="wf-icon" aria-hidden="true">&#10003;</span>
              )}
              {showStepSignal && isLocked && (
                <span className="wf-icon" aria-hidden="true">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="4" y="11" width="16" height="9" rx="2"></rect>
                    <path d="M8 11V7a4 4 0 1 1 8 0v4"></path>
                  </svg>
                </span>
              )}
              <span className="nav-step-label">{s.label}</span>

              {s.key === "search" && isCurrent && searchProgress > 0 && searchProgress < 100 && (
                <span className="step-progress-inline">{Math.round(searchProgress)}%</span>
              )}

              {showStepSignal && s.count && (
                <CountPill stepKey={s.key} count={s.count} active={isCurrent} />
              )}

              {s.stale && <span className="wf-stale-dot" aria-hidden="true" />}
            </button>
          );
        })}
      </div>
      <div className="nav-right-actions">
        <button className="user-profile-btn" onClick={onUserClick} aria-label="User Settings">
          {userAvatarUrl ? (
            <img src={userAvatarUrl} alt="User" className="user-avatar-small" />
          ) : (
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

function CountPill({ stepKey, count, active }) {
  // Render the most informative single number per step.
  let value = null;
  let tone = "neutral";
  if (stepKey === "approve") {
    if (count.pending > 0) {
      value = `${count.pending} to review`;
      tone = "attention";
    } else if (count.approved > 0) {
      value = `${count.approved} approved`;
      tone = "success";
    }
  } else if (stepKey === "search" && count.items) {
    value = `${count.items} found`;
  }
  if (value == null) return null;
  return (
    <span className={`wf-count-pill wf-count-${tone} ${active ? "active" : ""}`}>{value}</span>
  );
}
