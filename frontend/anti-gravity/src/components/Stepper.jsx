import "./Stepper.css";

export function Stepper({ currentStep, steps, onStepClick, searchProgress = 0 }) {
  return (
    <div className="global-nav-stepper">
      <div className="app-branding">Creator Bot</div>
      <div className="nav-steps-container">
        {steps.map((step, index) => {
          const stepNumber = index + 1;
          const isActive = stepNumber === currentStep;
          // Only disable Search (step 2) - it should be visible but not clickable
          const isDisabled = step.key === 'scrape';

          return (
            <button
              key={index}
              className={`nav-step ${isActive ? "active" : ""} ${isDisabled ? "disabled" : ""}`}
              onClick={() => !isDisabled && onStepClick && onStepClick(stepNumber)}
              type="button"
              disabled={isDisabled}
            >
              <span className="nav-step-label">
                {step.label}
                {isActive && step.key === 'scrape' && searchProgress > 0 && searchProgress < 100 && (
                  <span className="step-progress-inline"> {Math.round(searchProgress)}%</span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
