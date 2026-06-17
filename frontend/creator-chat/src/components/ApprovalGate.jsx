import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import "./ApprovalGate.css";
import { useFeedback } from "./feedback/useFeedback";

const DECISION_PENDING = "pending";
const DECISION_APPROVE = "approve";
const DECISION_DENY = "deny";

function decodeHtmlEntities(value) {
  const text = String(value || "");
  if (!text) return "";
  if (typeof document === "undefined") return text;
  const textarea = document.createElement("textarea");
  textarea.innerHTML = text;
  return textarea.value;
}

function normalizeDisplayText(value) {
  let text = decodeHtmlEntities(value);
  const replacements = [
    ["â€™", "'"],
    ["â€˜", "'"],
    ['â€œ', '"'],
    ['â€�', '"'],
    ["â€“", "-"],
    ["â€”", "-"],
    ["â€¦", "..."],
    ["Â", ""],
  ];
  replacements.forEach(([from, to]) => {
    text = text.split(from).join(to);
  });
  return text;
}

function platformStatusText(status, itemCount, error) {
  if (error) return error;
  if (status === "success") {
    return `${itemCount} ${itemCount === 1 ? "item" : "items"} found`;
  }
  if (status === "empty") return "No public content found";
  if (status === "error") return "Search failed";
  if (status === "skipped") return "Not searched";
  if (status === "searching") return "Search was still running";
  return "No status reported";
}

function platformName(key, status) {
  return status?.platform || status?.platform_label || String(key || "source").replace(/_/g, " ");
}

