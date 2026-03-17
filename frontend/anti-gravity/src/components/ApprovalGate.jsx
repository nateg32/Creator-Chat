import { useState, useMemo, useEffect } from "react";
import "./ApprovalGate.css";

const DECISION_PENDING = "pending";
const DECISION_APPROVE = "approve";
const DECISION_DENY = "deny";

export function ApprovalGate({ items, onSave, onBack, loading, progress, forceShowSave = false }) {
  const initialDecisionSignature = useMemo(() => JSON.stringify(
    items.map((item) => {
      const itemKey = item.item_id || item.queue_id;
      const currentStatus = String(item.status || item.item_status || DECISION_PENDING).toLowerCase();
      return [itemKey, currentStatus];
    })
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

  const [decisions, setDecisions] = useState(initialDecisions);

  useEffect(() => {
    setDecisions(initialDecisions);
  }, [initialDecisionSignature]);

  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

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
          (item.title || item.caption || "").toLowerCase().includes(query) ||
          ((item.metadata || {}).title || "").toLowerCase().includes(query)
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
      alert("Approve or deny at least one item before saving your content decisions.");
      return;
    }

    if (!isDirty && !forceShowSave) return;
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
  const saveButtonLabel = !isDirty && forceShowSave
    ? `Confirm content decisions (${decidedCount} items)`
    : `Save content decisions (${decidedCount} items)`;

  return (
    <div className="approval-gate">
      <div className="approval-header">
        <h2>Knowledge Base Gate</h2>
        <p className="subtitle">Approve or deny each item before adding to the knowledge base</p>
      </div>

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
                width: progress.total > 0 ? `${(progress.current / progress.total) * 100}%` : "0%",
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
              placeholder="Search by title..."
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
                const metadata = item.metadata || {};
                const platform = item.platform || metadata.platform || metadata.source || "unknown";
                const title = item.title || metadata.title || item.caption || "Untitled content";

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
                      {!item.is_primary && item.duplicate_of_item_id !== undefined && (
                        <span className="badge badge-warning">Duplicate</span>
                      )}
                    </div>

                    <div className="card-body">
                      <h3 className="card-title" title={title}>
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
                            View source →
                          </a>
                        )}

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
                            Approve
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

      <div className="approval-footer">
        <button onClick={onBack} className="secondary-button" disabled={loading}>
          Back
        </button>
        {isDirty || forceShowSave ? (
          <button
            onClick={handleSave}
            className="primary-button"
            disabled={loading || (approvedCount === 0 && deniedCount === 0)}
          >
            {loading ? (progress ? progress.message : "Saving...") : saveButtonLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
