import { useState, useMemo } from "react";
import "./ApprovalGate.css";

const DECISION_PENDING = "pending";
const DECISION_APPROVE = "approve";
const DECISION_DENY = "deny";

export function ApprovalGate({ items, onSave, onBack, loading, progress }) {
  const [decisions, setDecisions] = useState(() => {
    const initial = {};
    items.forEach((item) => {
      // Support both item_id (new) and queue_id (legacy)
      const itemKey = item.item_id || item.queue_id;
      initial[itemKey] = DECISION_PENDING;
    });
    return initial;
  });

  const [expanded, setExpanded] = useState({});
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const filteredItems = useMemo(() => {
    let filtered = items;

    // Filter by decision
    if (filter !== "all") {
      filtered = filtered.filter((item) => {
        const itemKey = item.item_id || item.queue_id;
        return decisions[itemKey] === filter;
      });
    }

    // Search
    if (search.trim()) {
      const query = search.toLowerCase();
      filtered = filtered.filter(
        (item) =>
          (item.source_url || item.url || "").toLowerCase().includes(query) ||
          (item.caption || item.title || "").toLowerCase().includes(query) ||
          (item.preview || "").toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [items, decisions, filter, search]);

  function setDecision(itemKey, decision) {
    setDecisions((prev) => ({ ...prev, [itemKey]: decision }));
  }

  function approveAll() {
    const newDecisions = {};
    items.forEach((item) => {
      const itemKey = item.item_id || item.queue_id;
      newDecisions[itemKey] = DECISION_APPROVE;
    });
    setDecisions(newDecisions);
  }

  function denyAll() {
    const newDecisions = {};
    items.forEach((item) => {
      const itemKey = item.item_id || item.queue_id;
      newDecisions[itemKey] = DECISION_DENY;
    });
    setDecisions(newDecisions);
  }

  function resetDecisions() {
    const newDecisions = {};
    items.forEach((item) => {
      const itemKey = item.item_id || item.queue_id;
      newDecisions[itemKey] = DECISION_PENDING;
    });
    setDecisions(newDecisions);
  }

  async function handleSave() {
    // Build decisions array in format expected by API
    const decisionsArray = items.map((item) => {
      const itemKey = item.item_id || item.queue_id;
      return {
        item_id: itemKey,
        decision: decisions[itemKey] || DECISION_PENDING,
      };
    });

    const approvedCount = decisionsArray.filter((d) => d.decision === DECISION_APPROVE).length;

    if (approvedCount === 0) {
      alert("Please approve at least one item to add to the knowledge base.");
      return;
    }

    await onSave(decisionsArray);
  }

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

  return (
    <div className="approval-gate">
      <div className="approval-header">
        <h2>Knowledge Base Gate</h2>
        <p className="subtitle">Approve or deny each item before adding to the knowledge base</p>
      </div>

      {/* Progress Bar */}
      {progress && (
        <div className="progress-container">
          <div className="progress-info">
            <span className="progress-stage">{progress.message || "Processing..."}</span>
            {progress.total > 0 && (
              <span className="progress-count">{progress.current} / {progress.total}</span>
            )}
          </div>
          <div className="progress-bar-wrapper">
            <div
              className="progress-bar-fill"
              style={{
                width: progress.total > 0 ? `${(progress.current / progress.total) * 100}%` : "0%"
              }}
            >
              {progress.total > 0 && (
                <span className="progress-percentage">
                  {Math.round((progress.current / progress.total) * 100)}%
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="approval-layout">
        <div className="approval-sidebar">
          <div className="filter-section">
            <label>Filter by decision</label>
            <div className="filter-buttons">
              <button
                className={filter === "all" ? "active" : ""}
                onClick={() => setFilter("all")}
              >
                All ({items.length})
              </button>
              <button
                className={filter === DECISION_APPROVE ? "active" : ""}
                onClick={() => setFilter(DECISION_APPROVE)}
              >
                Approved ({approvedCount})
              </button>
              <button
                className={filter === DECISION_DENY ? "active" : ""}
                onClick={() => setFilter(DECISION_DENY)}
              >
                Denied ({deniedCount})
              </button>
              <button
                className={filter === DECISION_PENDING ? "active" : ""}
                onClick={() => setFilter(DECISION_PENDING)}
              >
                Pending ({pendingCount})
              </button>
            </div>
          </div>

          <div className="search-section">
            <label>Search</label>
            <input
              type="text"
              placeholder="Search by title or content..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="bulk-actions">
            <label>Bulk actions</label>
            <div className="bulk-buttons">
              <button onClick={approveAll} className="bulk-button">
                Approve all
              </button>
              <button onClick={denyAll} className="bulk-button">
                Deny all
              </button>
              <button onClick={resetDecisions} className="bulk-button">
                Reset
              </button>
            </div>
          </div>
        </div>

        <div className="approval-content">
          <div className="items-grid">
            {filteredItems.length === 0 ? (
              <div className="empty-state">
                <p>No items match your filters.</p>
              </div>
            ) : (
              filteredItems.map((item) => {
                const itemKey = item.item_id || item.queue_id;
                const decision = decisions[itemKey] || DECISION_PENDING;
                const isExpanded = expanded[itemKey];
                const previewText = item.preview || item.caption || "";
                const transcriptStatus = item.transcript_status || "missing";

                return (
                  <div
                    key={itemKey}
                    className={`approval-card ${decision === DECISION_APPROVE ? "approved" : ""} ${decision === DECISION_DENY ? "denied" : ""}`}
                  >
                    <div className="card-header">
                      <div className="decision-controls">
                        <button
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
                          ✓ Approve
                        </button>
                        <button
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
                          ✗ Deny
                        </button>
                      </div>
                      <div className="transcript-badge">
                        {transcriptStatus === "present" && (
                          <span className="badge badge-success">✓ Transcript</span>
                        )}
                        {transcriptStatus === "missing" && (
                          <span className="badge badge-warning">⚠ No transcript</span>
                        )}
                        {transcriptStatus === "error" && (
                          <span className="badge badge-error">✗ Error</span>
                        )}
                      </div>
                    </div>

                    <div className="card-body">
                      <h3 className="card-title">
                        {item.source_url ? new URL(item.source_url).pathname.split("/").pop() : "Untitled"}
                      </h3>
                      <p className="card-preview">
                        {isExpanded ? previewText : (previewText.substring(0, 150) || "No preview available")}
                        {previewText.length > 150 && !isExpanded && "..."}
                      </p>
                      {previewText.length > 150 && (
                        <button
                          className="expand-button"
                          onClick={() =>
                            setExpanded((prev) => ({
                              ...prev,
                              [itemKey]: !prev[itemKey],
                            }))
                          }
                        >
                          {isExpanded ? "Show less" : "Show more"}
                        </button>
                      )}
                      {(item.source_url || item.url) && (
                        <a
                          href={item.source_url || item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="card-link"
                        >
                          View source →
                        </a>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <div className="approval-footer">
        <button onClick={onBack} className="secondary-button" disabled={loading}>
          Back
        </button>
        <button
          onClick={handleSave}
          className="primary-button"
          disabled={loading || approvedCount === 0}
        >
          {loading ? (progress ? progress.message : "Saving...") : `Save to knowledge base (${approvedCount} items)`}
        </button>
      </div>
    </div>
  );
}