export function ApprovalGate({ items, platformStatuses = null, onSave, onBack, loading, progress, reviewCount = null, forceShowSave = false }) {
  const { toast } = useFeedback();
  const gateRef = useRef(null);
  const progressRef = useRef(null);
  const footerRef = useRef(null);
  const hasScrolledToProgressRef = useRef(false);
  const itemKeySignature = useMemo(() => JSON.stringify(
    items.map((item) => item.item_id || item.queue_id)
  ), [items]);

  const initialDecisions = useMemo(() => {
    const initial = {};
    items.forEach((item) => {
      const itemKey = item.item_id || item.queue_id;
      const currentStatus = String(item.status || item.item_status || DECISION_PENDING).toLowerCase();
      if (["ingested", "approved", "completed", "ready"].includes(currentStatus)) {
        initial[itemKey] = DECISION_APPROVE;
      } else if (currentStatus === DECISION_DENY || currentStatus === "denied") {
        initial[itemKey] = DECISION_DENY;
      } else {
        initial[itemKey] = DECISION_PENDING;
      }
    });
    return initial;
  }, [items]);

  const [decisions, setDecisions] = useState(() => initialDecisions);
  const [saveHintActive, setSaveHintActive] = useState(false);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setDecisions((prev) => {
        const next = {};
        let changed = Object.keys(prev).length !== items.length;

        items.forEach((item) => {
          const itemKey = item.item_id || item.queue_id;
          const nextDecision = Object.prototype.hasOwnProperty.call(prev, itemKey)
            ? prev[itemKey]
            : (initialDecisions[itemKey] ?? DECISION_PENDING);
          next[itemKey] = nextDecision;
          if (prev[itemKey] !== nextDecision) {
            changed = true;
          }
        });

        return changed ? next : prev;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [itemKeySignature, items, initialDecisions]);

  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const scrollToApprovalTop = useCallback((behavior = "smooth") => {
    const workflowTop = document.querySelector(".workflow-container");
    const target = progressRef.current || gateRef.current || workflowTop;
    if (!target) {
      window.scrollTo({ top: 0, behavior });
      return;
    }
    const targetTop = target.getBoundingClientRect().top + window.scrollY;
    const workflowTopPosition = workflowTop
      ? workflowTop.getBoundingClientRect().top + window.scrollY
      : targetTop;
    window.scrollTo({
      top: Math.max(0, Math.min(targetTop, workflowTopPosition)),
      behavior,
    });
  }, []);

  useEffect(() => {
    if (!saveHintActive) return undefined;
    const timeout = window.setTimeout(() => setSaveHintActive(false), 1800);
    return () => window.clearTimeout(timeout);
  }, [saveHintActive]);

  useEffect(() => {
    if (!progress && !loading) {
      hasScrolledToProgressRef.current = false;
      return;
    }
    if (hasScrolledToProgressRef.current) return;

    hasScrolledToProgressRef.current = true;
    window.requestAnimationFrame(() => {
      scrollToApprovalTop();
    });
  }, [loading, progress, scrollToApprovalTop]);

  const filteredItems = useMemo(() => {
    let filtered = items;

    if (filter !== "all") {
      filtered = filtered.filter((item) => {
        const itemKey = item.item_id || item.queue_id;
        return decisions[itemKey] === filter;
      });
    }

    if (search.trim()) {
      const query = search.toLowerCase();
      filtered = filtered.filter(
        (item) =>
          (item.source_url || item.url || "").toLowerCase().includes(query) ||
          normalizeDisplayText(item.title || item.caption || "").toLowerCase().includes(query) ||
          normalizeDisplayText(((item.metadata || {}).title || "")).toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [items, decisions, filter, search]);

  const platformStatusEntries = useMemo(() => {
    if (!platformStatuses || typeof platformStatuses !== "object") return [];
    return Object.entries(platformStatuses).filter(([, status]) => status && typeof status === "object");
  }, [platformStatuses]);

  function setDecision(itemKey, decision) {
    setDecisions((prev) => ({ ...prev, [itemKey]: decision }));
  }

  function scrollToSave() {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        footerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
        setSaveHintActive(true);
      });
    });
  }

  function approveAll() {
    const newDecisions = {};
    items.forEach((item) => {
      const itemKey = item.item_id || item.queue_id;
      newDecisions[itemKey] = DECISION_APPROVE;
    });
    setDecisions(newDecisions);
    scrollToSave();
  }

  function resetDecisions() {
    setDecisions(initialDecisions);
  }

  async function handleSave() {
    const decisionsArray = items.map((item) => {
      const itemKey = item.item_id || item.queue_id;
      return {
        item_id: itemKey,
        decision: decisions[itemKey] || DECISION_PENDING,
      };
    });

    const actionableCount = decisionsArray.filter((d) => d.decision === DECISION_APPROVE || d.decision === DECISION_DENY).length;

    if (actionableCount === 0) {
      toast.info("Approve or deny at least one item before saving your content decisions.");
      return;
    }

    if (!isDirty && !forceShowSave) return;
    scrollToApprovalTop();
    await onSave(decisionsArray);
  }

  const isDirty = items.some((item) => {
    const itemKey = item.item_id || item.queue_id;
    return (decisions[itemKey] || DECISION_PENDING) !== (initialDecisions[itemKey] || DECISION_PENDING);
  });

  const approvedCount = items.filter((item) => {
    const itemKey = item.item_id || item.queue_id;
    return decisions[itemKey] === DECISION_APPROVE;
  }).length;
  const deniedCount = items.filter((item) => {
    const itemKey = item.item_id || item.queue_id;
    return decisions[itemKey] === DECISION_DENY;
  }).length;
  const pendingCount = items.filter((item) => {
    const itemKey = item.item_id || item.queue_id;
    return decisions[itemKey] === DECISION_PENDING;
  }).length;
  const decidedCount = approvedCount + deniedCount;
  const serverPendingCount = Number(reviewCount?.pending || 0);
  const serverTotalCount = Number(reviewCount?.total || 0);
  const saveButtonLabel = !isDirty && forceShowSave
    ? `Confirm (${decidedCount})`
    : `Save (${decidedCount})`;
  const approvalSubtitle = isDirty || forceShowSave
    ? "Scroll down to save your approval choices."
    : "Choose what shapes the creator.";
  const reviewNote = (() => {
    if (pendingCount > 0) {
      return `${pendingCount} item${pendingCount === 1 ? "" : "s"} still need a choice.`;
    }
    if (serverPendingCount > 0 && (isDirty || forceShowSave)) {
      return `${decidedCount} choice${decidedCount === 1 ? "" : "s"} selected. Save below to finish the latest review.`;
    }
    if (serverPendingCount > 0) {
      return `${serverPendingCount} item${serverPendingCount === 1 ? "" : "s"} still need review, but they are not visible here. Refresh to reload the latest review queue.`;
    }
    if (isDirty) {
      return `${decidedCount} choice${decidedCount === 1 ? "" : "s"} staged. Save to update the creator memory.`;
    }
    if (serverTotalCount > 0 && serverPendingCount === 0) {
      return "Latest search review is saved.";
    }
    return "";
  })();
  const progressPercent = progress?.total > 0
    ? Math.max(0, Math.min(100, Math.round((Number(progress.current || 0) / Number(progress.total || 100)) * 100)))
    : 0;
  const progressStage = String(progress?.stage || "").toLowerCase();
  const progressSteps = [
    { key: "queued", label: "Queued", active: ["starting", "queued", "pending", "reconnecting"].includes(progressStage), done: progressPercent >= 12 },
    { key: "preparing", label: "Preparing", active: progressStage === "processing" && progressPercent < 55, done: progressPercent >= 55 },
    { key: "embedding", label: "Embedding", active: progressStage === "processing" && progressPercent >= 55 && progressPercent < 92, done: progressPercent >= 92 },
    { key: "finalising", label: "Finalising", active: progressStage === "processing" && progressPercent >= 92, done: progressStage === "completed" },
  ];
  const activeProgressStep = progressSteps.find((step) => step.active) || progressSteps.find((step) => !step.done) || progressSteps[progressSteps.length - 1];

  return (
    <div className="approval-gate" ref={gateRef}>
      <div className="approval-header">
        <div>
          <span className="approval-kicker">Review</span>
          <h2>Approve Signal</h2>
        </div>
        <p className="subtitle">{approvalSubtitle}</p>
      </div>

      {progress && (
        <div
          ref={progressRef}
          className={`progress-container progress-stage-${progressStage || "active"}`}
        >
          <div className="progress-info">
            <div>
              <span className="progress-kicker">Knowledge update</span>
              <span className="progress-stage">{progress.message || "Processing..."}</span>
              {progress.detail && <span className="progress-detail">{progress.detail}</span>}
            </div>
            <div className="progress-meta">
              <span className="progress-current-phase">{activeProgressStep?.label || "Updating"}</span>
              <span className="progress-count">{progressPercent}%</span>
            </div>
          </div>
          <div className="progress-bar-wrapper" aria-label="Knowledge update progress">
            <div
              className="progress-bar-fill"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <div className="progress-rail">
            {progressSteps.map((step, index) => (
              <span
                key={step.key}
                className={`progress-step ${step.active ? "active" : ""} ${step.done ? "done" : ""}`}
              >
                {step.label}
                {index < progressSteps.length - 1 && <span className="progress-step-separator">/</span>}
              </span>
            ))}
          </div>
          <p className="progress-guidance">
            Keep this page open while the approved content is added to the creator.
          </p>
        </div>
      )}

      <div className="approval-layout">
        <div className="approval-sidebar">
          {reviewNote && (
            <div className={`approval-review-note ${serverPendingCount > 0 ? "needs-review" : "saved"}`}>
              {reviewNote}
            </div>
          )}

          <div className="filter-section">
            <label>Filter by choice</label>
            <div className="filter-buttons">
              <button
                type="button"
                className={filter === "all" ? "active" : ""}
                onClick={() => setFilter("all")}
              >
                All ({items.length})
              </button>
              <button
                type="button"
                className={filter === DECISION_APPROVE ? "active" : ""}
                onClick={() => setFilter(DECISION_APPROVE)}
              >
                To approve ({approvedCount})
              </button>
              <button
                type="button"
                className={filter === DECISION_DENY ? "active" : ""}
                onClick={() => setFilter(DECISION_DENY)}
              >
                Deny ({deniedCount})
              </button>
              <button
                type="button"
                className={filter === DECISION_PENDING ? "active" : ""}
                onClick={() => setFilter(DECISION_PENDING)}
              >
                Unreviewed ({pendingCount})
              </button>
            </div>
          </div>

          <div className="search-section">
            <label>Search</label>
            <input
              type="text"
              placeholder="Search by title..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="bulk-actions">
            <label>Bulk actions</label>
            <div className="bulk-buttons">
              <button type="button" onClick={approveAll} className="bulk-button">
                Approve all
              </button>
              <button type="button" onClick={resetDecisions} className="bulk-button">
                Reset
              </button>
            </div>
          </div>
        </div>

        <div className="approval-content">
          <div className="items-grid">
            {filteredItems.length === 0 ? (
              <div className="empty-state">
                <div>
                  <h3>{items.length === 0 ? "No search results to review" : "No items match this view"}</h3>
                  <p>
                    {items.length === 0
                      ? "Adjust the sources or limits, then run search again."
                      : "Clear the search or switch filters to keep reviewing."}
                  </p>
                  {items.length === 0 && platformStatusEntries.length > 0 && (
                    <div className="approval-platform-statuses" aria-label="Platform search statuses">
                      {platformStatusEntries.map(([key, status]) => {
                        const statusValue = String(status.status || status.last_scrape_status || "unknown").toLowerCase();
                        const itemCount = Number(status.item_count ?? status.items_found ?? 0);
                        const error = status.error || status.last_error || status.detail || "";
                        return (
                          <div key={key} className={`approval-platform-status-row ${statusValue}`}>
                            <span className="approval-platform-name">{platformName(key, status)}</span>
                            <span className="approval-platform-detail">
                              {platformStatusText(statusValue, itemCount, error)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {items.length === 0 && onBack && (
                    <button type="button" className="secondary-button" onClick={onBack}>
                      Adjust sources
                    </button>
                  )}
                </div>
              </div>
            ) : (
              filteredItems.map((item) => {
                const itemKey = item.item_id || item.queue_id;
                const decision = decisions[itemKey] || DECISION_PENDING;
                const metadata = item.metadata || {};
                const platform = item.platform || metadata.platform || metadata.source || "unknown";
                const title = normalizeDisplayText(item.title || metadata.title || item.caption || "Untitled content");

                return (
                  <div
                    key={itemKey}
                    className={`approval-card ${decision === DECISION_APPROVE ? "approved" : ""} ${decision === DECISION_DENY ? "denied" : ""}`}
                  >
                    <div className="card-header">
                      <div className="platform-info">
                        <span className={`platform-badge platform-${platform.toLowerCase().replace(" / ", "-")}`}>
                          {platform.toUpperCase()}
                        </span>
                      </div>
                    </div>

                    <div className="card-body">
                      <h3 className="card-title">
                        {title}
                      </h3>

                      <div className="card-actions">
                        {(item.source_url || item.url) && (
                          <a
                            href={item.source_url || item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="card-link"
                          >
                            View source
                          </a>
                        )}

                        <div className="decision-controls">
                          <button
                            type="button"
                            className={`decision-button ${decision === DECISION_APPROVE ? "active approve" : ""}`}
                            onClick={() =>
                              setDecision(
                                itemKey,
                                decision === DECISION_APPROVE
                                  ? DECISION_PENDING
                                  : DECISION_APPROVE
                              )
                            }
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className={`decision-button ${decision === DECISION_DENY ? "active deny" : ""}`}
                            onClick={() =>
                              setDecision(
                                itemKey,
                                decision === DECISION_DENY
                                  ? DECISION_PENDING
                                  : DECISION_DENY
                              )
                            }
                          >
                            Deny
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <div
        ref={footerRef}
        className={`approval-footer ${saveHintActive ? "approval-footer-attention" : ""}`}
      >
        <div className="approval-footer-copy">
          {isDirty || forceShowSave
            ? "Choices are not saved yet."
            : approvedCount > 0
              ? "Saved approved content becomes memory."
              : "Approve content to unlock Persona."}
        </div>
        {isDirty || forceShowSave ? (
          <button
            type="button"
            onClick={handleSave}
            className="primary-button"
            disabled={loading || (approvedCount === 0 && deniedCount === 0)}
          >
            {loading ? "Updating knowledge..." : saveButtonLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
