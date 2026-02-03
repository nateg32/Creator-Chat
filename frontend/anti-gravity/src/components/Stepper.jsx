import "./Stepper.css";

export function Stepper({ currentStep, steps }) {
  return (
    <div className="stepper">
      {steps.map((step, index) => {
        const stepNumber = index + 1;
        const isActive = stepNumber === currentStep;
        const isCompleted = stepNumber < currentStep;
        
        return (
          <div key={index} className={`stepper-step ${isActive ? "active" : ""} ${isCompleted ? "completed" : ""}`}>
            <div className="stepper-circle">
              {isCompleted ? (
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <path
                    d="M16.667 5L7.5 14.167 3.333 10"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : (
                <span>{stepNumber}</span>
              )}
            </div>
            <div className="stepper-label">{step.label}</div>
            {index < steps.length - 1 && <div className="stepper-line" />}
          </div>
        );
      })}
    </div>
  );
}
