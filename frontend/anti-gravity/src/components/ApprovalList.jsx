import { useState } from "react";
import { approveIngest, getQueueItems } from "../api/client";

export function ApprovalList({ creatorId, scrapedItems, onIngested, onItemsUpdated }) {
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  if (!scrapedItems || scrapedItems.length === 0) {
    return (
      <div className="approval-list">
        <h2>Approval List</h2>
        <div className="empty-state">No search results yet. Use Creator Setup to search content.</div>
      </div>
    );
  }

  function toggleItem(queueId) {
    const newSelected = new Set(selected);
    if (newSelected.has(queueId)) {
      newSelected.delete(queueId);
    } else {
      newSelected.add(queueId);
    }
    setSelected(newSelected);
  }

  function toggleAll() {
    const allQueueIds = scrapedItems
      .filter((item) => item.status !== "ingested")
      .map((item) => item.queue_id);
    if (selected.size === allQueueIds.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(allQueueIds));
    }
  }

  async function handleIngest() {
    if (selected.size === 0) {
      setError("Please select at least one item to ingest");
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const queue_ids = Array.from(selected);

      const result = await approveIngest({
        creator_id: creatorId,
        queue_ids,
      });

      const totalChunks = result.ingested.reduce((sum, item) => sum + item.chunks_inserted, 0);
      setSuccess(
        `✅ Ingested ${result.ingested.length} items, ${totalChunks} total chunks`
      );
      setSelected(new Set());
      
      // Re-fetch queue items to get updated status
      try {
        const queueData = await getQueueItems(creatorId);
        if (onItemsUpdated) {
          onItemsUpdated(queueData.items);
        }
      } catch (e) {
        console.error("Failed to refresh queue items:", e);
      }
      
      if (onIngested) {
        onIngested(result);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="approval-list">
      <div className="approval-header">
        <h2>Approval List</h2>
        <div className="approval-actions">
          <button onClick={toggleAll} className="toggle-all">
            {selected.size === scrapedItems.length ? "Deselect All" : "Select All"}
          </button>
          <button
            onClick={handleIngest}
            disabled={loading || selected.size === 0}
            className="ingest-button"
          >
            {loading ? "Ingesting…" : `Ingest Selected (${selected.size})`}
          </button>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}
      {success && <div className="success-message">{success}</div>}

      <div className="items-list">
        {scrapedItems.map((item) => {
          const isIngested = item.status === "ingested";
          const isSelected = selected.has(item.queue_id);

          return (
            <div
              key={item.queue_id}
              className={`approval-item ${isSelected ? "selected" : ""} ${
                isIngested ? "ingested" : ""
              }`}
              onClick={() => !isIngested && toggleItem(item.queue_id)}
            >
              <div className="item-checkbox">
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={isIngested}
                  onChange={() => !isIngested && toggleItem(item.queue_id)}
                  onClick={(e) => e.stopPropagation()}
                />
              </div>
              <div className="item-content">
                <div className="item-header">
                  <h3 className="item-title">{item.title || `Item ${item.queue_id}`}</h3>
                  <span className={`item-status ${isIngested ? "status-ingested" : "status-pending"}`}>
                    {isIngested
                      ? `Ingested${item.chunks_inserted ? ` (${item.chunks_inserted} chunks)` : ""}`
                      : "Pending"}
                  </span>
                </div>
                <div className="item-preview">{item.preview}</div>
                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="item-link"
                    onClick={(e) => e.stopPropagation()}
                  >
                    View source →
                  </a>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
