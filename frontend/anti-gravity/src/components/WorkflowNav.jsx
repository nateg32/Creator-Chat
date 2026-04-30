import { useMemo } from "react";
import creatorBotMark from "../assets/creator-bot-mark.svg";
import { useWorkflow } from "../hooks/useWorkflow";
import "./Stepper.css";
import "./WorkflowNav.css";

/**
 * Workflow-aware navigation. Replaces <Stepper> when a creator is selected.
 *
 * Rendering rules (FSM-driven):
 *   - status="complete":  check icon, dimmed, fully clickable
 *   - status="active":    bold, accent ring (current focus)
 *   - status="available": clickable, neutral
 *   - status="locked":    lock icon, not clickable, tooltip = blocked_reason
 *   - stale=true:         dot + "Stale" tooltip; still clickable (forward action invalidates downstream)
 *
 * Counts render as small pills (e.g. "5 pending", "12 docs").
 *
 * Props mirror <Stepper> so swap-in is mechanical:
 *   - currentStep:   numeric (1..5) used as a fallback highlight
 *   - steps:         the local STEPS array (for label + key fallback if backend silent)
 *   - onStepClick:   (stepNumber) => void
 *   - creatorId:     enables backend FSM polling
 *   - searchProgress: in-flight % for the Search step
 *   - onUserClick / userAvatarUrl: avatar bubble (unchanged)
 */
export function WorkflowNav({
  currentStep,
  steps,
  onStepClick,
  creatorId,
  searchProgress = 0,
  onUserClick,
  userAvatarUrl,
}) {
  const { stepsByKey, currentStep: serverStep } = useWorkflow(creatorId);

  // Merge local steps (label + ordering) with server FSM (status/count/blocked).
  const merged = useMemo(() => {
    return steps.map((s, idx) => {
      const remote = stepsByKey[s.key] || null;
      return {
        index: idx + 1,
        key: s.key,
        label: s.label,
        status: remote?.status || (idx + 1 === currentStep ? "active" : "available"),
        ready: remote ? remote.ready : true,
        stale: Boolean(remote?.stale),
        blocked_reason: remote?.blocked_reason || null,
        count: remote?.count || null,
      };
    });
  }, [steps, stepsByKey, currentStep]);

  const activeKey = serverStep || steps[currentStep - 1]?.key;

  return (
    <div className="global-nav-stepper">
      <div className="app-branding">
        <img src={creatorBotMark} alt="Creator Bot" className="app-branding-mark" />
      </div>
      <div className="nav-steps-container">
        {merged.map((s) => {
          const isCurrent = s.key === activeKey;
          const isLocked = s.status === "locked" || !s.ready;
          const cls = [
            "nav-step",
            "wf-step",
            `wf-${s.status}`,
            isCurrent ? "active" : "",
            isLocked ? "disabled" : "",
            s.stale ? "wf-stale" : "",
          ].filter(Boolean).join(" ");

          const tooltip = isLocked
            ? (s.blocked_reason || "Complete previous steps first")
            : (s.stale ? "Upstream changed — re-run to refresh" : s.label);

          return (
            <button
              key={s.key}
              type="button"
              className={cls}
              disabled={isLocked}
              title={tooltip}
              aria-label={`${s.label}${isLocked ? " (locked)" : ""}`}
              onClick={() => !isLocked && onStepClick && onStepClick(s.index)}
            >
              {s.status === "complete" && (
                <span className="wf-icon" aria-hidden="true">✓</span>
              )}
              {isLocked && (
                <span className="wf-icon" aria-hidden="true">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="4" y="11" width="16" height="9" rx="2"></rect>
                    <path d="M8 11V7a4 4 0 1 1 8 0v4"></path>
                  </svg>
                </span>
              )}
              <span className="nav-step-label">{s.label}</span>

              {s.key === "scrape" && isCurrent && searchProgress > 0 && searchProgress < 100 && (
                <span className="step-progress-inline">{Math.round(searchProgress)}%</span>
              )}

              {s.count && (
                <CountPill stepKey={s.key} count={s.count} active={isCurrent} />
              )}

              {s.stale && <span className="wf-stale-dot" aria-hidden="true" />}
            </button>
          );
        })}
      </div>
      <div className="nav-right-actions">
        <button className="user-profile-btn" onClick={onUserClick} title="User Settings">
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
      value = count.pending;
      tone = "attention";
    } else if (count.approved > 0) {
      value = count.approved;
      tone = "success";
    }
  } else if (stepKey === "scrape" && count.items) {
    value = count.items;
  } else if (stepKey === "persona" && count.docs) {
    value = count.docs;
  } else if (stepKey === "setup" && count.sources) {
    value = count.sources;
  }
  if (value == null) return null;
  return (
    <span className={`wf-count-pill wf-count-${tone} ${active ? "active" : ""}`}>{value}</span>
  );
}
