import "./ScrapePreview.css";

function statusIcon(s) {
  if (s === "success") return "✓";
  if (s === "error") return "⚠";
  return "·";
}

export function ScrapePreview({ items, platformStatuses, onContinue, onBack }) {
  const hasItems = items && items.length > 0;
  const statusEntries = platformStatuses && Object.entries(platformStatuses);

  return (
    <div className="scrape-preview-card">
      {statusEntries && statusEntries.length > 0 && (
        <div className="platform-status-summary">
          <h3 style={{ marginBottom: "12px", fontSize: "14px", fontWeight: 600 }}>Platform Results:</h3>
          {statusEntries.map(([key, st]) => {
            const itemsCount = st?.items_found || 0;
            const status = st?.last_scrape_status || "skipped";
            return (
              <div key={key} className={`platform-status platform-status-${status}`} title={st?.last_error || ""} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", marginBottom: "4px" }}>
                <span>
                  {statusIcon(status)} {key}
                </span>
                <span style={{ fontSize: "12px", opacity: 0.8 }}>
                  {itemsCount} {itemsCount === 1 ? "item" : "items"}
                  {st?.last_error && ` - ${st.last_error}`}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {!hasItems ? (
        <>
          <div className="empty-state">
            <p>No items found.</p>
            {(!statusEntries || statusEntries.length === 0) ? (
              <p style={{ marginTop: "8px", fontSize: "14px", color: "#666" }}>
                Go back to Setup and add valid profile URLs for each platform (e.g. <code>https://instagram.com/username</code>), then Save and Search again.
              </p>
            ) : (
              <p style={{ marginTop: "8px", fontSize: "14px", color: "#666" }}>
                Check the platform results above for details. Add valid profile URLs in Setup if needed.
              </p>
            )}
          </div>
          <div className="button-group">
            <button onClick={onBack} className="secondary-button">Back</button>
          </div>
        </>
      ) : (
        <>
          <div className="preview-header">
            <h2>Search Results</h2>
            <div className="item-count">Total items found: {items.length}</div>
          </div>

          <div className="items-list">
            {items.map((item) => {
              const itemKey = item.item_id || item.queue_id;
              const transcriptStatus = item.transcript_status || "missing";
              const platform = item.platform || item.metadata?.platform;
              const publishedAt = item.published_at;
              const matched = item.metadata?.matched_time_filter;
              return (
                <div key={itemKey} className="preview-item">
                  <div className="item-badge">
                    {transcriptStatus === "present" ? "✓ Reel" : "Reel"}
                    {platform && <span className="item-platform">{platform}</span>}
                  </div>
                  <div className="item-content">
                    <h3 className="item-title">
                      {(() => {
                        try {
                          return item.source_url ? new URL(item.source_url).pathname.split("/").filter(Boolean).pop() || "Untitled" : "Untitled";
                        } catch {
                          return "Untitled";
                        }
                      })()}
                    </h3>
                    <p className="item-snippet">{item.preview || item.caption || "No preview"}</p>
                    <div className="item-meta">
                      {publishedAt && <span className="item-published">📅 {publishedAt.slice(0, 10)}</span>}
                      {matched === true && <span className="item-filter">✓ matched time filter</span>}
                    </div>
                    {transcriptStatus === "missing" && (
                      <p className="item-warning" style={{ fontSize: "12px", color: "#e65100", marginTop: "4px" }}>
                        ⚠ Transcript not available — will use caption
                      </p>
                    )}
                    {(item.source_url || item.url) && (
                      <a
                        href={item.source_url || item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="item-link"
                      >
                        View source →
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="button-group">
            <button onClick={onBack} className="secondary-button">
              Back
            </button>
            <button onClick={onContinue} className="primary-button">
              Continue to approval
            </button>
          </div>
        </>
      )}
    </div>
  );
}
