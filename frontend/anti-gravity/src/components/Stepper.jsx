import "./Stepper.css";

export function Stepper({ currentStep, steps, onStepClick }) {
  return (
    <div className="global-nav-stepper">
      {steps.map((step, index) => {
        const stepNumber = index + 1;
        const isActive = stepNumber === currentStep;

        return (
          <button
            key={index}
            className={`nav-step ${isActive ? "active" : ""}`}
            onClick={() => onStepClick && onStepClick(stepNumber)}
            type="button"
          >
            <span className="nav-step-label">{step.label}</span>
          </button>
        );
      })}
    </div>
  );
}
