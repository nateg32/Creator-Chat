/**
 * Unified feedback system: Toast + Confirm dialog.
 *
 * Usage:
 *   const { toast, confirm } = useFeedback();
 *   toast.success("Saved");
 *   toast.error("Failed: " + err.message);
 *   const ok = await confirm({ title: "Delete chat?", message: "...", danger: true });
 *
 * Replaces window.alert / window.confirm with on-brand UI matching the
 * app's premium light theme.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import "./Feedback.css";

const FeedbackContext = createContext(null);

let _toastId = 0;
const TOAST_DEFAULT_MS = 4200;

// ── Icons (inline SVG, currentColor) ───────────────────────────────────
const IconCheck = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
    <path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const IconAlert = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
    <path d="M8 5v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <circle cx="8" cy="11.5" r="1" fill="currentColor" />
    <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);
const IconInfo = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" />
    <path d="M8 7.5v3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <circle cx="8" cy="5" r="1" fill="currentColor" />
  </svg>
);

const VARIANT_ICON = {
  success: IconCheck,
  error: IconAlert,
  info: IconInfo,
};

// ── Toast ──────────────────────────────────────────────────────────────
function ToastItem({ toast, onDismiss }) {
  const [leaving, setLeaving] = useState(false);
  const dismiss = useCallback(() => {
    setLeaving(true);
    setTimeout(() => onDismiss(toast.id), 180);
  }, [onDismiss, toast.id]);

  useEffect(() => {
    if (toast.duration === 0) return undefined;
    const t = setTimeout(dismiss, toast.duration ?? TOAST_DEFAULT_MS);
    return () => clearTimeout(t);
  }, [toast.duration, dismiss]);

  const Icon = VARIANT_ICON[toast.variant] || IconInfo;
  return (
    <div
      className={`fb-toast fb-toast--${toast.variant}${leaving ? " fb-toast--leaving" : ""}`}
      role={toast.variant === "error" ? "alert" : "status"}
    >
      <span className="fb-toast__icon"><Icon /></span>
      <div className="fb-toast__body">
        {toast.title && <p className="fb-toast__title">{toast.title}</p>}
        <p className="fb-toast__msg">{toast.message}</p>
      </div>
      <button type="button" className="fb-toast__close" onClick={dismiss} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}

function ToastRegion({ toasts, onDismiss }) {
  if (!toasts.length) return null;
  return (
    <div className="fb-toast-region" aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

// ── Confirm dialog ─────────────────────────────────────────────────────
function ConfirmDialog({ request, onResolve }) {
  const confirmBtnRef = useRef(null);

  useEffect(() => {
    confirmBtnRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onResolve(false);
      else if (e.key === "Enter") onResolve(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onResolve]);

  const {
    title = "Are you sure?",
    message = "",
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    danger = false,
  } = request;

  return (
    <div
      className="fb-confirm-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="fb-confirm-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onResolve(false);
      }}
    >
      <div className={`fb-confirm${danger ? " fb-confirm--danger" : ""}`}>
        <div className="fb-confirm__icon" aria-hidden="true">
          {danger ? <IconAlert /> : <IconInfo />}
        </div>
        <h3 id="fb-confirm-title" className="fb-confirm__title">{title}</h3>
        {message && <p className="fb-confirm__msg">{message}</p>}
        <div className="fb-confirm__actions">
          <button
            type="button"
            className="fb-confirm__btn fb-confirm__btn--secondary"
            onClick={() => onResolve(false)}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            ref={confirmBtnRef}
            className="fb-confirm__btn fb-confirm__btn--primary"
            onClick={() => onResolve(true)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Provider ───────────────────────────────────────────────────────────
export function FeedbackProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [confirmReq, setConfirmReq] = useState(null);
  const confirmResolverRef = useRef(null);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const pushToast = useCallback((variant, messageOrOpts, maybeOpts) => {
    const opts =
      typeof messageOrOpts === "string"
        ? { message: messageOrOpts, ...(maybeOpts || {}) }
        : (messageOrOpts || {});
    const id = ++_toastId;
    setToasts((prev) => [
      ...prev,
      {
        id,
        variant,
        title: opts.title,
        message: opts.message ?? "",
        duration: opts.duration,
      },
    ]);
    return id;
  }, []);

  const toast = useMemo(
    () => ({
      success: (m, o) => pushToast("success", m, o),
      error: (m, o) => pushToast("error", m, o),
      info: (m, o) => pushToast("info", m, o),
      dismiss: dismissToast,
    }),
    [pushToast, dismissToast],
  );

  const confirm = useCallback((options) => {
    return new Promise((resolve) => {
      confirmResolverRef.current = resolve;
      setConfirmReq(options || {});
    });
  }, []);

  const resolveConfirm = useCallback((value) => {
    setConfirmReq(null);
    const r = confirmResolverRef.current;
    confirmResolverRef.current = null;
    if (r) r(value);
  }, []);

  const value = useMemo(() => ({ toast, confirm }), [toast, confirm]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <ToastRegion toasts={toasts} onDismiss={dismissToast} />
      {confirmReq && <ConfirmDialog request={confirmReq} onResolve={resolveConfirm} />}
    </FeedbackContext.Provider>
  );
}

export function useFeedback() {
  const ctx = useContext(FeedbackContext);
  if (!ctx) {
    // Soft fallback so any component can call this without crashing if the
    // provider isn't mounted (e.g. tests, isolated stories).
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
