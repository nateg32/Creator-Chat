import "./WorkflowGuide.css";

const FALLBACK_COPY = {
  setup: {
    title: "Sources",
    body: "Choose profiles. Search starts after save.",
  },
  search: {
    title: "Searching",
    body: "Gathering public content.",
  },
  approve: {
    title: "Review",
    body: "Keep only the useful signal.",
  },
  persona: {
    title: "Persona",
    body: "Building voice and behavior.",
  },
  chat: {
    title: "Chat",
    body: "Ready for conversation.",
  },
};

function normalizeStatus(status) {
  const value = String(status || "").toLowerCase();
  if (value === "complete") return "complete";
  if (value === "active") return "active";
  if (value === "available") return "available";
  if (value === "locked") return "locked";
  return "available";
}

function countLabel(step) {
  const count = step?.count || {};
  if (step?.key === "setup" && count.sources) return `${count.sources} source${count.sources === 1 ? "" : "s"}`;
  if (step?.key === "search" && count.items) return `${count.items} found`;
  if (step?.key === "approve") {
    if (count.pending > 0) return `${count.pending} to review`;
    if (count.approved > 0) return `${count.approved} approved`;
  }
  if (step?.key === "persona" && count.docs) return `${count.docs} docs`;
  return "";
}

function stepStateLabel(step) {
  if (step?.stale) return "Needs review";
  const status = normalizeStatus(step?.status);
  if (status === "complete") return "Done";
  if (status === "active") return "Now";
  if (status === "locked") return "Locked";
  return "Ready";
}

export function WorkflowGuide({ workflowState, currentStepKey }) {
  const steps = (workflowState?.steps || []).filter((step) => !step.hidden);
  if (!steps.length) return null;

  const activeStep = steps.find((step) => step.key === currentStepKey)
    || steps.find((step) => step.key === workflowState?.currentStep)
    || steps.find((step) => normalizeStatus(step.status) === "active")
    || steps[0];
  const activeIndex = Math.max(0, steps.findIndex((step) => step.key === activeStep.key));
  const activeCopy = FALLBACK_COPY[activeStep.key] || FALLBACK_COPY.setup;

  return (
    <section className={`workflow-guide workflow-guide-${activeStep.key}`} aria-label="Creator setup flow">
      <div className="workflow-guide-copy">
        <span className="workflow-guide-eyebrow">
          Step {activeIndex + 1} of {steps.length}
        </span>
        <strong>{activeCopy.title}</strong>
        <p>{activeCopy.body}</p>
      </div>

      <ol className="workflow-guide-rail">
        {steps.map((step, index) => {
          const status = normalizeStatus(step.status);
          const isCurrent = step.key === activeStep.key;
          const meta = countLabel(step);
          return (
            <li
              key={step.key}
              className={[
                "workflow-guide-step",
                `is-${status}`,
                isCurrent ? "current" : "",
                step.stale ? "stale" : "",
              ].filter(Boolean).join(" ")}
            >
              <span className="workflow-guide-index">{index + 1}</span>
              <span className="workflow-guide-step-copy">
                <span>{step.label}</span>
                <em>{meta || stepStateLabel(step)}</em>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
