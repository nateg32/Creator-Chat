import { useContext } from "react";
import { FeedbackContext } from "./FeedbackContext";

export function useFeedback() {
  const ctx = useContext(FeedbackContext);
  if (!ctx) {
    return {
      toast: {
        success: (m) => console.log("[toast]", m),
        error: (m) => console.warn("[toast:error]", m),
        info: (m) => console.log("[toast:info]", m),
        dismiss: () => {},
      },
      confirm: async (opts) => window.confirm(opts?.message || opts?.title || "Confirm?"),
    };
  }
  return ctx;
}
