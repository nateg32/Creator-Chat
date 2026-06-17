import { useEffect, useRef } from "react";
import CreatorChatLogo from "../assets/creator-chat-logo.png";
import "./Stepper.css";

export function Stepper({ currentStep, steps, onStepClick, searchProgress = 0, onUserClick, userAvatarUrl }) {
  const activeStepRef = useRef(null);

  useEffect(() => {
    activeStepRef.current?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
      inline: "center",
    });
  }, [currentStep]);

  return (
    <div className="global-nav-stepper">
      <div className="app-branding">
        <img src={CreatorChatLogo} alt="Creator Chat" className="app-branding-mark" />
      </div>
      <div className="nav-steps-container">
        {steps.map((step, index) => {
          const stepNumber = index + 1;
          const isActive = stepNumber === currentStep;
          // Only disable Search (step 2) - it should be visible but not clickable
          const isDisabled = step.key === 'search';

          return (
            <button
              key={index}
              ref={isActive ? activeStepRef : null}
              className={`nav-step ${isActive ? "active" : ""} ${isDisabled ? "disabled" : ""}`}
              onClick={() => !isDisabled && onStepClick && onStepClick(stepNumber)}
              type="button"
              disabled={isDisabled}
            >
              <span className="nav-step-label">
                {step.label}
                {isActive && step.key === 'search' && searchProgress > 0 && searchProgress < 100 && (
                  <span className="step-progress-inline"> {Math.round(searchProgress)}%</span>
                )}
              </span>
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
